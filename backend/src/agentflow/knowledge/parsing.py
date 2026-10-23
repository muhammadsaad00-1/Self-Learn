"""Turn an uploaded file into plain text for the chunker.

Supported: Markdown, plain text, PDF. The type is decided from the content (PDF magic bytes,
valid UTF-8 text), not from the client's Content-Type header, which is trivially spoofed.
"""

import io
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

ALLOWED_EXTENSIONS = {".md": "text/markdown", ".markdown": "text/markdown", ".txt": "text/plain"}
log = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"
MAX_PDF_PAGES = 100
MAX_OCR_PAGES = 6
MAX_EXTRACTED_CHARS = 48_000


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
            if len(reader.pages) > MAX_PDF_PAGES:
                raise UnsupportedFileError(f"PDFs are limited to {MAX_PDF_PAGES} pages")
            # OCR only pages without an embedded text layer. This handles mixed PDFs too.
            pages = [page.extract_text() or "" for page in reader.pages]
            scanned = [i for i, page_text in enumerate(pages) if not page_text.strip()]
            if scanned:
                if len(scanned) > MAX_OCR_PAGES:
                    raise UnsupportedFileError(
                        f"This PDF has more than {MAX_OCR_PAGES} scanned pages; "
                        "split it into smaller files"
                    )
                recognized = _ocr_pages(data, scanned)
                for index, page_text in recognized.items():
                    pages[index] = page_text
            # One blank line between pages keeps paragraphs from merging across pages.
            text = "\n\n".join(pages)[:MAX_EXTRACTED_CHARS]
        except PdfReadError as e:
            raise UnsupportedFileError(f"Unreadable PDF: {e}") from None
    else:
        text = data.decode("utf-8")
    if not text.strip():
        raise UnsupportedFileError("No text could be recognized in this file")
    return text


def _ocr_pages(data: bytes, page_indexes: list[int]) -> dict[int, str]:
    """Render only scanned pages and recognize English/Urdu using system Poppler/Tesseract."""
    renderer = shutil.which("pdftoppm")
    ocr = shutil.which("tesseract")
    if renderer is None or ocr is None:
        raise UnsupportedFileError("OCR tools are unavailable on this server")

    with tempfile.TemporaryDirectory(prefix="agentflow-ocr-") as tmp:
        root = Path(tmp)
        pdf_path, prefix = root / "source.pdf", root / "page"
        pdf_path.write_bytes(data)
        try:
            # Absolute executable path, constant args, shell=False; no user-controlled command.
            langs = subprocess.run(  # noqa: S603
                [ocr, "--list-langs"], capture_output=True, text=True, timeout=5, check=True
            ).stdout.splitlines()
            language = "eng+urd" if "urd" in langs else "eng"
        except (OSError, subprocess.SubprocessError):
            language = "eng"

        output: dict[int, str] = {}
        for index in page_indexes:
            page_number = index + 1
            try:
                subprocess.run(  # noqa: S603 -- absolute executable and argument-list invocation.
                    [
                        renderer,
                        "-f",
                        str(page_number),
                        "-l",
                        str(page_number),
                        "-singlefile",
                        "-scale-to",
                        "2400",
                        "-png",
                        str(pdf_path),
                        str(prefix),
                    ],
                    capture_output=True,
                    timeout=10,
                    check=True,
                )
                recognized = subprocess.run(  # noqa: S603 -- absolute executable and argument-list invocation.
                    # psm 4 + kept spaces: a table stays one line per row with its columns
                    # aligned. The default layout reads column by column, which detaches
                    # amounts from their rows.
                    [
                        ocr,
                        str(prefix) + ".png",
                        "stdout",
                        "-l",
                        language,
                        "--psm",
                        "4",
                        "-c",
                        "preserve_interword_spaces=1",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=True,
                )
            except (OSError, subprocess.SubprocessError):
                # The exception text holds the command line and temp paths: log it, don't show it.
                log.warning("ocr failed", extra={"page": page_number}, exc_info=True)
                raise UnsupportedFileError(f"Could not read scanned page {page_number}") from None
            output[index] = recognized.stdout.strip()
        return output
