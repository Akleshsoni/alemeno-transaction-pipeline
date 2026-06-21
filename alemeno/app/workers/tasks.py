"""
Core processing pipeline task.
Executed by the Celery worker asynchronously.
Steps: clean → anomaly detect → LLM classify → LLM narrative → persist
"""

import logging
import uuid
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.workers.celery_app import celery_app
from app.core.config import get_settings
from app.models.job import Job, JobStatus
from app.models.transaction import Transaction, JobSummary
from app.services.cleaner import clean_dataframe, rows_needing_llm_category
from app.services.anomaly import detect_anomalies
from app.services.llm import classify_categories_batch, generate_narrative

logger = logging.getLogger(__name__)
settings = get_settings()


def _get_sync_session() -> Session:
    engine = create_engine(settings.SYNC_DATABASE_URL, pool_pre_ping=True)
    return Session(engine)


@celery_app.task(bind=True, name="app.workers.tasks.process_csv", max_retries=0)
def process_csv(self, job_id: str, csv_content: str, filename: str):
    """
    Full async processing pipeline for a single job.
    Uses synchronous SQLAlchemy inside Celery (no asyncio in worker).
    """
    db: Session = _get_sync_session()
    job_uuid = uuid.UUID(job_id)

    try:
        # ── Mark job as processing ───────────────────────────────────────────
        job: Job = db.get(Job, job_uuid)
        if not job:
            logger.error("Job %s not found", job_id)
            return
        job.status = JobStatus.PROCESSING
        db.commit()

        # ── a) Load & clean ──────────────────────────────────────────────────
        import io
        df = pd.read_csv(io.StringIO(csv_content), dtype=str, keep_default_na=False)
        # Replace empty strings with NaN
        df = df.replace("", pd.NA)
        df, raw_count = clean_dataframe(df)

        # ── b) Anomaly detection ─────────────────────────────────────────────
        df = detect_anomalies(df)

        # ── c) LLM category classification ───────────────────────────────────
        needs_category = rows_needing_llm_category(df)
        llm_failed_indices: set[int] = set()

        if needs_category:
            batch_input = [
                {
                    "_idx": str(idx),
                    "merchant": df.at[idx, "merchant"],
                    "amount": df.at[idx, "amount"],
                    "currency": df.at[idx, "currency"],
                    "notes": df.at[idx, "notes"] if pd.notna(df.at[idx, "notes"]) else "",
                }
                for idx in needs_category
            ]
            category_map = classify_categories_batch(batch_input)
            for idx in needs_category:
                result = category_map.get(str(idx), "__FAILED__")
                if result == "__FAILED__":
                    llm_failed_indices.add(idx)
                    df.at[idx, "category"] = "Uncategorised"
                else:
                    df.at[idx, "llm_category"] = result
                    df.at[idx, "category"] = result

        # ── d) Build category spend breakdown ─────────────────────────────────
        cat_breakdown: dict[str, float] = {}
        for _, row in df.iterrows():
            cat = str(row.get("category") or "Uncategorised")
            amt = float(row["amount"]) if pd.notna(row.get("amount")) else 0.0
            cat_breakdown[cat] = cat_breakdown.get(cat, 0.0) + amt

        # Top 3 merchants
        merchant_spend: dict[str, float] = {}
        for _, row in df.iterrows():
            m = str(row.get("merchant") or "Unknown")
            amt = float(row["amount"]) if pd.notna(row.get("amount")) else 0.0
            merchant_spend[m] = merchant_spend.get(m, 0.0) + amt
        top_merchants = sorted(merchant_spend, key=merchant_spend.get, reverse=True)[:3]

        total_inr = float(df[df["currency"] == "INR"]["amount"].sum())
        total_usd = float(df[df["currency"] == "USD"]["amount"].sum())
        anomaly_count = int(df["is_anomaly"].sum())

        # ── e) LLM narrative ──────────────────────────────────────────────────
        narrative_data = {
            "total_spend_inr": total_inr,
            "total_spend_usd": total_usd,
            "top_merchants": top_merchants,
            "anomaly_count": anomaly_count,
            "category_breakdown": cat_breakdown,
            "suspicious_notes_count": int(
                df["notes"].str.contains("SUSPICIOUS", na=False).sum()
            ),
        }
        llm_summary = generate_narrative(narrative_data)

        # ── f) Persist transactions ───────────────────────────────────────────
        for idx, row in df.iterrows():
            txn = Transaction(
                job_id=job_uuid,
                txn_id=row.get("txn_id") if pd.notna(row.get("txn_id")) else None,
                date=row.get("date"),
                merchant=row.get("merchant"),
                amount=float(row["amount"]) if pd.notna(row.get("amount")) else None,
                currency=row.get("currency"),
                status=row.get("status"),
                category=row.get("category"),
                account_id=row.get("account_id"),
                notes=row.get("notes") if pd.notna(row.get("notes")) else None,
                is_anomaly=bool(row.get("is_anomaly", False)),
                anomaly_reason=row.get("anomaly_reason") if row.get("is_anomaly") else None,
                llm_category=row.get("llm_category"),
                llm_failed=idx in llm_failed_indices,
            )
            db.add(txn)

        # ── g) Persist summary ────────────────────────────────────────────────
        summary = JobSummary(
            job_id=job_uuid,
            total_spend_inr=llm_summary.get("total_spend_inr", total_inr),
            total_spend_usd=llm_summary.get("total_spend_usd", total_usd),
            top_merchants=llm_summary.get("top_merchants", top_merchants),
            anomaly_count=llm_summary.get("anomaly_count", anomaly_count),
            narrative=llm_summary.get("narrative"),
            risk_level=llm_summary.get("risk_level", "medium"),
            category_breakdown=cat_breakdown,
        )
        db.add(summary)

        # ── h) Complete job ───────────────────────────────────────────────────
        job.status = JobStatus.COMPLETED
        job.row_count_raw = raw_count
        job.row_count_clean = len(df)
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("Job %s completed: %d clean rows", job_id, len(df))

    except Exception as exc:
        logger.exception("Job %s failed: %s", job_id, exc)
        try:
            job = db.get(Job, job_uuid)
            if job:
                job.status = JobStatus.FAILED
                job.error_message = str(exc)
                db.commit()
        except Exception:
            pass
        raise
    finally:
        db.close()
