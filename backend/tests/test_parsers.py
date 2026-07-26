from __future__ import annotations

from pathlib import Path

from ebooklib import epub

from backend.app.parsers import parse_document

FIXTURES = Path(__file__).parent / "fixtures"


def make_epub(path: Path) -> Path:
    book = epub.EpubBook()
    book.set_identifier("parser-fixture")
    book.set_title("Parser Fixture")
    book.set_language("en")
    chapter = epub.EpubHtml(title="One", file_name="text/one.xhtml", lang="en")
    chapter.content = """
    <html xmlns="http://www.w3.org/1999/xhtml"><head><title>One</title></head>
    <body><h1>Chapter One</h1>
    <p id="linked-paragraph">Hello <a href="https://example.com">world</a>.
    <img id="inline-image" src="../images/missing.png" alt=""/></p>
    <p>Run <code>x=1</code> now.</p>
    <pre>print("skip")</pre></body></html>
    """
    book.add_item(chapter)
    book.toc = (epub.Link("text/one.xhtml", "One", "one"),)
    book.spine = ["nav", chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book)
    return path


def test_markdown_parser_preserves_protected_blocks_and_chapters() -> None:
    document = parse_document(FIXTURES / "sample.md")
    assert document.source_type == "md"
    assert [chapter.title for chapter in document.chapters] == ["Chapter One", "Chapter Two"]
    blocks = [block for chapter in document.chapters for block in chapter.blocks]
    paragraph = next(block for block in blocks if "https://example.com" in (block.text or ""))
    assert paragraph.kind == "para"
    assert paragraph.struct_path["token_index"] >= 0
    assert paragraph.struct_path["inline_source"] == paragraph.text
    code = next(block for block in blocks if block.kind == "code")
    assert code.translatable is False
    assert 'print("do not translate")' in (code.text or "")
    assert any(block.kind == "table_cell" for block in blocks)


def test_epub_parser_follows_spine_and_builds_xpath(tmp_path: Path) -> None:
    source = make_epub(tmp_path / "book.epub")
    document = parse_document(source, "epub")
    assert len(document.chapters) == 1
    assert document.chapters[0].href == "text/one.xhtml"
    blocks = document.chapters[0].blocks
    assert [block.kind for block in blocks] == ["heading", "para", "para", "code"]
    assert blocks[1].text == "Hello world."
    assert blocks[1].struct_path["xpath"].startswith("/")
    assert "x=1" not in (blocks[2].text or "")
    assert "⟦EPUBCODE0000⟧" in (blocks[2].text or "")
    assert blocks[2].struct_path["inline_code"]["⟦EPUBCODE0000⟧"] == "x=1"
    assert blocks[3].translatable is False
