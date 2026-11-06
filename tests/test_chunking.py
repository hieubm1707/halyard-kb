import pytest

from kbsync.chunking import (
    CHUNK_OVERLAP_WORDS,
    MAX_CHUNK_WORDS,
    estimate_chunks,
    with_citation_anchors,
)

URL = "https://help.example.com/hc/en-us/articles/42-Add-A-Video"
ANCHOR = f"Article URL: {URL}"


def chunks_of(text: str) -> list[str]:
    """Mirror the white-space chunker: fixed windows of words with overlap."""
    words = text.split()
    stride = MAX_CHUNK_WORDS - CHUNK_OVERLAP_WORDS
    windows = []
    start = 0
    while True:
        windows.append(" ".join(words[start : start + MAX_CHUNK_WORDS]))
        if start + MAX_CHUNK_WORDS >= len(words):
            return windows
        start += stride


def article(body: str) -> str:
    return f"# Title\n\n{ANCHOR}\n\n{body}\n"


@pytest.mark.parametrize(
    "body",
    [
        "Short body.",
        "\n\n".join(f"Paragraph {i} " + "words " * 60 for i in range(60)),
        "| " + "long table cell " * 1500 + "|",
        "\n".join(f"- item {i} " + "text " * 30 for i in range(200)),
    ],
    ids=["short", "paragraphs", "one-giant-line", "long-list"],
)
def test_every_chunk_contains_a_whole_citation_line(body):
    for chunk in chunks_of(with_citation_anchors(article(body), URL)):
        assert ANCHOR in chunk


def test_anchor_never_lands_inside_a_code_block():
    code = "```\n" + "\n".join(f"line {i} " + "x " * 40 for i in range(20)) + "\n```"
    body = "\n\n".join(["text " * 150, code, "text " * 150])
    uploaded = with_citation_anchors(article(body), URL)
    assert code in uploaded


def test_short_article_gets_no_extra_anchor():
    assert with_citation_anchors(article("Short."), URL).count(ANCHOR) == 2


@pytest.mark.parametrize(
    "words, expected",
    [(1, 1), (400, 1), (401, 2), (740, 2), (741, 3)],
)
def test_estimate_chunks(words, expected):
    assert estimate_chunks(words) == expected
