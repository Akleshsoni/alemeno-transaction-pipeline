import uuid
import io
from typing import Optional

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.models.job import Job, JobStatus
from app.models.transaction import Transaction, JobSummary
from app.api.schemas import (
    JobCreatedResponse, JobStatusResponse, JobResultsResponse,
    JobListItem, JobSummaryOut, TransactionOut,
)
from app.workers.tasks import process_csv

router = APIRouter(prefix="/jobs", tags=["jobs"])


# ── POST /jobs/upload ────────────────────────────────────────────────────────

@router.post("/upload", response_model=JobCreatedResponse, status_code=202)
async def upload_csv(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are accepted.")

    content_bytes = await file.read()
    if not content_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # Validate it's parseable
    try:
        import pandas as pd
        df = pd.read_csv(io.BytesIO(content_bytes), dtype=str)
        required_cols = {"txn_id", "date", "merchant", "amount", "currency", "status"}
        missing = required_cols - set(df.columns)
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"CSV missing required columns: {missing}",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid CSV: {exc}")

    job = Job(filename=file.filename, row_count_raw=len(df))
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Enqueue the background task
    process_csv.apply_async(
        args=[str(job.id), content_bytes.decode("utf-8", errors="replace"), file.filename],
        queue="transactions",
    )

    return JobCreatedResponse(
        job_id=job.id,
        status=job.status.value,
        message="Job enqueued. Poll GET /jobs/{job_id}/status for updates.",
    )


# ── GET /jobs/{job_id}/status ────────────────────────────────────────────────

@router.get("/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status(job_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    summary_out = None
    if job.status == JobStatus.COMPLETED and job.summary:
        summary_out = JobSummaryOut(
            total_spend_inr=float(job.summary.total_spend_inr),
            total_spend_usd=float(job.summary.total_spend_usd),
            top_merchants=job.summary.top_merchants,
            anomaly_count=job.summary.anomaly_count,
            narrative=job.summary.narrative,
            risk_level=job.summary.risk_level,
            category_breakdown=job.summary.category_breakdown,
        )

    return JobStatusResponse(
        job_id=job.id,
        status=job.status.value,
        filename=job.filename,
        row_count_raw=job.row_count_raw,
        row_count_clean=job.row_count_clean,
        created_at=job.created_at,
        completed_at=job.completed_at,
        error_message=job.error_message,
        summary=summary_out,
    )


# ── GET /jobs/{job_id}/results ───────────────────────────────────────────────

@router.get("/{job_id}/results", response_model=JobResultsResponse)
async def get_job_results(job_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=f"Job is not completed yet. Current status: {job.status.value}",
        )

    result = await db.execute(
        select(Transaction).where(Transaction.job_id == job_id)
    )
    transactions = result.scalars().all()
    anomalies = [t for t in transactions if t.is_anomaly]

    summary_out = None
    if job.summary:
        summary_out = JobSummaryOut(
            total_spend_inr=float(job.summary.total_spend_inr),
            total_spend_usd=float(job.summary.total_spend_usd),
            top_merchants=job.summary.top_merchants,
            anomaly_count=job.summary.anomaly_count,
            narrative=job.summary.narrative,
            risk_level=job.summary.risk_level,
            category_breakdown=job.summary.category_breakdown,
        )

    return JobResultsResponse(
        job_id=job_id,
        status=job.status.value,
        transactions=[TransactionOut.model_validate(t) for t in transactions],
        anomalies=[TransactionOut.model_validate(t) for t in anomalies],
        category_breakdown=job.summary.category_breakdown if job.summary else {},
        summary=summary_out,
    )


# ── GET /jobs ────────────────────────────────────────────────────────────────

@router.get("", response_model=list[JobListItem])
async def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status"),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Job).order_by(Job.created_at.desc())
    if status:
        try:
            status_enum = JobStatus(status.lower())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
        stmt = stmt.where(Job.status == status_enum)

    result = await db.execute(stmt)
    jobs = result.scalars().all()
    return [
        JobListItem(
            job_id=j.id,
            status=j.status.value,
            filename=j.filename,
            row_count_raw=j.row_count_raw,
            created_at=j.created_at,
        )
        for j in jobs
    ]
