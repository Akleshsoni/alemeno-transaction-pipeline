"""
Data cleaning pipeline.
Handles: mixed date formats, $ prefix in amounts, currency casing,
         status casing, missing categories, and exact duplicate removal.
"""

import re
import hashlib
from datetime import date
from typing import Any
import pandas as pd


DATE_FORMATS = [
    "%d-%m-%Y",    # 04-09-2024
    "%Y/%m/%d",    # 2024/02/05
    "%Y-%m-%d",    # 2024-07-15 (already ISO)
]

VALID_CATEGORIES = {
    "Food", "Shopping", "Travel", "Transport",
    "Utilities", "Cash Withdrawal", "Entertainment", "Other",
}

VALID_STATUSES = {"SUCCESS", "FAILED", "PENDING"}


def _parse_date(raw: Any) -> date | None:
    if pd.isna(raw) or not str(raw).strip():
        return None
    s = str(raw).strip()
    for fmt in DATE_FORMATS:
        try:
            return pd.to_datetime(s, format=fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _clean_amount(raw: Any) -> float | None:
    if pd.isna(raw):
        return None
    cleaned = re.sub(r"[^\d.]", "", str(raw))
    try:
        return float(cleaned)
    except ValueError:
        return None


def _row_fingerprint(row: dict) -> str:
    """SHA-256 of the key fields for exact-duplicate detection."""
    key = "|".join(str(row.get(f, "")) for f in [
        "txn_id", "date", "merchant", "amount", "currency", "status", "account_id"
    ])
    return hashlib.sha256(key.encode()).hexdigest()


def clean_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """
    Returns (cleaned_df, raw_row_count).
    The returned df has clean, normalised values.
    """
    raw_count = len(df)

    # ── 1. Normalise date ────────────────────────────────────────────────────
    df["date"] = df["date"].apply(_parse_date)

    # ── 2. Strip currency symbols from amount ───────────────────────────────
    df["amount"] = df["amount"].apply(_clean_amount)

    # ── 3. Uppercase currency + status ──────────────────────────────────────
    df["currency"] = df["currency"].str.strip().str.upper()
    df["status"] = df["status"].str.strip().str.upper()
    df.loc[~df["status"].isin(VALID_STATUSES), "status"] = None

    # ── 4. Fill missing txn_id with None string marker ──────────────────────
    df["txn_id"] = df["txn_id"].where(df["txn_id"].notna() & (df["txn_id"].str.strip() != ""), other=None)

    # ── 5. Fill missing categories ──────────────────────────────────────────
    df["category"] = df["category"].where(
        df["category"].notna() & (df["category"].str.strip() != ""),
        other=None,          # mark as None so LLM can classify
    )

    # ── 6. Remove exact duplicates ──────────────────────────────────────────
    df["_fp"] = df.apply(_row_fingerprint, axis=1)
    df = df.drop_duplicates(subset=["_fp"]).drop(columns=["_fp"])

    df = df.reset_index(drop=True)
    return df, raw_count


def rows_needing_llm_category(df: pd.DataFrame) -> list[int]:
    """Return indices where category is blank (LLM needs to classify)."""
    return df[df["category"].isna()].index.tolist()
