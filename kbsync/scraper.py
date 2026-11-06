"""Fetch published help-center articles through the public Zendesk API."""

from dataclasses import dataclass
import re

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_BASE_URL = "https://support.optisigns.com"
PAGE_SIZE = 100
TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    url: str
    body_html: str

    @property
    def slug(self) -> str:
        """Filesystem-safe name. Zendesk URLs end in `<id>-<title>`, so it is unique."""
        last_segment = self.url.rstrip("/").rsplit("/", 1)[-1]
        slug = re.sub(r"[^a-z0-9-]+", "-", last_segment.lower()).strip("-")
        return slug or self.id


def _session() -> requests.Session:
    retry = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.headers["User-Agent"] = "kb-sync/1.0"
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def fetch_articles(
    base_url: str = DEFAULT_BASE_URL,
    locale: str = "en-us",
    max_articles: int | None = None,
) -> list[Article]:
    """Return published articles. Any HTTP failure raises, so a partial scrape never
    reaches the sync step and cannot be mistaken for articles having been removed."""
    session = _session()
    next_url: str | None = (
        f"{base_url}/api/v2/help_center/{locale}/articles.json?per_page={PAGE_SIZE}"
    )
    articles: list[Article] = []

    while next_url:
        response = session.get(next_url, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()

        for raw in payload["articles"]:
            if raw.get("draft") or not (raw.get("body") or "").strip():
                continue
            articles.append(
                Article(
                    id=str(raw["id"]),
                    title=raw["title"].strip(),
                    url=raw["html_url"],
                    body_html=raw["body"],
                )
            )
            if max_articles is not None and len(articles) >= max_articles:
                return articles

        next_url = payload.get("next_page")

    if not articles:
        raise RuntimeError(f"No articles returned by {base_url}")
    return articles
