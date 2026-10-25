import io
import zipfile
from pathlib import Path

from PIL import Image

from backend.docx_export import markdown_to_docx


def _note_markdown() -> str:
    return (
        "# 视频笔记：《测试》\n\n"
        "## 小节一 [00:00-01:00]\n\n"
        "这是**加粗**和*斜体*，还有 `代码` 与公式 $a*b$。\n\n"
        "- 项目一\n"
        "- 项目二\n\n"
        "> 引用一句话\n\n"
        "| 列一 | 列二 |\n"
        "| --- | --- |\n"
        "| 甲 | 乙 |\n\n"
        "![截图 1](./frames/f1.png)\n"
    )


def test_markdown_to_docx_keeps_structure_and_embeds_images(tmp_path: Path) -> None:
    frames = tmp_path / "frames"
    frames.mkdir()
    Image.new("RGB", (320, 180), (200, 30, 30)).save(frames / "f1.png")

    data = markdown_to_docx(_note_markdown(), tmp_path, "测试")

    from docx import Document

    document = Document(io.BytesIO(data))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "视频笔记：《测试》" in text
    assert "项目一" in text
    assert "引用一句话" in text
    assert "加粗" in text
    assert document.tables and document.tables[0].cell(1, 0).text == "甲"
    assert len(document.inline_shapes) == 1
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert any(name.startswith("word/media/") for name in archive.namelist())


def test_docx_export_does_not_fetch_external_images(tmp_path: Path) -> None:
    markdown = "# 笔记\n\n![远程图](https://example.com/x.png)\n"
    data = markdown_to_docx(markdown, tmp_path, "笔记")

    from docx import Document

    document = Document(io.BytesIO(data))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "图片：远程图" in text
    assert not document.inline_shapes


def test_docx_export_ignores_paths_outside_the_task_directory(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.png"
    Image.new("RGB", (10, 10), (0, 0, 0)).save(outside)
    markdown = f"![越界](../{outside.name})\n"
    data = markdown_to_docx(markdown, tmp_path, "笔记")

    from docx import Document

    document = Document(io.BytesIO(data))
    assert not document.inline_shapes
