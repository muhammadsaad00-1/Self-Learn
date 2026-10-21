"""Turn an uploaded file into plain text for the chunker.

Supported: Markdown, plain text, PDF. The type is decided from the content (PDF magic bytes,
valid UTF-8 text), not from the client's Content-Type header, which is trivially spoofed.
"""

import io
from dataclasses import dataclass

from pypdf import PdfReader
from pypdf.errors import PdfReadError

ALLOWED_EXTENSIONS = {".md": "text/markdown", ".markdown": "text/markdown", ".txt": "text/plain"}
PDF_MAGIC = b"%PDF-"


class UnsupportedFileError(ValueError):
    pass


@dataclass(frozen=True)
class FileKind:
    content_type: str
    extension: str


def detect_kind(filename: str, data: bytes) -> FileKind:
    """Accept only types we can parse, judged by the bytes themselves."""
    if data.startswith(PDF_MAGIC):
        return FileKind("application/pdf", ".pdf")
    extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in ALLOWED_EXTENSIONS:
        raise UnsupportedFileError("Only .md, .txt and .pdf files are supported")
    if b"\x00" in data[:8192]:  # binary content with a text extension
        raise UnsupportedFileError("File is not a text document")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        raise UnsupportedFileError("Text files must be UTF-8") from None
    return FileKind(ALLOWED_EXTENSIONS[extension], extension)


def extract_text(data: bytes, content_type: str) -> str:
    if content_type == "application/pdf":
        try:
            reader = PdfReader(io.BytesIO(data))
            # One blank line between pages keeps paragraphs from merging across pages.
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except PdfReadError as e:
            raise UnsupportedFileError(f"Unreadable PDF: {e}") from None
    else:
        text = data.decode("utf-8")
    if not text.strip():
        # Scanned PDFs have images, not text; OCR is out of scope for now.
        raise UnsupportedFileError("No extractable text (scanned PDFs need OCR)")
    return text
