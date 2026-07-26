from __future__ import annotations

from pathlib import Path

from ebooklib import ITEM_DOCUMENT, epub
from lxml import etree
from markdown_it import MarkdownIt

from backend.app.parsers import parse_document
from backend.app.segment import create_segments
from backend.app.writers import write_epub, write_markdown
from backend.tests.test_parsers import make_epub

FIXTURES = Path(__file__).parent / "fixtures"


def translated_segments(path: Path):
    segments = create_segments(parse_document(path))
    for segment in segments:
        segment.status = "done"
        if "https://example.com" in segment.source_text:
            segment.target_text = (
                "一个 *粗体* [链接](https://changed.invalid) 和 `inline_code`。"
            )
        else:
            segment.target_text = f"译文：{segment.source_text}"
    return segments


def test_markdown_bilingual_and_target_only_round_trip(tmp_path: Path) -> None:
    source = FIXTURES / "sample.md"
    segments = translated_segments(source)
    bilingual = write_markdown(source, segments, tmp_path / "bilingual.md")
    output = bilingual.read_text(encoding="utf-8")
    assert "https://example.com/path?q=1" in output
    assert "changed.invalid" not in output
    assert "`inline_code`" in output
    assert 'print("do not translate")' in output
    assert "| --- | --- |" in output
    assert "> 一个 *粗体*" in output

    target = write_markdown(
        source,
        segments,
        tmp_path / "target.md",
        mode="target_only",
    )
    target_text = target.read_text(encoding="utf-8")
    tokens = MarkdownIt("commonmark").parse(target_text)
    assert any(token.type == "fence" for token in tokens)
    assert "https://example.com/path?q=1" in target_text
    assert "| --- | --- |" in target_text


def test_markdown_setext_heading_keeps_underline_attached(tmp_path: Path) -> None:
    source = tmp_path / "setext.md"
    source.write_text("Book title\n==========\n\nBody.\n", encoding="utf-8")
    segments = create_segments(parse_document(source))
    for segment in segments:
        segment.status = "done"
        segment.target_text = f"译文：{segment.source_text}"
    output = write_markdown(source, segments, tmp_path / "setext-out.md")
    rendered = output.read_text(encoding="utf-8")
    assert rendered.index("==========") < rendered.index("> 译文：Book title")
    parsed = MarkdownIt("commonmark").parse(rendered)
    assert any(token.type == "heading_open" and token.tag == "h1" for token in parsed)


def test_markdown_multiline_containers_survive_target_only(tmp_path: Path) -> None:
    source = tmp_path / "containers.md"
    source.write_text(
        "> quoted line\n\n- list line\n",
        encoding="utf-8",
    )
    segments = create_segments(parse_document(source))
    for segment in segments:
        segment.status = "done"
        segment.target_text = "译文第一段\n\n译文第二段"
    output = write_markdown(
        source,
        segments,
        tmp_path / "containers-out.md",
        mode="target_only",
    )
    rendered = output.read_text(encoding="utf-8")
    tokens = MarkdownIt("commonmark").parse(rendered)
    assert any(token.type == "blockquote_open" for token in tokens)
    assert any(token.type == "bullet_list_open" for token in tokens)
    assert "> 译文第一段\n> \n> 译文第二段" in rendered
    assert "- 译文第一段\n  \n  译文第二段" in rendered
    assert sum(token.type == "blockquote_open" for token in tokens) == 1
    assert sum(token.type == "list_item_open" for token in tokens) == 1


def test_markdown_target_only_escapes_table_cell_content(tmp_path: Path) -> None:
    source = tmp_path / "table.md"
    source.write_text(
        "| Key | Value |\n| --- | --- |\n| x | y |\n",
        encoding="utf-8",
    )
    segments = create_segments(parse_document(source))
    for segment in segments:
        segment.status = "done"
        segment.target_text = (
            "甲 | 乙\n第二行"
            if segment.struct_path["block_kind"] == "table_cell"
            and segment.source_text == "x"
            else segment.source_text
        )
    output = write_markdown(
        source,
        segments,
        tmp_path / "table-out.md",
        mode="target_only",
    )
    rendered = output.read_text(encoding="utf-8")
    tokens = MarkdownIt("commonmark").enable("table").parse(rendered)
    cells = [token for token in tokens if token.type in {"th_open", "td_open"}]

    assert "甲 \\| 乙<br />第二行" in rendered
    assert len(cells) == 4


def test_markdown_table_escaped_pipes_keep_cell_and_inline_structure(
    tmp_path: Path,
) -> None:
    source = tmp_path / "protected-table.md"
    source.write_text(
        "| First | Second | Third |\n"
        "| --- | --- | --- |\n"
        "| Run `a\\|b` now | Run `a\\|b` now | "
        "[docs](https://example.com/a\\|b) |\n",
        encoding="utf-8",
    )
    segments = create_segments(parse_document(source))
    body = [
        segment
        for segment in segments
        if segment.source_text
        in {
            "Run `a|b` now",
            "[docs](https://example.com/a|b)",
        }
    ]
    assert len(body) == 3
    assert [segment.struct_path["table_cell_index"] for segment in body] == [0, 1, 2]
    assert [segment.struct_path["inline_raw_source"] for segment in body] == [
        "Run `a\\|b` now",
        "Run `a\\|b` now",
        "[docs](https://example.com/a\\|b)",
    ]

    # Simulate ledgers written before exact table-cell offsets were recorded.
    for segment in body:
        segment.struct_path.pop("table_cell_index")
        segment.struct_path.pop("inline_raw_source")
        segment.struct_path["inline_offset"] = -1
    for index, segment in enumerate(body):
        segment.status = "done"
        segment.target_text = (
            f"执行{index + 1} `a|b`"
            if index < 2
            else "[文档](https://changed.invalid/a|b)"
        )

    output = write_markdown(
        source,
        segments,
        tmp_path / "protected-table-out.md",
        mode="target_only",
    )
    rendered = output.read_text(encoding="utf-8")
    tokens = MarkdownIt("commonmark").enable("table").parse(rendered)
    cells = [token for token in tokens if token.type in {"th_open", "td_open"}]
    inline_children = [
        child
        for token in tokens
        if token.type == "inline"
        for child in token.children or []
    ]
    code_contents = [
        child.content for child in inline_children if child.type == "code_inline"
    ]
    link_hrefs = [
        child.attrGet("href")
        for child in inline_children
        if child.type == "link_open"
    ]

    assert len(cells) == 6
    assert code_contents == ["a|b", "a|b"]
    assert link_hrefs == ["https://example.com/a%7Cb"]
    assert "changed.invalid" not in rendered
    assert "执行1 `a\\|b`" in rendered
    assert "执行2 `a\\|b`" in rendered


def _all_xhtml_text(path: Path) -> tuple[str, list[str]]:
    book = epub.read_epub(str(path))
    texts: list[str] = []
    links: list[str] = []
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        root = etree.fromstring(
            bytes(item.get_content()),
            parser=etree.XMLParser(recover=True),
        )
        texts.append("".join(root.itertext()))
        links.extend(root.xpath("//*[local-name()='a']/@href"))
    return "\n".join(texts), links


def _xhtml_attribute_counts(path: Path) -> tuple[int, list[str]]:
    book = epub.read_epub(str(path))
    image_count = 0
    ids: list[str] = []
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        root = etree.fromstring(
            bytes(item.get_content()),
            parser=etree.XMLParser(recover=True),
        )
        image_count += len(root.xpath("//*[local-name()='img']"))
        ids.extend(root.xpath("//*[@id]/@id"))
    return image_count, ids


def _external_link_texts(path: Path) -> list[str]:
    book = epub.read_epub(str(path))
    texts: list[str] = []
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        root = etree.fromstring(
            bytes(item.get_content()),
            parser=etree.XMLParser(recover=True),
        )
        for link in root.xpath(
            "//*[local-name()='a' and @href='https://example.com']"
        ):
            texts.append("".join(link.itertext()))
    return texts


def _make_protected_only_epub(path: Path) -> Path:
    book = epub.EpubBook()
    book.set_identifier("protected-only")
    book.set_title("Protected-only fixture")
    book.set_language("en")
    chapter = epub.EpubHtml(title="One", file_name="text/one.xhtml", lang="en")
    chapter.content = """
    <html xmlns="http://www.w3.org/1999/xhtml"><head><title>One</title></head>
    <body>
      <p id="code-only"><code>x=1</code></p>
      <p id="nonvisible"><script>window.secret = 1;</script><style>.x { color: red; }</style></p>
      <p id="mixed">Before <code>y=2</code> after.</p>
    </body></html>
    """
    book.add_item(chapter)
    book.toc = (epub.Link("text/one.xhtml", "One", "one"),)
    book.spine = ["nav", chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book)
    return path


def test_epub_skips_protected_only_blocks_and_uses_target_language(
    tmp_path: Path,
) -> None:
    source = _make_protected_only_epub(tmp_path / "protected-only.epub")
    document = parse_document(source)
    paragraphs = [
        block
        for chapter in document.chapters
        for block in chapter.blocks
        if block.kind == "para"
    ]
    code_only = next(block for block in paragraphs if "EPUBCODE" in (block.text or ""))
    nonvisible = next(block for block in paragraphs if block.text == "")
    mixed = next(block for block in paragraphs if "Before" in (block.text or ""))

    assert code_only.translatable is False
    assert code_only.struct_path["inline_slots"] == []
    assert nonvisible.translatable is False
    assert nonvisible.struct_path["inline_slots"] == []
    assert mixed.translatable is True

    segments = create_segments(document)
    assert len(segments) == 1
    segments[0].status = "done"
    segments[0].target_text = "之前之后。"
    output = write_epub(
        source,
        segments,
        tmp_path / "protected-only-out.epub",
        target_lang="ja",
    )
    book = epub.read_epub(str(output))
    roots = [
        etree.fromstring(
            bytes(item.get_content()),
            parser=etree.XMLParser(recover=True),
        )
        for item in book.get_items_of_type(ITEM_DOCUMENT)
    ]
    chapter_root = next(root for root in roots if root.xpath("//*[@id='mixed']"))
    translations = chapter_root.xpath(
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' translation ')]"
    )

    assert len(chapter_root.xpath("//*[@id='code-only']")) == 1
    assert len(chapter_root.xpath("//*[local-name()='code' and text()='x=1']")) == 1
    assert len(chapter_root.xpath("//*[local-name()='script']")) == 1
    original_styles = chapter_root.xpath(
        "//*[local-name()='style' and not(@data-translation-workbench)]"
    )
    assert len(original_styles) == 1
    assert len(translations) == 1
    assert translations[0].get("lang") == "ja"


def test_epub_bilingual_and_target_only_remain_parseable(tmp_path: Path) -> None:
    source = make_epub(tmp_path / "source.epub")
    segments = create_segments(parse_document(source))
    for segment in segments:
        segment.status = "done"
        segment.target_text = f"译文：{segment.source_text}"
        if segment.source_text == "Hello world.":
            segment.target_text = "你好，世界。"
            segment.struct_path["target_slots"] = ["你好，", "世界", "。"]
            segment.struct_path["target_slots_text"] = segment.target_text

    bilingual = write_epub(source, segments, tmp_path / "bilingual.epub")
    text, links = _all_xhtml_text(bilingual)
    assert "Hello world." in text
    assert "你好，世界。" in text
    assert "x=1" in text
    assert "EPUBCODE" not in text
    assert "https://example.com" in links
    image_count, ids = _xhtml_attribute_counts(bilingual)
    assert image_count == 1
    assert ids.count("inline-image") == 1
    assert ids.count("linked-paragraph") == 1
    assert _external_link_texts(bilingual) == ["world", "世界"]

    target = write_epub(
        source,
        segments,
        tmp_path / "target.epub",
        mode="target_only",
    )
    target_text, target_links = _all_xhtml_text(target)
    assert "你好，世界。" in target_text
    assert "x=1" in target_text
    assert "EPUBCODE" not in target_text
    assert "https://example.com" in target_links
    target_image_count, target_ids = _xhtml_attribute_counts(target)
    assert target_image_count == 1
    assert target_ids.count("inline-image") == 1
    assert _external_link_texts(target) == ["世界"]
