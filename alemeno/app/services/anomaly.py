"""
Anomaly detection:
  1. Statistical outlier: amount > ANOMALY_MULTIPLIER * per-account median
  2. Currency mismatch: USD transaction with domestic-only merchant
"""

import pandas as pd
from app.core.config import get_settings

settings = get_settings()


def detect_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["is_anomaly"] = False
    df["anomaly_reason"] = None

    # ── 1. Statistical outlier per account ──────────────────────────────────
    medians = (
        df[df["amount"].notna()]
        .groupby("account_id")["amount"]
        .median()
    )

    for idx, row in df.iterrows():
        if pd.isna(row["amount"]) or pd.isna(row["account_id"]):
            continue
        median = medians.get(row["account_id"])
        if median and row["amount"] > settings.ANOMALY_MULTIPLIER * median:
            df.at[idx, "is_anomaly"] = True
            df.at[idx, "anomaly_reason"] = (
                f"Amount {row['amount']:.2f} exceeds "
                f"{settings.ANOMALY_MULTIPLIER}x account median ({median:.2f})"
            )

    # ── 2. Currency mismatch ─────────────────────────────────────────────────
    domestic = set(settings.DOMESTIC_MERCHANTS)
    for idx, row in df.iterrows():
        if str(row.get("currency", "")).upper() == "USD":
            merchant_lower = str(row.get("merchant", "")).lower()
            if any(d in merchant_lower for d in domestic):
                reasons = [df.at[idx, "anomaly_reason"]] if df.at[idx, "is_anomaly"] else []
                reasons.append(
                    f"USD currency used with domestic-only merchant '{row['merchant']}'"
                )
                df.at[idx, "is_anomaly"] = True
                df.at[idx, "anomaly_reason"] = "; ".join(filter(None, reasons))

    return df
