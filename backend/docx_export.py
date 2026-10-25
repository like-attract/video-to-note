"""把笔记 Markdown 转成 Word (.docx)。

导出入口在 ``main.download_summary_markdown``（``?format=docx``）。这里只做
Markdown → Word 的有损但足够用的转换：标题层级、列表、加粗/斜体/行内代码、
引用、表格、代码块与截图。公式保持 ``$...$`` 原文，Word 自身不解析 KaTeX。

截图路径只认任务目录内的相对引用与 ``/api/image/...``；外部 URL 不联网抓取，
避免导出时把本机网络行为带进产物。
"""
from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from urllib.parse import unquote

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

MAX_IMAGE_WIDTH = Inches(6.1)
MAX_IMAGE_HEIGHT = Inches(8.2)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_QUOTE_RE = re.compile(r"^>\s?(.*)$")
_HR_RE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
_INLINE_RE = re.compile(
    r"(\$\$?[^$\n]+\$\$?)"          # 公式整段保留，避免 $a*b$ 被当成斜体
    r"|!\[([^\]]*)\]\(([^)]+)\)"
    r"|\[([^\]]+)\]\(([^)]+)\)"
    r"|\*\*(.+?)\*\*"
    r"|__(.+?)__"
    r"|`([^`]+)`"
    r"|~~(.+?)~~"
    r"|\*(.+?)\*"
    r"|_(.+?)_",
    re.S,
)


def markdown_to_docx(markdown: str, task_dir: Path, title: str = "") -> bytes:
    document = Document()
    _configure_styles(document)
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.strip().startswith("```"):
            index = _add_code_block(document, lines, index)
            continue
        table_end = _add_table(document, lines, index, task_dir)
        if table_end is not None:
            index = table_end
            continue
        if _HR_RE.match(line):
            _add_horizontal_rule(document)
            index += 1
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            level = min(4, len(heading.group(1)))
            paragraph = document.add_heading(level=level)
            _add_inline(paragraph, heading.group(2), task_dir)
            index += 1
            continue
        quote = _QUOTE_RE.match(line)
        if quote:
            paragraph = document.add_paragraph(style="Quote")
            _add_inline(paragraph, quote.group(1), task_dir)
            index += 1
            continue
        unordered = _UL_RE.match(line)
        ordered = _OL_RE.match(line)
        if unordered or ordered:
            match = unordered or ordered
            text = match.group(2)
            level = len(match.group(1).replace("\t", "  ")) // 2
            style = "List Bullet" if unordered else "List Number"
            try:
                paragraph = document.add_paragraph(style=style)
            except KeyError:
                paragraph = document.add_paragraph()
            if level:
                paragraph.paragraph_format.left_indent = Inches(0.25 + 0.25 * level)
            _add_inline(paragraph, text, task_dir)
            index += 1
            continue
        if line.strip():
            paragraph = document.add_paragraph()
            _add_inline(paragraph, line, task_dir)
        elif document.paragraphs:
            document.add_paragraph()
        index += 1

    if title and not document.paragraphs:
        document.add_heading(title, level=1)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _configure_styles(document: Document) -> None:
    styles = document.styles
    normal = styles["Normal"]
    _set_east_asia_font(normal, "微软雅黑")
    normal.font.size = Pt(10.5)
    for name, size in (
        ("Title", 20),
        ("Heading 1", 16),
        ("Heading 2", 13.5),
        ("Heading 3", 11.5),
        ("Heading 4", 10.5),
    ):
        try:
            style = styles[name]
        except KeyError:
            continue
        _set_east_asia_font(style, "微软雅黑")
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor(0x1F, 0x1F, 0x1F)
        style.font.bold = True


def _set_east_asia_font(style, family: str) -> None:
    style.font.name = family
    rpr = style.element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    fonts.set(qn("w:eastAsia"), family)


def _add_code_block(document: Document, lines: list[str], start: int) -> int:
    """围栏代码块：整段等宽 + 浅灰底；返回围栏结束后的下一行。"""
    index = start + 1
    code_lines: list[str] = []
    while index < len(lines) and not lines[index].strip().startswith("```"):
        code_lines.append(lines[index])
        index += 1
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.left_indent = Inches(0.15)
    ppr = paragraph._p.get_or_add_pPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), "F2F2F2")
    ppr.append(shading)
    run = paragraph.add_run()
    run.font.name = "Consolas"
    run.font.size = Pt(9)
    for line_index, code_line in enumerate(code_lines):
        if line_index:
            run.add_break()
        run.add_text(code_line)
    return index + 1


def _add_table(
    document: Document, lines: list[str], start: int, task_dir: Path
) -> int | None:
    """识别 GFM 管道表格；识别成功时返回表格之后的行号。"""
    header = lines[start]
    if "|" not in header or start + 1 >= len(lines):
        return None
    if not _TABLE_SEPARATOR_RE.match(lines[start + 1]):
        return None
    rows: list[list[str]] = [_split_table_row(header)]
    index = start + 2
    while index < len(lines) and "|" in lines[index] and lines[index].strip():
        rows.append(_split_table_row(lines[index]))
        index += 1
    columns = max(len(row) for row in rows)
    table = document.add_table(rows=len(rows), cols=columns)
    try:
        table.style = "Table Grid"
    except KeyError:
        pass
    for row_index, row in enumerate(rows):
        for column in range(columns):
            cell = table.cell(row_index, column)
            cell.text = ""
            paragraph = cell.paragraphs[0]
            text = row[column] if column < len(row) else ""
            _add_inline(paragraph, text, task_dir, bold_all=row_index == 0)
    return index


def _split_table_row(line: str) -> list[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    return [cell.strip() for cell in value.split("|")]


def _add_horizontal_rule(document: Document) -> None:
    paragraph = document.add_paragraph()
    ppr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "BFBFBF")
    borders.append(bottom)
    ppr.append(borders)


def _add_inline(
    paragraph, text: str, task_dir: Path, bold_all: bool = False
) -> None:
    position = 0
    for match in _INLINE_RE.finditer(text):
        if match.start() > position:
            _add_run(paragraph, text[position : match.start()], bold=bold_all)
        groups = match.groups()
        if groups[0]:  # 公式
            _add_run(paragraph, groups[0], bold=bold_all)
        elif groups[1] is not None and groups[2] is not None:  # 图片
            _add_image(paragraph, groups[1], groups[2], task_dir)
        elif groups[3] is not None and groups[4] is not None:  # 链接
            _add_run(paragraph, groups[3], bold=bold_all)
        elif groups[5] is not None:
            _add_run(paragraph, groups[5], bold=True)
        elif groups[6] is not None:
            _add_run(paragraph, groups[6], bold=True)
        elif groups[7] is not None:
            _add_run(paragraph, groups[7], font="Consolas", size=9)
        elif groups[8] is not None:
            _add_run(paragraph, groups[8], strike=True)
        elif groups[9] is not None:
            _add_run(paragraph, groups[9], italic=True)
        elif groups[10] is not None:
            _add_run(paragraph, groups[10], italic=True)
        else:
            _add_run(paragraph, match.group(0), bold=bold_all)
        position = match.end()
    if position < len(text):
        _add_run(paragraph, text[position:], bold=bold_all)


def _add_run(
    paragraph,
    text: str,
    *,
    bold: bool = False,
    italic: bool = False,
    strike: bool = False,
    font: str | None = None,
    size: float | None = None,
):
    run = paragraph.add_run(text)
    run.bold = bold or None
    run.italic = italic or None
    if strike:
        run.font.strike = True
    if font:
        run.font.name = font
    if size:
        run.font.size = Pt(size)
    return run


def _add_image(paragraph, alt: str, target: str, task_dir: Path) -> None:
    image = _resolve_image(target, task_dir)
    if image is None:
        _add_run(paragraph, f"[图片：{alt or target}]", italic=True)
        return
    try:
        stream, width = image
        run = paragraph.add_run()
        if width is not None:
            run.add_picture(stream, width=width)
        else:
            run.add_picture(stream)
    except Exception:
        _add_run(paragraph, f"[图片无法嵌入：{alt or target}]", italic=True)


def _resolve_image(target: str, task_dir: Path):
    """返回 (BinaryIO, width) 或 None；只允许任务目录内的截图与 data URI。"""
    value = unquote(target.strip().strip("<>"))
    if value.startswith("data:image/"):
        try:
            header, payload = value.split(",", 1)
            raw = base64.b64decode(payload)
        except (ValueError, base64.binascii.Error):
            return None
        return _sized_image(io.BytesIO(raw))
    if value.startswith(("http://", "https://")):
        return None
    if value.startswith("/api/image/"):
        value = "frames/" + value.rsplit("/", 1)[-1]
    value = value.replace("\\", "/")
    if value.startswith("./"):
        value = value[2:]
    path = (task_dir / value).resolve()
    try:
        path.relative_to(task_dir.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    return _sized_image(path)


def _sized_image(source):
    try:
        from PIL import Image

        with Image.open(source) as image:
            width, height = image.size
        if hasattr(source, "seek"):
            source.seek(0)
        scale = min(
            float(MAX_IMAGE_WIDTH) / max(1, width),
            float(MAX_IMAGE_HEIGHT) / max(1, height),
            1.0,
        )
        # python-docx 1.2 的 Image.from_file 只认 str 或 file-like，不认 Path
        return (source if hasattr(source, "seek") else str(source)), int(width * scale)
    except Exception:
        # 尺寸读不出来时交给 python-docx 按原始尺寸处理
        if hasattr(source, "seek"):
            source.seek(0)
        return (source if hasattr(source, "seek") else str(source)), None
