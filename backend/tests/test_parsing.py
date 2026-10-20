"""File type detection (by content, not by the client's claims) and text extraction."""

import pytest

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
        (b"%PDF-1.4 garbage", "application/pdf", "Unreadable PDF|No extractable text"),
        (make_pdf(""), "application/pdf", "No extractable text"),  # e.g. a scanned PDF
        (b"   \n\n ", "text/plain", "No extractable text"),
    ],
)
def test_documents_without_text_rejected(data: bytes, content_type: str, message: str) -> None:
    with pytest.raises(UnsupportedFileError, match=message):
        extract_text(data, content_type)
