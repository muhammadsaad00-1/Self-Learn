"""Import use cases: stage rows (typed, CSV, sample, or extracted from a report), let people
review and fix them, then commit them to the real tables once, in one transaction.

    manual / CSV / sample ──► rows checked ──► REVIEW ──(admin commits)──► ledger / trips
    PDF / text report ──► EXTRACTING ──(worker: extraction agent)──► REVIEW ──► ...

Nothing an import contains reaches the tables the agents read until a person has seen it.
"""

import csv
import hashlib
import hmac
import io
import logging
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from agentflow.audit import service as audit
from agentflow.datasets.models import BatchSource, BatchStatus, ImportBatch, ImportRow
from agentflow.datasets.templates import RECORD_TYPES, RecordType, check_row, map_headers
from agentflow.identity import service as identity
from agentflow.jobs import Claim, backoff
from agentflow.ledger import service as ledger
from agentflow.transport import service as transport

log = logging.getLogger("agentflow.datasets")

MAX_ROWS = 1000  # per batch: review in a browser stays usable
MAX_TEXT_CHARS = 48_000  # of a report the extraction agent reads (8 chunks x 6,000)


class BatchNotFoundError(Exception):
    pass


class BatchLockedError(Exception):
    """The batch is no longer in review (committed, discarded, extracting)."""


class ImportFileError(Exception):
    pass


def _xlsx_xml(data: bytes) -> ET.Element:
    """Parse an XLSX XML part, rejecting DTD/entity declarations before stdlib parsing.

    XLSX does not require either feature. Rejecting them prevents entity expansion and
    external entity tricks while avoiding another runtime dependency.
    """
    try:
        # UTF-16 would hide "<!DOCTYPE" from a byte search; Excel writes UTF-8.
        upper = data.decode("utf-8-sig").upper()
    except UnicodeDecodeError:
        raise ImportFileError("Excel workbook XML must be UTF-8") from None
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise ImportFileError("Excel workbook contains unsupported XML declarations")
    return ET.fromstring(data)  # noqa: S314 -- DTD/entity declarations are rejected above.


# --- Creating batches ---------------------------------------------------------------------


def parse_csv(record_type: RecordType, data: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    """CSV bytes -> raw rows keyed by field name, plus notes (ignored columns, ...)."""
    try:
        decoded = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = data.decode("latin-1")
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(decoded[:4096], ",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(decoded), dialect)
    try:
        lines = [row for row in reader if any(cell.strip() for cell in row)]
    except csv.Error:
        raise ImportFileError("The CSV file has malformed rows") from None
    return parse_matrix(record_type, lines)


def parse_xlsx(record_type: RecordType, data: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    """Read the first worksheet from an XLSX archive without extracting files to disk."""
    ns = {
        "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "p": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as book:
            if sum(item.file_size for item in book.infolist()) > 50 * 1024 * 1024:
                raise ImportFileError("Uncompressed Excel workbook is larger than 50 MB")
            workbook = _xlsx_xml(book.read("xl/workbook.xml"))
            first = workbook.find("m:sheets/m:sheet", ns)
            if first is None:
                raise ImportFileError("The Excel workbook has no worksheets")
            rel_id = first.attrib[f"{{{ns['r']}}}id"]
            rels = _xlsx_xml(book.read("xl/_rels/workbook.xml.rels"))
            target = next(
                rel.attrib["Target"]
                for rel in rels.findall("p:Relationship", ns)
                if rel.attrib.get("Id") == rel_id
            )
            sheet_path = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
            # Spreadsheet packages may use ../ paths in relationships. Resolve safely within
            # the zip namespace; never extract or access a filesystem path.
            parts: list[str] = []
            for part in sheet_path.split("/"):
                if part == "..":
                    if not parts:
                        raise ImportFileError("Invalid Excel worksheet path")
                    parts.pop()
                elif part not in ("", "."):
                    parts.append(part)
            sheet_path = "/".join(parts)
            shared: list[str] = []
            if "xl/sharedStrings.xml" in book.namelist():
                strings = _xlsx_xml(book.read("xl/sharedStrings.xml"))
                shared = ["".join(node.itertext()) for node in strings.findall("m:si", ns)]
            date_styles: set[int] = set()
            if "xl/styles.xml" in book.namelist():
                styles = _xlsx_xml(book.read("xl/styles.xml"))
                custom_dates = {
                    int(fmt.attrib["numFmtId"])
                    for fmt in styles.findall("m:numFmts/m:numFmt", ns)
                    if re.search(
                        r"[ydm]",
                        re.sub(
                            r'"[^"]*"|\\.|\[[^]]*\]',
                            "",
                            fmt.attrib.get("formatCode", "").lower(),
                        ),
                    )
                }
                xfs = styles.find("m:cellXfs", ns)
                if xfs is not None:
                    date_styles = {
                        i
                        for i, xf in enumerate(xfs)
                        if int(xf.attrib.get("numFmtId", "0"))
                        in custom_dates | set(range(14, 23)) | {45, 46, 47}
                    }
            root = _xlsx_xml(book.read(sheet_path))
            matrix: list[list[str]] = []
            for row in root.findall("m:sheetData/m:row", ns):
                cells: list[str] = []
                for cell in row.findall("m:c", ns):
                    # "r" (A1, C7, ...) is optional in the format: without it, the cell is
                    # the next one in the row.
                    ref = cell.attrib.get("r")
                    col = 0 if ref else len(cells) + 1
                    for char in ref or "":
                        if char.isalpha():
                            col = col * 26 + ord(char.upper()) - ord("A") + 1
                        else:
                            break
                    value = cell.find("m:v", ns)
                    text_value = ""
                    kind = cell.attrib.get("t")
                    if kind == "inlineStr":
                        inline = cell.find("m:is", ns)
                        text_value = "".join(inline.itertext()) if inline is not None else ""
                    elif value is not None and kind == "s":
                        text_value = shared[int(value.text or "0")]
                    elif value is not None:
                        text_value = value.text or ""
                        if (
                            cell.attrib.get("s", "0").isdigit()
                            and int(cell.attrib.get("s", "0")) in date_styles
                        ):
                            date = datetime(1899, 12, 30) + timedelta(days=float(text_value))
                            text_value = date.isoformat(sep=" ")
                    if col > 16_384:
                        raise ImportFileError("Excel worksheet has too many columns")
                    if len(cells) < col:
                        cells.extend([""] * (col - len(cells)))
                    if col:
                        cells[col - 1] = text_value
                if any(cell.strip() for cell in cells):
                    matrix.append(cells)
                if len(matrix) > MAX_ROWS + 1:
                    break
    except ImportFileError:
        raise
    except (
        KeyError,
        ValueError,
        IndexError,
        OverflowError,
        OSError,
        RuntimeError,
        zipfile.BadZipFile,
        ET.ParseError,
        StopIteration,
    ):
        raise ImportFileError("Unreadable Excel workbook") from None
    if not matrix:
        raise ImportFileError("The Excel worksheet has no rows")
    notes = [f"Read worksheet '{first.attrib.get('name', 'Sheet 1')}'"]
    # Convert XLSX cells into a real CSV byte stream first, then reuse the exact CSV
    # dialect/header/validation path. This is kept in memory and never writes uploads to disk.
    converted = io.StringIO(newline="")
    writer = csv.writer(converted)
    writer.writerows(matrix)
    rows, parse_notes = parse_csv(record_type, converted.getvalue().encode("utf-8"))
    return rows, [*notes, *parse_notes]


def parse_matrix(
    record_type: RecordType, lines: list[list[str]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Header mapping and row limiting shared by CSV and spreadsheet importers."""
    if not lines:
        raise ImportFileError("The file has no rows")
    headers, body = lines[0], lines[1:]
    mapping = map_headers(record_type, headers)
    if not mapping:
        raise ImportFileError(
            "No column matched a field of "
            f"{record_type.label}: expected headers like "
            + ", ".join(f.label for f in record_type.fields if f.required)
        )
    notes = [f'Column "{h}" ignored' for i, h in enumerate(headers) if i not in mapping and h]
    missing = [f.label for f in record_type.fields if f.required and f.name not in mapping.values()]
    if missing:
        notes.append("No column for: " + ", ".join(missing))
    if len(body) > MAX_ROWS:
        notes.append(f"Only the first {MAX_ROWS} of {len(body)} rows were imported")
    rows = [
        {field: row[i] if i < len(row) else None for i, field in mapping.items()}
        for row in body[:MAX_ROWS]
    ]
    return rows, notes


async def create_batch(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    record_type: str,
    source: BatchSource,
    filename: str | None = None,
    rows: list[dict[str, Any]] | None = None,
    source_text: str | None = None,
    source_data: bytes | None = None,
    source_content_type: str | None = None,
    notes: list[str] | None = None,
) -> ImportBatch:
    """Rows go straight to review; a report's text is queued for the extraction agent."""
    batch = ImportBatch(
        org_id=org_id,
        record_type=record_type,
        source=source,
        filename=filename,
        status=(
            BatchStatus.REVIEW
            if source_text is None and source_data is None
            else BatchStatus.EXTRACTING
        ),
        source_text=source_text[:MAX_TEXT_CHARS] if source_text else None,
        source_data=source_data,
        source_content_type=source_content_type,
        notes=notes or [],
        created_by=user_id,
    )
    session.add(batch)
    await session.flush()
    if rows:
        await add_rows(session, batch, rows)
    await audit.record(
        session,
        org_id=org_id,
        actor_user_id=user_id,
        action="import.created",
        target_type="import_batch",
        target_id=batch.id,
        details={"record_type": record_type, "source": source, "rows": len(rows or [])},
    )
    return batch


async def company_zone(session: AsyncSession, org_id: uuid.UUID) -> ZoneInfo:
    profile = (await identity.get_profile(session, org_id)).profile
    return ZoneInfo(profile.timezone if profile else "Asia/Karachi")


async def add_rows(session: AsyncSession, batch: ImportBatch, raws: list[dict[str, Any]]) -> None:
    record_type = RECORD_TYPES[batch.record_type]
    zone = await company_zone(session, batch.org_id)
    start = await session.scalar(
        select(func.coalesce(func.max(ImportRow.position), 0)).where(ImportRow.batch_id == batch.id)
    )
    for i, raw in enumerate(raws[:MAX_ROWS], start=(start or 0) + 1):
        checked = check_row(record_type, raw, zone)
        session.add(
            ImportRow(
                org_id=batch.org_id,
                batch_id=batch.id,
                position=i,
                data=checked.data,
                # Code checks against the source report; editing the row re-checks and clears.
                errors=checked.errors | raw.get("_flags", {}),
            )
        )
    await session.flush()


# --- Reading and editing --------------------------------------------------------------------


async def get_batch(session: AsyncSession, org_id: uuid.UUID, batch_id: uuid.UUID) -> ImportBatch:
    batch = await session.scalar(
        select(ImportBatch).where(ImportBatch.id == batch_id, ImportBatch.org_id == org_id)
    )
    if batch is None:
        raise BatchNotFoundError
    return batch


async def rows_of(session: AsyncSession, batch_id: uuid.UUID) -> list[ImportRow]:
    return list(
        await session.scalars(
            select(ImportRow).where(ImportRow.batch_id == batch_id).order_by(ImportRow.position)
        )
    )


async def list_batches(session: AsyncSession, org_id: uuid.UUID) -> list[ImportBatch]:
    return list(
        await session.scalars(
            select(ImportBatch)
            .where(ImportBatch.org_id == org_id)
            .order_by(ImportBatch.created_at.desc())
            .limit(50)
        )
    )


def _editable(batch: ImportBatch) -> None:
    if batch.status != BatchStatus.REVIEW:
        raise BatchLockedError(f"This import is {batch.status}")


async def update_row(
    session: AsyncSession, batch: ImportBatch, row_id: uuid.UUID, raw: dict[str, Any]
) -> ImportRow:
    _editable(batch)
    row = await session.scalar(
        select(ImportRow).where(ImportRow.id == row_id, ImportRow.batch_id == batch.id)
    )
    if row is None:
        raise BatchNotFoundError
    zone = await company_zone(session, batch.org_id)
    checked = check_row(RECORD_TYPES[batch.record_type], raw, zone)
    row.data, row.errors = checked.data, checked.errors
    await session.flush()
    return row


async def delete_row(session: AsyncSession, batch: ImportBatch, row_id: uuid.UUID) -> None:
    _editable(batch)
    row = await session.scalar(
        select(ImportRow).where(ImportRow.id == row_id, ImportRow.batch_id == batch.id)
    )
    if row is None:
        raise BatchNotFoundError
    await session.delete(row)


async def add_row(session: AsyncSession, batch: ImportBatch, raw: dict[str, Any]) -> None:
    _editable(batch)
    await add_rows(session, batch, [raw])


async def discard(session: AsyncSession, batch: ImportBatch, user_id: uuid.UUID) -> None:
    if batch.status == BatchStatus.COMMITTED:
        raise BatchLockedError("A committed import can't be discarded")
    batch.status = BatchStatus.DISCARDED
    batch.source_text = None
    batch.source_data = None
    batch.source_content_type = None
    for row in await rows_of(session, batch.id):
        if "cnic" in row.data:
            row.data = {**row.data, "cnic": None}
    await audit.record(
        session,
        org_id=batch.org_id,
        actor_user_id=user_id,
        action="import.discarded",
        target_type="import_batch",
        target_id=batch.id,
    )


# --- Commit -------------------------------------------------------------------------------


async def commit(
    session: AsyncSession, batch: ImportBatch, user_id: uuid.UUID, *, cnic_secret: str
) -> dict[str, Any]:
    """Write every row to the real tables, in this one transaction, exactly once.

    Refused while any row has an error. Rows whose dependencies are missing (a transaction
    for a customer that doesn't exist) are marked and the commit is refused, so nothing is
    half-imported: {"committed": False, "blocked": n}.
    """
    _editable(batch)
    rows = await rows_of(session, batch.id)
    if not rows:
        raise BatchLockedError("Nothing to import: the batch has no rows")
    if any(r.errors for r in rows):
        return {"committed": False, "blocked": sum(1 for r in rows if r.errors)}

    if batch.record_type == "transactions":
        refs = {r.data["customer_ref"] for r in rows}
        known = await ledger.existing_customer_refs(session, batch.org_id, refs)
        missing = [r for r in rows if r.data["customer_ref"] not in known]
        if missing:
            for r in missing:
                r.errors = {
                    "customer_ref": "No customer with this ID: import or add the customer first"
                }
            return {"committed": False, "blocked": len(missing)}

    created = updated = skipped = 0
    for r in rows:
        data = _typed(r.data)
        match batch.record_type:
            case "customers":
                cnic = re.sub(r"\D", "", data.get("cnic") or "")
                digest = (
                    hmac.new(
                        cnic_secret.encode(), f"{batch.org_id}:{cnic}".encode(), hashlib.sha256
                    ).hexdigest()
                    if cnic
                    else None
                )
                new = await ledger.upsert_customer(session, batch.org_id, data, cnic_digest=digest)
                if "cnic" in r.data:
                    r.data = {**r.data, "cnic": None}
                created, updated = created + new, updated + (not new)
            case "transactions":
                new = await ledger.insert_transaction(session, batch.org_id, data)
                created, skipped = created + new, skipped + (not new)  # duplicate reference
            case "trips":
                new = await transport.upsert_trip(session, batch.org_id, data)
                created, updated = created + new, updated + (not new)
    summary = {"created": created, "updated": updated, "skipped_duplicates": skipped}
    batch.status = BatchStatus.COMMITTED
    batch.committed_by = user_id
    batch.committed_at = datetime.now(UTC)
    batch.summary = summary
    await audit.record(
        session,
        org_id=batch.org_id,
        actor_user_id=user_id,
        action="import.committed",
        target_type="import_batch",
        target_id=batch.id,
        details={"record_type": batch.record_type, "rows": len(rows)} | summary,
    )
    return {"committed": True, **summary}


def _typed(data: dict[str, Any]) -> dict[str, Any]:
    """Checked JSON values back to Python types for the services (dates)."""
    out = dict(data)
    for key in ("occurred_at", "departs_at", "arrives_at"):
        if isinstance(out.get(key), str):
            out[key] = datetime.fromisoformat(out[key])
    return out


# --- Extraction jobs (worker) ---------------------------------------------------------------


async def claim_next_extraction(
    session: AsyncSession, *, max_attempts: int, lease_seconds: int
) -> Claim | None:
    row = (
        await session.execute(
            text("SELECT * FROM claim_next_import(:max_attempts, :lease_seconds)"),
            {"max_attempts": max_attempts, "lease_seconds": lease_seconds},
        )
    ).one_or_none()
    return Claim(row.claimed_id, row.claimed_org_id, row.claimed_attempt) if row else None


async def lock_if_still_ours(session: AsyncSession, claim: Claim) -> ImportBatch | None:
    batch = await session.scalar(
        select(ImportBatch).where(ImportBatch.id == claim.job_id).with_for_update()
    )
    if (
        batch is None
        or batch.status != BatchStatus.EXTRACTING
        or batch.attempts != claim.attempt
        or (batch.locked_until and batch.locked_until < datetime.now(UTC))
    ):
        log.warning("import.lease_lost", extra={"attempt": claim.attempt})
        return None
    return batch


async def complete_extraction(
    session: AsyncSession, batch: ImportBatch, rows: list[dict[str, Any]], notes: list[str]
) -> None:
    batch.status = BatchStatus.REVIEW
    batch.locked_until = None
    batch.error = None
    batch.source_data = None
    batch.source_content_type = None
    batch.source_text = None
    batch.notes = [*batch.notes, *notes]
    if not rows:
        batch.notes = [*batch.notes, "No records found in the report: add them by hand"]
    await add_rows(session, batch, rows)


async def fail_extraction(
    session: AsyncSession, batch: ImportBatch, claim: Claim, error: str, *, max_attempts: int
) -> None:
    batch.error = error
    batch.locked_until = None
    if claim.attempt >= max_attempts:
        batch.status = BatchStatus.FAILED
        batch.source_data = None
        batch.source_content_type = None
        batch.source_text = None
    else:
        batch.available_at = datetime.now(UTC) + max(backoff(claim.attempt), timedelta(seconds=20))
