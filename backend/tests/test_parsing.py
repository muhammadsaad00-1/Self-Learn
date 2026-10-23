"""File type detection (by content, not by the client's claims) and text extraction."""

import shutil
import subprocess

import pytest

from agentflow.knowledge import parsing
from agentflow.knowledge.parsing import UnsupportedFileError, detect_kind, extract_text


def make_pdf(text: str) -> bytes:
    """A minimal valid one-page PDF showing `text` (with a correct xref table)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return out


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [("policy.md", "text/markdown"), ("NOTES.TXT", "text/plain"), ("a.markdown", "text/markdown")],
)
def test_text_documents_accepted(filename: str, content_type: str) -> None:
    assert (
        detect_kind(filename, b"# Refunds\nDuplicates are reversed.").content_type == content_type
    )


def test_pdf_detected_by_magic_bytes_whatever_the_name() -> None:
    kind = detect_kind("upload.bin", make_pdf("hi"))
    assert (kind.content_type, kind.extension) == ("application/pdf", ".pdf")


@pytest.mark.parametrize(
    ("filename", "data", "message"),
    [
        ("malware.exe", b"MZ\x90\x00", "Only .md, .txt and .pdf"),
        ("policy.docx", b"PK\x03\x04", "Only .md, .txt and .pdf"),
        ("no_extension", b"text", "Only .md, .txt and .pdf"),
        ("fake.txt", b"MZ\x00\x00binary", "not a text document"),  # binary with a text name
        ("latin1.txt", "café".encode("latin-1"), "must be UTF-8"),
    ],
)
def test_other_files_rejected(filename: str, data: bytes, message: str) -> None:
    with pytest.raises(UnsupportedFileError, match=message):
        detect_kind(filename, data)


def test_extracts_pdf_text() -> None:
    assert "Duplicate debits are reversed" in extract_text(
        make_pdf("Duplicate debits are reversed"), "application/pdf"
    )


def test_extracts_utf8_text() -> None:
    assert (
        extract_text("Fee: ₨ 500 — Urdu: فیس".encode(), "text/markdown") == "Fee: ₨ 500 — Urdu: فیس"
    )


@pytest.mark.parametrize(
    ("data", "content_type", "message"),
    [
        (b"%PDF-1.4 garbage", "application/pdf", "Unreadable PDF|No text could be recognized"),
        (make_pdf(""), "application/pdf", "No text could be recognized"),  # blank scan
        (b"   \n\n ", "text/plain", "No text could be recognized"),
    ],
)
def test_documents_without_text_rejected(
    data: bytes, content_type: str, message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # OCR finds nothing on a blank page; stubbed so the test doesn't depend on Tesseract.
    monkeypatch.setattr(parsing, "_ocr_pages", lambda _data, pages: dict.fromkeys(pages, ""))
    with pytest.raises(UnsupportedFileError, match=message):
        extract_text(data, content_type)


def test_scanned_pages_are_ocred_and_text_pages_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []

    def fake_ocr(_data: bytes, pages: list[int]) -> dict[int, str]:
        seen.extend(pages)
        return dict.fromkeys(pages, "Statement for CUST-1001")

    monkeypatch.setattr(parsing, "_ocr_pages", fake_ocr)
    assert extract_text(make_pdf(""), "application/pdf") == "Statement for CUST-1001"
    assert seen == [0]
    seen.clear()
    assert "Duplicate" in extract_text(make_pdf("Duplicate"), "application/pdf")
    assert seen == []  # pages with a text layer are never sent to OCR


def test_ocr_errors_do_not_leak_command_details(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, ["/usr/bin/pdftoppm", "/srv/upload-123.pdf"])

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(UnsupportedFileError) as e:
        extract_text(make_pdf(""), "application/pdf")
    assert str(e.value) == "Could not read scanned page 1"
