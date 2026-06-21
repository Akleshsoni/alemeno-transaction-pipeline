from __future__ import annotations
import uuid
from datetime import date, datetime
from typing import Any
from pydantic import BaseModel, ConfigDict


# ── Job schemas ──────────────────────────────────────────────────────────────

class JobCreatedResponse(BaseModel):
    job_id: uuid.UUID
    status: str
    message: str


class JobSummaryOut(BaseModel):
    total_spend_inr: float
    total_spend_usd: float
    top_merchants: list[str]
    anomaly_count: int
    narrative: str | None
    risk_level: str | None
    category_breakdown: dict[str, float]


class JobStatusResponse(BaseModel):
    job_id: uuid.UUID
    status: str
    filename: str
    row_count_raw: int
    row_count_clean: int
    created_at: datetime
    completed_at: datetime | None
    error_message: str | None
    summary: JobSummaryOut | None = None


class JobListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    job_id: uuid.UUID
    status: str
    filename: str
    row_count_raw: int
    created_at: datetime


# ── Transaction schemas ───────────────────────────────────────────────────────

class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    txn_id: str | None
    date: date | None
    merchant: str | None
    amount: float | None
    currency: str | None
    status: str | None
    category: str | None
    account_id: str | None
    notes: str | None
    is_anomaly: bool
    anomaly_reason: str | None
    llm_category: str | None
    llm_failed: bool


# ── Results schema ────────────────────────────────────────────────────────────

class JobResultsResponse(BaseModel):
    job_id: uuid.UUID
    status: str
    transactions: list[TransactionOut]
    anomalies: list[TransactionOut]
    category_breakdown: dict[str, Any]
    summary: JobSummaryOut | None
