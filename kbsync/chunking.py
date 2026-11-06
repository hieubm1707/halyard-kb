"""Chunking settings, chunk estimates, and per-chunk citation anchors.

Gemini File Search's white-space chunker counts whitespace-separated words, not model
tokens, and knows nothing about the document. An "Article URL:" line that only sits at the
top of a file would reach the first chunk alone, and answers built from later chunks would
have no URL to cite. Repeating the line often enough puts a whole copy in every chunk.
"""

import math

# Comfortably under the documented 512-word ceiling for one chunk.
MAX_CHUNK_WORDS = 400
CHUNK_OVERLAP_WORDS = 60


def count_words(text: str) -> int:
    return len(text.split())


def estimate_chunks(word_count: int) -> int:
    """Chunks the white-space strategy produces. The API does not report a chunk count."""
    if word_count <= MAX_CHUNK_WORDS:
        return 1
    stride = MAX_CHUNK_WORDS - CHUNK_OVERLAP_WORDS
    return 1 + math.ceil((word_count - MAX_CHUNK_WORDS) / stride)


def _split_units(markdown: str) -> list[str]:
    """Split into lines, keeping each fenced code block whole so no anchor lands inside."""
    units: list[str] = []
    fence: list[str] | None = None
    for line in markdown.split("\n"):
        if line.lstrip().startswith("```"):
            if fence is None:
                fence = [line]
            else:
                fence.append(line)
                units.append("\n".join(fence))
                fence = None
        elif fence is not None:
            fence.append(line)
        else:
            units.append(line)
    if fence is not None:
        units.append("\n".join(fence))
    return units


def _split_long_line(line: str, limit: int) -> list[str]:
    """Break a line over the limit at spaces. Fenced code is never passed here, and URLs
    contain no spaces, so links stay intact."""
    words = line.split(" ")
    pieces: list[str] = []
    current: list[str] = []
    for word in words:
        if current and count_words(" ".join(current + [word])) > limit:
            pieces.append(" ".join(current))
            current = []
        current.append(word)
    pieces.append(" ".join(current))
    return pieces


def with_citation_anchors(markdown: str, url: str) -> str:
    """Return the upload version of an article, with its URL line repeated throughout."""
    anchor = f"Article URL: {url}"
    # A chunk can start just past one anchor's first word, losing that copy, so it must
    # still fit the whole next one: words between anchors <= chunk size - 2 anchors.
    limit = MAX_CHUNK_WORDS - 2 * count_words(anchor) - 4

    units: list[str] = []
    for unit in _split_units(markdown):
        is_code = unit.lstrip().startswith("```")
        too_long = count_words(unit) > limit
        units += _split_long_line(unit, limit) if too_long and not is_code else [unit]

    output: list[str] = []
    since_anchor = 0
    for unit in units:
        words = count_words(unit)
        if unit.strip() == anchor:
            since_anchor = 0
        else:
            if since_anchor + words > limit:
                output += ["", anchor, ""]
                since_anchor = 0
            since_anchor += words
        output.append(unit)
    # The last chunk can be far shorter than the limit, so it needs its own copy.
    if since_anchor:
        output += ["", anchor]
    return "\n".join(output) + "\n"
