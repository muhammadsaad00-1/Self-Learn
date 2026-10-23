"""Data imports (admins): bring customers, transactions or trips in by hand, CSV, or a PDF /
text report; review the rows; commit them once.

    GET    /v1/imports/templates               record types for this company's industry
    POST   /v1/imports/manual                  rows typed in (may be empty: fill in the grid)
    POST   /v1/imports/file                    CSV (parsed now) or PDF/text (extracted by an agent)
    POST   /v1/imports/sample                  a realistic sample file (demos)
    GET    /v1/imports                         recent imports
    GET    /v1/imports/{id}                    one import with its rows and their problems
    POST   /v1/imports/{id}/rows               add a row
    PATCH  /v1/imports/{id}/rows/{row_id}      edit a row (checked again)
    DELETE /v1/imports/{id}/rows/{row_id}      drop a row
    POST   /v1/imports/{id}/commit             write to the real tables (once)
    POST   /v1/imports/{id}/discard
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from agentflow.datasets import service
from agentflow.datasets.models import BatchSource, BatchStatus
from agentflow.datasets.samples import SAMPLES
from agentflow.datasets.templates import RECORD_TYPES
from agentflow.db import SessionDep
from agentflow.identity import service as identity
from agentflow.identity.deps import AdminContextDep, SettingsDep
from agentflow.knowledge.parsing import UnsupportedFileError, detect_kind, extract_text

router = APIRouter(prefix="/v1/imports", tags=["imports"])

MAX_FILE_BYTES = 10 * 1024 * 1024


class FieldOut(BaseModel):
    name: str
    label: str
    kind: str
    required: bool
    choices: list[str]
    hint: str


class TemplateOut(BaseModel):
    name: str
    label: str
    description: str
    fields: list[FieldOut]


class RowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    data: dict[str, Any]
    errors: dict[str, str]


class BatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    record_type: str
    source: BatchSource
    filename: str | None
    status: BatchStatus
    notes: list[str]
    error: str | None
    summary: dict[str, Any] | None
    created_at: datetime
    committed_at: datetime | None


class BatchDetail(BatchOut):
    rows: list[RowOut]
    row_count: int
    error_count: int


class ManualIn(BaseModel):
    record_type: str
    rows: list[dict[str, Any]] = Field(default=[], max_length=service.MAX_ROWS)


class RowIn(BaseModel):
    data: dict[str, Any]


class SampleIn(BaseModel):
    record_type: str


class CommitOut(BaseModel):
    committed: bool
    blocked: int = 0  # rows with problems: fix or delete them first
    created: int = 0
    updated: int = 0
    skipped_duplicates: int = 0


async def _allowed(session: SessionDep, org_id: uuid.UUID, record_type: str) -> None:
    caps = (await identity.get_profile(session, org_id)).capabilities
    if record_type not in caps.record_types:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"'{record_type}' isn't a record type for this company "
            f"(available: {', '.join(caps.record_types)})",
        )


async def _detail(session: SessionDep, batch: Any) -> BatchDetail:
    rows = await service.rows_of(session, batch.id)
    return BatchDetail(
        **BatchOut.model_validate(batch).model_dump(),
        rows=[RowOut.model_validate(r) for r in rows],
        row_count=len(rows),
        error_count=sum(1 for r in rows if r.errors),
    )


async def _batch(session: SessionDep, ctx: AdminContextDep, batch_id: uuid.UUID) -> Any:
    try:
        return await service.get_batch(session, ctx.org_id, batch_id)
    except service.BatchNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import not found") from None


def _locked(e: service.BatchLockedError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(e))


@router.get("/templates")
async def templates(ctx: AdminContextDep, session: SessionDep) -> list[TemplateOut]:
    caps = (await identity.get_profile(session, ctx.org_id)).capabilities
    return [
        TemplateOut(
            name=r.name,
            label=r.label,
            description=r.description,
            fields=[
                FieldOut(
                    name=f.name,
                    label=f.label,
                    kind=f.kind,
                    required=f.required,
                    choices=list(f.choices),
                    hint=f.hint,
                )
                for f in r.fields
            ],
        )
        for name in caps.record_types
        if (r := RECORD_TYPES[name])
    ]


@router.post("/manual", status_code=status.HTTP_201_CREATED)
async def manual(body: ManualIn, ctx: AdminContextDep, session: SessionDep) -> BatchDetail:
    await _allowed(session, ctx.org_id, body.record_type)
    batch = await service.create_batch(
        session,
        org_id=ctx.org_id,
        user_id=ctx.user.id,
        record_type=body.record_type,
        source=BatchSource.MANUAL,
        rows=body.rows,
    )
    return await _detail(session, batch)


@router.post("/file", status_code=status.HTTP_201_CREATED)
async def upload(
    ctx: AdminContextDep,
    session: SessionDep,
    record_type: Annotated[str, Form()],
    file: Annotated[
        UploadFile, File(description="CSV/XLSX spreadsheet or a PDF/text report (10 MB)")
    ],
) -> BatchDetail:
    """CSV/XLSX is parsed into the review grid. PDFs are extracted (including OCR) in the
    worker; poll until an import reaches `review`."""
    await _allowed(session, ctx.org_id, record_type)
    data = await file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "File is larger than 10 MB")
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "File is empty")
    name = file.filename or "upload"
    try:
        if name.lower().endswith((".csv", ".tsv")):
            rows, notes = service.parse_csv(RECORD_TYPES[record_type], data)
            batch = await service.create_batch(
                session,
                org_id=ctx.org_id,
                user_id=ctx.user.id,
                record_type=record_type,
                source=BatchSource.CSV,
                filename=name,
                rows=rows,
                notes=notes,
            )
        elif name.lower().endswith(".xlsx"):
            if not data.startswith(b"PK\x03\x04"):
                raise service.ImportFileError("The file is not a valid XLSX workbook")
            rows, notes = service.parse_xlsx(RECORD_TYPES[record_type], data)
            batch = await service.create_batch(
                session,
                org_id=ctx.org_id,
                user_id=ctx.user.id,
                record_type=record_type,
                source=BatchSource.XLSX,
                filename=name,
                rows=rows,
                notes=notes,
            )
        else:
            kind = detect_kind(name, data)
            if kind.content_type == "application/pdf":
                batch = await service.create_batch(
                    session,
                    org_id=ctx.org_id,
                    user_id=ctx.user.id,
                    record_type=record_type,
                    source=BatchSource.DOCUMENT,
                    filename=name,
                    source_data=data,
                    source_content_type=kind.content_type,
                )
                return await _detail(session, batch)
            report = extract_text(data, kind.content_type)
            batch = await service.create_batch(
                session,
                org_id=ctx.org_id,
                user_id=ctx.user.id,
                record_type=record_type,
                source=BatchSource.DOCUMENT,
                filename=name,
                source_text=report,
            )
    except (service.ImportFileError, UnsupportedFileError) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from None
    return await _detail(session, batch)


@router.post("/sample", status_code=status.HTTP_201_CREATED)
async def sample(
    body: SampleIn, ctx: AdminContextDep, session: SessionDep, settings: SettingsDep
) -> BatchDetail:
    """A realistic sample file through the CSV path (never in production)."""
    if settings.env == "prod":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    await _allowed(session, ctx.org_id, body.record_type)
    rows, notes = service.parse_csv(RECORD_TYPES[body.record_type], SAMPLES[body.record_type]())
    batch = await service.create_batch(
        session,
        org_id=ctx.org_id,
        user_id=ctx.user.id,
        record_type=body.record_type,
        source=BatchSource.SAMPLE,
        filename=f"sample-{body.record_type}.csv",
        rows=rows,
        notes=notes,
    )
    return await _detail(session, batch)


@router.get("")
async def list_imports(ctx: AdminContextDep, session: SessionDep) -> list[BatchOut]:
    return [BatchOut.model_validate(b) for b in await service.list_batches(session, ctx.org_id)]


@router.get("/{batch_id}")
async def get_import(batch_id: uuid.UUID, ctx: AdminContextDep, session: SessionDep) -> BatchDetail:
    return await _detail(session, await _batch(session, ctx, batch_id))


@router.post("/{batch_id}/rows", status_code=status.HTTP_201_CREATED)
async def add_row(
    batch_id: uuid.UUID, body: RowIn, ctx: AdminContextDep, session: SessionDep
) -> BatchDetail:
    batch = await _batch(session, ctx, batch_id)
    try:
        await service.add_row(session, batch, body.data)
    except service.BatchLockedError as e:
        raise _locked(e) from None
    return await _detail(session, batch)


@router.patch("/{batch_id}/rows/{row_id}")
async def edit_row(
    batch_id: uuid.UUID, row_id: uuid.UUID, body: RowIn, ctx: AdminContextDep, session: SessionDep
) -> RowOut:
    batch = await _batch(session, ctx, batch_id)
    try:
        return RowOut.model_validate(await service.update_row(session, batch, row_id, body.data))
    except service.BatchLockedError as e:
        raise _locked(e) from None
    except service.BatchNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Row not found") from None


@router.delete("/{batch_id}/rows/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_row(
    batch_id: uuid.UUID, row_id: uuid.UUID, ctx: AdminContextDep, session: SessionDep
) -> Response:
    batch = await _batch(session, ctx, batch_id)
    try:
        await service.delete_row(session, batch, row_id)
    except service.BatchLockedError as e:
        raise _locked(e) from None
    except service.BatchNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Row not found") from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{batch_id}/commit")
async def commit(
    batch_id: uuid.UUID, ctx: AdminContextDep, session: SessionDep, settings: SettingsDep
) -> CommitOut:
    """Write the reviewed rows to the real tables, once. Refused (committed=false) while rows
    have problems; nothing is half-imported."""
    batch = await _batch(session, ctx, batch_id)
    try:
        return CommitOut(
            **await service.commit(
                session,
                batch,
                ctx.user.id,
                cnic_secret=settings.cnic_hash_secret.get_secret_value(),
            )
        )
    except service.BatchLockedError as e:
        raise _locked(e) from None


@router.post("/{batch_id}/discard")
async def discard(batch_id: uuid.UUID, ctx: AdminContextDep, session: SessionDep) -> BatchOut:
    batch = await _batch(session, ctx, batch_id)
    try:
        await service.discard(session, batch, ctx.user.id)
    except service.BatchLockedError as e:
        raise _locked(e) from None
    return BatchOut.model_validate(batch)
