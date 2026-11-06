"""Convert Zendesk article HTML into clean Markdown."""

import re

from bs4 import BeautifulSoup, NavigableString
from markdownify import MarkdownConverter

from .scraper import Article

REMOVED_TAGS = ("script", "style", "noscript", "nav", "aside", "form", "button", "hr")
HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
KNOWN_TAGS = frozenset(
    """a abbr b big blockquote body br caption center cite code col colgroup dd del details
    div dl dt em figcaption figure font h1 h2 h3 h4 h5 h6 html i iframe img ins kbd li mark
    ol p pre q s samp small span strike strong sub summary sup table tbody td tfoot th thead
    tr tt u ul var video source""".split()
)
EMPTY_BOLD = re.compile(r"(?<!\*)\*{4}(?!\*)")
CHAR_FIXES ={**dict.fromkeys(map(ord, "​‌‍⁠﻿")), 0xA0: " "}


def _absolute(src: str) -> str:
    return f"https:{src}" if src.startswith("//") else src


def _clean_html(body_html: str) -> BeautifulSoup:
    soup = BeautifulSoup(body_html, "html.parser")

    for tag in soup(REMOVED_TAGS):
        tag.decompose()

    # Unescaped placeholders such as `<your-api-key>` parse as tags; keep them as text.
    for tag in soup.find_all(True):
        if tag.name not in KNOWN_TAGS:
            tag.insert_before(NavigableString(f"<{tag.name}>"))
            tag.unwrap()

    # Embedded videos would otherwise disappear, since Markdown has no iframe.
    for media in soup(["iframe", "video"]):
        src = media.get("src") or (media.source.get("src") if media.source else None)
        if src:
            link = soup.new_tag("a", href=_absolute(src))
            link.string = media.get("title") or "Embedded video"
            paragraph = soup.new_tag("p")
            paragraph.append(link)
            media.replace_with(paragraph)
        else:
            media.decompose()

    # Inline base64 images add tens of thousands of tokens and no searchable text.
    for image in soup("img"):
        if image.get("src", "").startswith("data:"):
            image.decompose()

    for heading in soup(HEADINGS):
        if not heading.get_text(strip=True):
            if heading.find("img"):
                heading.name = "p"
            else:
                heading.decompose()

    return soup


def _tidy(markdown: str) -> str:
    """Normalize whitespace, keeping blank lines and indentation inside code fences."""
    lines: list[str] = []
    in_fence = False
    for line in markdown.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        if in_fence:
            lines.append(line.rstrip())
            continue
        # Adjacent or nested <strong> tags render as a stray "****".
        stripped = EMPTY_BOLD.sub("", line).rstrip()
        hard_break = bool(stripped) and line.endswith("  ")
        if not stripped and lines and not lines[-1]:
            continue
        lines.append(stripped + ("  " if hard_break else ""))
    return "\n".join(lines).strip()


def to_markdown(article: Article) -> str:
    """Render an article as Markdown, led by its title and citable source URL."""
    converter = MarkdownConverter(
        heading_style="ATX",
        bullets="-",
        escape_asterisks=False,
        escape_underscores=False,
        keep_inline_images_in=["td", "th"],
    )
    body = converter.convert_soup(_clean_html(article.body_html))
    body = _tidy(body.translate(CHAR_FIXES))
    return f"# {article.title}\n\nArticle URL: {article.url}\n\n{body}\n"
