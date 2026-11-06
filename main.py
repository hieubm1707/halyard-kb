"""Scrape help-center articles and sync the changes into a Gemini File Search store."""

import argparse
import logging
import os
from pathlib import Path
import sys

from kbsync.chunking import count_words, estimate_chunks, with_citation_anchors
from kbsync.converter import to_markdown
from kbsync.scraper import DEFAULT_BASE_URL, fetch_articles
from kbsync.sync import (
    Document,
    apply_plan,
    build_plan,
    list_remote,
    resolve_store,
)

log = logging.getLogger("kbsync")


def scrape(output_dir: Path, max_articles: int | None) -> list[Document]:
    """Save clean Markdown to disk and return the versions to upload."""
    base_url = os.getenv("HELP_CENTER_URL", DEFAULT_BASE_URL)
    articles = fetch_articles(base_url, max_articles=max_articles)
    output_dir.mkdir(parents=True, exist_ok=True)

    documents = []
    for article in articles:
        markdown = to_markdown(article)
        filename = f"{article.slug}.md"
        (output_dir / filename).write_text(markdown, encoding="utf-8")
        documents.append(
            Document(article.id, filename, with_citation_anchors(markdown, article.url))
        )
    # Keep the folder a mirror of the help center: drop articles that were unpublished.
    current = {document.filename for document in documents}
    for stale in output_dir.glob("*.md"):
        if stale.name not in current and max_articles is None:
            stale.unlink()
    log.info("Scraped %d articles into %s", len(documents), output_dir)
    return documents


def run(dry_run: bool) -> int:
    max_articles = int(os.environ["MAX_ARTICLES"]) if os.getenv("MAX_ARTICLES") else None
    documents = scrape(Path(os.getenv("OUTPUT_DIR", "articles")), max_articles)

    if dry_run:
        chunks = sum(estimate_chunks(count_words(d.content)) for d in documents)
        log.info("Dry run: files=%d estimated_chunks=%d", len(documents), chunks)
        return 0

    api_key = os.getenv("API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        log.error("API_KEY is not set (use --dry-run to scrape without uploading)")
        return 2

    from google import genai

    client = genai.Client(api_key=api_key)
    store_name = resolve_store(
        client,
        os.getenv("FILE_SEARCH_STORE"),
        os.getenv("FILE_SEARCH_STORE_DISPLAY_NAME", "support-kb"),
    )
    log.info("File Search store: %s", store_name)

    # A capped scrape cannot tell a removed article from one past the cap.
    plan = build_plan(
        documents, list_remote(client, store_name), remove_missing=max_articles is None
    )
    result = apply_plan(client, store_name, plan)

    log.info(
        "Done: added=%d updated=%d skipped=%d removed=%d failed=%d",
        result.added,
        result.updated,
        result.skipped,
        result.removed,
        result.failed,
    )
    log.info(
        "Embedded this run: files=%d chunks~%d | store total: files=%d chunks~%d",
        result.added + result.updated,
        result.chunks_embedded,
        result.added + result.updated + result.skipped,
        result.chunks_total,
    )
    return 1 if result.failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="scrape and convert only, without uploading"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout
    )
    # The HTTP clients log every request at INFO, which is noise in the job log.
    for noisy in ("httpx", "httpx2", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return run(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
