from kbsync.converter import to_markdown
from kbsync.scraper import Article

URL = "https://help.example.com/hc/en-us/articles/42-Add-A-Video"


def convert(body_html: str) -> str:
    return to_markdown(Article("42", "Add a Video", URL, body_html))


def test_leads_with_title_and_citable_url():
    assert convert("<p>Hi</p>").startswith(f"# Add a Video\n\nArticle URL: {URL}\n\nHi")


def test_slug_comes_from_url_and_is_filesystem_safe():
    assert Article("42", "x", URL, "").slug == "42-add-a-video"
    unsafe = Article("42", "x", "https://h.example/hc/articles/42_Add%2F..?x=1", "")
    assert unsafe.slug == "42-add-2f-x-1"
    assert Article("42", "x", "https://h.example/???", "").slug == "42"


def test_preserves_headings_relative_links_and_code_blocks():
    markdown = convert(
        '<h2>Steps</h2><p><a href="/hc/articles/1">Docs</a> <a href="#steps">Jump</a></p>'
        "<pre><code>line 1\n\n    line 2</code></pre>"
    )
    assert "## Steps" in markdown
    assert "[Docs](/hc/articles/1)" in markdown
    assert "[Jump](#steps)" in markdown
    assert "line 1\n\n    line 2" in markdown


def test_removes_page_chrome():
    markdown = convert(
        "<nav>Menu</nav><script>track()</script><style>p{}</style><p>Keep</p><hr>"
    )
    assert "Keep" in markdown
    for noise in ("Menu", "track", "p{}", "---"):
        assert noise not in markdown


def test_keeps_embedded_video_as_a_link():
    markdown = convert('<iframe src="//www.youtube.com/embed/abc"></iframe>')
    assert "[Embedded video](https://www.youtube.com/embed/abc)" in markdown


def test_drops_base64_images_but_keeps_hosted_ones():
    markdown = convert(
        '<img src="data:image/png;base64,AAAA" alt="a"><img src="/img/b.png" alt="b">'
    )
    assert "base64" not in markdown
    assert "![b](/img/b.png)" in markdown


def test_keeps_unescaped_placeholder_as_text():
    assert "<your-api-key>" in convert("<p>Set <your-api-key> first</p>")


def test_removes_empty_headings_and_stray_bold_markers():
    markdown = convert("<h4>&nbsp;</h4><p><strong>A</strong><strong>B</strong></p>")
    assert "####" not in markdown
    assert "****" not in markdown
    assert "**AB**" in markdown


def test_normalizes_invisible_characters_and_blank_runs():
    markdown = convert("<p>a​b c</p><p>&nbsp;</p><p>&nbsp;</p><p>d</p>")
    assert "ab c" in markdown
    assert "\n\n\n" not in markdown
