import unicodedata
import zipfile

from conftest import CID, FakeChatGPT
from pypdf import PdfReader

from session_vault.archive import archive_conversations
from session_vault.conversation import parse_conversation
from session_vault.render import export_pdf, render_html


def test_html_escapes_active_content(conversation_data):
    data = conversation_data
    data["title"] = "<script>window.injection = 1</script>"
    data["mapping"]["m0"]["message"]["content"]["parts"] = [
        '<script>fetch("https://evil.invalid/")</script>\n\n'
        "![remote](https://evil.invalid/image.png)\n\n[bad](javascript:alert(1))"
    ]
    document = render_html(parse_conversation(data, CID))
    assert '<script>fetch("https://evil.invalid/")</script>' not in document
    assert 'src="https://evil.invalid' not in document
    assert 'href="javascript:' not in document
    assert "&lt;script&gt;" in document


def test_pdf_and_full_archive_are_readable(conversation_data, tmp_path):
    manifest = archive_conversations(FakeChatGPT(conversation_data), [CID], tmp_path)
    item = manifest["conversations"][0]
    reader = PdfReader(tmp_path / item["pdf"])
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert "A weekend worth remembering" in text
    assert "Philosopher" in text
    assert "Weekend" in text
    # macOS PingFang's PDF ToUnicode map may use compatibility ideographs/radicals.
    assert "中文" in unicodedata.normalize("NFKC", text)
    assert item["message_count"] == 4
    assert len(manifest["sha256"]) == 3
    with zipfile.ZipFile(tmp_path / "session-archives.zip") as archive:
        assert set(archive.namelist()) == set(manifest["sha256"]) | {"manifest.json"}


def test_long_code_and_table_paginate(conversation_data, tmp_path):
    data = conversation_data
    data["mapping"]["m1"]["message"]["content"]["parts"] = [
        "```python\n"
        + "\n".join(f"print('line {i}')" for i in range(160))
        + "\n```\n\n| Column | Value |\n|---|---|\n"
        + "\n".join(f"| row {i} | 内容 {i} |" for i in range(80))
    ]
    target = tmp_path / "long.pdf"
    export_pdf(render_html(parse_conversation(data, CID)), target)
    reader = PdfReader(target)
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert len(reader.pages) >= 5
    assert "line 159" in text and "row 79" in text
