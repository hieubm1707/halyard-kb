"""Sync articles into a Gemini File Search store, uploading only what changed.

State lives on the store itself (article id and content hash as document metadata), so
the job keeps no local state and works from a fresh container on every run.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import hashlib
import io
import logging
import time

from .chunking import (
    CHUNK_OVERLAP_WORDS,
    MAX_CHUNK_WORDS,
    count_words,
    estimate_chunks,
)

log = logging.getLogger(__name__)

UPLOAD_WORKERS = 4
POLL_SECONDS = 2
UPLOAD_TIMEOUT_SECONDS = 300
CHUNKING_CONFIG = {
    "white_space_config": {
        "max_tokens_per_chunk": MAX_CHUNK_WORDS,
        "max_overlap_tokens": CHUNK_OVERLAP_WORDS,
    }
}


@dataclass(frozen=True)
class Document:
    article_id: str
    filename: str
    content: str

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode()).hexdigest()


@dataclass(frozen=True)
class RemoteFile:
    name: str
    article_id: str
    content_hash: str


@dataclass
class Plan:
    added: list[Document] = field(default_factory=list)
    updated: list[tuple[Document, RemoteFile]] = field(default_factory=list)
    skipped: list[Document] = field(default_factory=list)
    removed: list[RemoteFile] = field(default_factory=list)


@dataclass
class Result:
    added: int = 0
    updated: int = 0
    skipped: int = 0
    removed: int = 0
    failed: int = 0
    chunks_embedded: int = 0
    chunks_total: int = 0


def build_plan(
    documents: list[Document], remote: dict[str, RemoteFile], remove_missing: bool
) -> Plan:
    plan = Plan()
    for document in documents:
        current = remote.get(document.article_id)
        if current is None:
            plan.added.append(document)
        elif current.content_hash != document.content_hash:
            plan.updated.append((document, current))
        else:
            plan.skipped.append(document)
    if remove_missing:
        seen = {document.article_id for document in documents}
        plan.removed = [f for f in remote.values() if f.article_id not in seen]
    return plan


def resolve_store(client, store_name: str | None, display_name: str) -> str:
    if store_name:
        return store_name
    for store in client.file_search_stores.list():
        if store.display_name == display_name:
            return store.name
    return client.file_search_stores.create(config={"display_name": display_name}).name


def _delete(client, document_name: str) -> None:
    # force also deletes the document's chunks, which a plain delete refuses to do.
    client.file_search_stores.documents.delete(name=document_name, config={"force": True})


def _metadata(document) -> dict[str, str]:
    return {item.key: item.string_value for item in document.custom_metadata or []}


def list_remote(client, store_name: str) -> dict[str, RemoteFile]:
    """Map article id to its live document, clearing anything an interrupted run left
    behind: documents that failed or never finished indexing, and older duplicates."""
    documents = sorted(
        client.file_search_stores.documents.list(parent=store_name),
        key=lambda document: document.create_time,
        reverse=True,
    )
    remote: dict[str, RemoteFile] = {}
    # Newest first, so the first document seen for an article is the one to keep.
    for document in documents:
        metadata = _metadata(document)
        article_id = metadata.get("article_id")
        if not article_id:
            continue
        if document.state.value != "STATE_ACTIVE" or article_id in remote:
            log.warning("Clearing stale document %s (article %s)", document.name, article_id)
            _delete(client, document.name)
            continue
        remote[article_id] = RemoteFile(
            document.name, article_id, metadata.get("content_hash", "")
        )
    return remote


def _upload(client, store_name: str, document: Document) -> None:
    operation = client.file_search_stores.upload_to_file_search_store(
        file_search_store_name=store_name,
        file=io.BytesIO(document.content.encode()),
        config={
            "display_name": document.filename,
            "mime_type": "text/plain",
            "custom_metadata": [
                {"key": "article_id", "string_value": document.article_id},
                {"key": "content_hash", "string_value": document.content_hash},
            ],
            "chunking_config": CHUNKING_CONFIG,
        },
    )
    deadline = time.monotonic() + UPLOAD_TIMEOUT_SECONDS
    while not operation.done:
        if time.monotonic() > deadline:
            raise TimeoutError(f"Indexing timed out for {document.filename}")
        time.sleep(POLL_SECONDS)
        operation = client.operations.get(operation)
    # A failed document left in the store is cleared at the start of the next run.
    if operation.error:
        raise RuntimeError(f"Indexing failed for {document.filename}: {operation.error}")


def _chunks(document: Document) -> int:
    return estimate_chunks(count_words(document.content))


def apply_plan(client, store_name: str, plan: Plan) -> Result:
    result = Result(skipped=len(plan.skipped))

    def add(document: Document) -> Document:
        _upload(client, store_name, document)
        return document

    def update(pair: tuple[Document, RemoteFile]) -> Document:
        document, old = pair
        # Upload before deleting, so the article stays searchable if the upload fails.
        _upload(client, store_name, document)
        _delete(client, old.name)
        return document

    def remove(old: RemoteFile) -> None:
        _delete(client, old.name)

    with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as pool:
        tasks = (
            [("added", pool.submit(add, item)) for item in plan.added]
            + [("updated", pool.submit(update, item)) for item in plan.updated]
            + [("removed", pool.submit(remove, item)) for item in plan.removed]
        )

    for outcome, task in tasks:
        error = task.exception()
        if error:
            result.failed += 1
            log.error("%s failed: %s", outcome, error)
            continue
        setattr(result, outcome, getattr(result, outcome) + 1)
        document = task.result()
        if document:
            result.chunks_embedded += _chunks(document)

    embedded = plan.added + [document for document, _ in plan.updated] + plan.skipped
    result.chunks_total = sum(_chunks(document) for document in embedded)
    return result
