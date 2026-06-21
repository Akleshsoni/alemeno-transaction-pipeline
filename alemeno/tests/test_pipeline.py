"""
Unit tests for the data cleaning and anomaly detection pipeline.
Run with: pytest tests/ -v
"""

import pytest
import pandas as pd
from app.services.cleaner import clean_dataframe, rows_needing_llm_category
from app.services.anomaly import detect_anomalies


SAMPLE_CSV = """txn_id,date,merchant,amount,currency,status,category,account_id,notes
TXN001,04-09-2024,Flipkart,10882.55,INR,SUCCESS,Shopping,ACC001,
TXN002,2024/02/05,Swiggy,$11325.79,INR,success,Food,ACC001,
TXN003,17-02-2024,Zomato,2536.35,usd,SUCCESS,Food,ACC002,SUSPICIOUS
TXN001,04-09-2024,Flipkart,10882.55,INR,SUCCESS,Shopping,ACC001,
TXN004,01-01-2024,Ola,500.00,inr,failed,,ACC002,
,15-03-2024,Amazon,8000.00,INR,PENDING,,ACC001,
TXN_BIG,2024-07-15,IRCTC,193647.29,INR,SUCCESS,,ACC002,
"""


@pytest.fixture
def sample_df():
    import io
    df = pd.read_csv(io.StringIO(SAMPLE_CSV), dtype=str, keep_default_na=False)
    df = df.replace("", pd.NA)
    return df


def test_date_normalisation(sample_df):
    df, _ = clean_dataframe(sample_df.copy())
    assert str(df.loc[df["txn_id"] == "TXN001", "date"].values[0]) == "2024-09-04"
    assert str(df.loc[df["txn_id"] == "TXN002", "date"].values[0]) == "2024-02-05"
    assert str(df.loc[df["txn_id"] == "TXN_BIG", "date"].values[0]) == "2024-07-15"


def test_amount_strips_dollar(sample_df):
    df, _ = clean_dataframe(sample_df.copy())
    swiggy_row = df[df["txn_id"] == "TXN002"]
    assert float(swiggy_row["amount"].values[0]) == pytest.approx(11325.79)


def test_currency_uppercased(sample_df):
    df, _ = clean_dataframe(sample_df.copy())
    assert all(df["currency"].dropna().str.isupper())


def test_status_uppercased(sample_df):
    df, _ = clean_dataframe(sample_df.copy())
    valid = {"SUCCESS", "FAILED", "PENDING"}
    assert set(df["status"].dropna().unique()).issubset(valid)


def test_exact_duplicates_removed(sample_df):
    df, raw = clean_dataframe(sample_df.copy())
    # TXN001 appears twice
    assert raw == 7
    assert len(df[df["txn_id"] == "TXN001"]) == 1


def test_missing_category_detected(sample_df):
    df, _ = clean_dataframe(sample_df.copy())
    needs = rows_needing_llm_category(df)
    # TXN004, blank txn_id row, TXN_BIG all have no category
    assert len(needs) >= 3


def test_anomaly_statistical(sample_df):
    df, _ = clean_dataframe(sample_df.copy())
    df = detect_anomalies(df)
    # TXN_BIG (193647.29) should be flagged as outlier for ACC002
    big_row = df[df["txn_id"] == "TXN_BIG"]
    assert big_row["is_anomaly"].values[0] is True


def test_anomaly_currency_mismatch(sample_df):
    df, _ = clean_dataframe(sample_df.copy())
    df = detect_anomalies(df)
    # TXN003: Zomato with USD → domestic merchant mismatch
    zomato = df[df["txn_id"] == "TXN003"]
    assert zomato["is_anomaly"].values[0] is True
    assert "domestic" in zomato["anomaly_reason"].values[0].lower()


def test_no_false_positive_normal_txn(sample_df):
    df, _ = clean_dataframe(sample_df.copy())
    df = detect_anomalies(df)
    flipkart = df[df["txn_id"] == "TXN001"]
    # Normal INR transaction with normal amount shouldn't be flagged
    assert flipkart["is_anomaly"].values[0] is False
