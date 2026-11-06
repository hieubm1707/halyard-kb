from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from kbsync import sync
from kbsync.sync import Document, apply_plan, build_plan, list_remote

EPOCH = datetime(2026, 1, 1)


def doc(article_id: str, content: str = "text") -> Document:
    return Document(article_id, f"{article_id}.md", content)


def remote_doc(name, article_id, content_hash, state="STATE_ACTIVE", age=0):
    metadata = (
        None
        if article_id is None
        else [
            SimpleNamespace(key="article_id", string_value=article_id),
            SimpleNamespace(key="content_hash", string_value=content_hash),
        ]
    )
    return SimpleNamespace(
        name=name,
        state=SimpleNamespace(value=state),
        custom_metadata=metadata,
        create_time=EPOCH - timedelta(minutes=age),
    )


class FakeGenAI:
    """In-memory stand-in for the parts of the google-genai client the sync uses."""

    def __init__(self, fail_for: str | None = None):
        self.fail_for = fail_for
        self.events: list[tuple[str, str]] = []
        self.store: list[SimpleNamespace] = []
        self._next = 0
        self.operations = SimpleNamespace(get=lambda operation: operation)
        self.file_search_stores = SimpleNamespace(
            upload_to_file_search_store=self._upload,
            documents=SimpleNamespace(list=self._list, delete=self._delete),
        )

    def _upload(self, file_search_store_name, file, config):
        self._next += 1
        name = f"doc-{self._next}"
        self.events.append(("upload", config["display_name"]))
        metadata = {m["key"]: m["string_value"] for m in config["custom_metadata"]}
        failed = config["display_name"] == self.fail_for
        self.store.append(
            remote_doc(
                name,
                metadata["article_id"],
                metadata["content_hash"],
                state="STATE_FAILED" if failed else "STATE_ACTIVE",
                age=-self._next,
            )
        )
        return SimpleNamespace(done=True, error={"message": "boom"} if failed else None)

    def _list(self, parent):
        return list(self.store)

    def _delete(self, name, config):
        assert config == {"force": True}
        self.events.append(("delete", name))
        self.store = [item for item in self.store if item.name != name]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(sync, "POLL_SECONDS", 0)


def test_plan_classifies_new_changed_unchanged_and_missing():
    unchanged, changed, new = doc("1"), doc("2", "v2"), doc("3")
    remote = {
        "1": SimpleNamespace(article_id="1", content_hash=unchanged.content_hash),
        "2": SimpleNamespace(article_id="2", content_hash="old"),
        "4": SimpleNamespace(article_id="4", content_hash="gone"),
    }
    plan = build_plan([unchanged, changed, new], remote, remove_missing=True)
    assert plan.added == [new]
    assert [document for document, _ in plan.updated] == [changed]
    assert plan.skipped == [unchanged]
    assert [item.article_id for item in plan.removed] == ["4"]


def test_capped_scrape_never_removes_articles():
    remote = {"4": SimpleNamespace(article_id="4", content_hash="x")}
    assert build_plan([doc("1")], remote, remove_missing=False).removed == []


def test_second_run_uploads_nothing():
    client = FakeGenAI()
    documents = [doc("1"), doc("2")]

    first = apply_plan(client, "store", build_plan(documents, {}, True))
    assert (first.added, first.skipped) == (2, 0)

    events = len(client.events)
    remote = list_remote(client, "store")
    second = apply_plan(client, "store", build_plan(documents, remote, True))
    assert (second.added, second.updated, second.skipped) == (0, 0, 2)
    assert len(client.events) == events


def test_update_uploads_new_version_before_deleting_old():
    client = FakeGenAI()
    client.store.append(remote_doc("old", "1", "stale-hash", age=10))
    remote = list_remote(client, "store")
    result = apply_plan(client, "store", build_plan([doc("1")], remote, True))
    assert result.updated == 1
    assert client.events == [("upload", "1.md"), ("delete", "old")]


def test_failed_update_keeps_the_old_version_searchable():
    client = FakeGenAI(fail_for="1.md")
    client.store.append(remote_doc("old", "1", "stale-hash", age=10))
    remote = list_remote(client, "store")
    result = apply_plan(client, "store", build_plan([doc("1")], remote, True))
    assert (result.updated, result.failed) == (0, 1)
    assert ("delete", "old") not in client.events

    # The next run clears the failed copy and still finds the old one.
    assert list_remote(client, "store")["1"].name == "old"
    assert [item.name for item in client.store] == ["old"]


def test_listing_clears_duplicates_and_unfinished_documents():
    client = FakeGenAI()
    client.store += [
        remote_doc("older", "1", "a", age=5),
        remote_doc("newer", "1", "b", age=1),
        remote_doc("pending", "2", "c", state="STATE_PENDING"),
    ]
    remote = list_remote(client, "store")
    assert remote["1"].name == "newer"
    assert "2" not in remote
    assert [item.name for item in client.store] == ["newer"]


def test_listing_leaves_unrelated_documents_alone():
    client = FakeGenAI()
    client.store.append(remote_doc("mine", None, None))
    assert list_remote(client, "store") == {}
    assert client.events == []


def test_upload_sends_chunking_and_metadata():
    client = FakeGenAI()
    captured = {}
    original = client.file_search_stores.upload_to_file_search_store

    def spy(**kwargs):
        captured.update(kwargs)
        return original(**kwargs)

    client.file_search_stores.upload_to_file_search_store = spy
    apply_plan(client, "store", build_plan([doc("7")], {}, True))
    config = captured["config"]
    assert captured["file_search_store_name"] == "store"
    assert config["chunking_config"] == sync.CHUNKING_CONFIG
    assert {"key": "article_id", "string_value": "7"} in config["custom_metadata"]
