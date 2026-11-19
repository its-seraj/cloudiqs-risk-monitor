"""Unit tests for the Risk Monitor Agent anomaly detection reasoning."""

import pytest
from app.models import (
    Transaction,
    RiskAnalysisRequest,
    RiskLevel,
    AnomalyCategory
)
from app.agent import RiskMonitorAgent
from app.tools import clear_dispatch_store


@pytest.fixture
def agent():
    """Agent instance for tests."""
    return RiskMonitorAgent()


@pytest.fixture(autouse=True)
def clean_state():
    clear_dispatch_store()
    yield
    clear_dispatch_store()


def test_transaction_model_validation():
    """Verify transaction fields and timestamp validation."""
    txn = Transaction(
        amount=120.50,
        merchant="Target",
        timestamp="2026-11-19T12:00:00Z",
        customer_id="cust_101"
    )
    assert txn.amount == 120.50
    assert txn.transaction_id.startswith("txn_")

    # Negative amount should fail validation
    with pytest.raises(ValueError):
        Transaction(
            amount=-50.0,
            merchant="Target",
            timestamp="2026-11-19T12:00:00Z",
            customer_id="cust_101"
        )


def test_benign_transactions_not_flagged(agent):
    """Verify normal transactions are classified as LOW risk and produce no alerts."""
    txns = [
        Transaction(
            amount=42.15,
            merchant="Whole Foods Market",
            timestamp="2026-11-19T12:00:00Z",
            customer_id="cust_101"
        ),
        Transaction(
            amount=15.99,
            merchant="Netflix Subscription",
            timestamp="2026-11-19T14:30:00Z",
            customer_id="cust_101"
        )
    ]
    req = RiskAnalysisRequest(transactions=txns)
    summary = agent.analyze(req)

    assert summary.total_transactions_analyzed == 2
    assert summary.total_anomalies_flagged == 0
    assert summary.overall_risk_level == RiskLevel.LOW
    assert len(summary.flagged_transactions) == 0


def test_out_of_pattern_amount_flagged(agent):
    """Verify an unusually large transaction ($7,500) triggers anomaly flag."""
    txns = [
        Transaction(
            amount=7500.0,
            merchant="Luxury Jewelers Ltd",
            timestamp="2026-11-19T15:00:00Z",
            customer_id="cust_102"
        )
    ]
    req = RiskAnalysisRequest(transactions=txns)
    summary = agent.analyze(req)

    assert summary.total_anomalies_flagged == 1
    assert summary.overall_risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    flag = summary.flagged_transactions[0]
    assert flag.anomaly_category == AnomalyCategory.OUT_OF_PATTERN_AMOUNT
    assert "threshold" in flag.reason.lower() or "$7,500" in flag.reason


def test_high_risk_merchant_flagged(agent):
    """Verify transactions with high-risk entities (Casino, Crypto) trigger anomaly flag."""
    txns = [
        Transaction(
            amount=1800.0,
            merchant="Binance Crypto Exchange",
            timestamp="2026-11-19T16:00:00Z",
            customer_id="cust_103"
        )
    ]
    req = RiskAnalysisRequest(transactions=txns)
    summary = agent.analyze(req)

    assert summary.total_anomalies_flagged == 1
    flag = summary.flagged_transactions[0]
    assert flag.anomaly_category == AnomalyCategory.HIGH_RISK_MERCHANT
    assert "binance" in flag.reason.lower() or "crypto" in flag.reason.lower()


def test_velocity_spike_anomaly_flagged(agent):
    """Verify rapid succession transactions (< 5 mins) for same customer trigger velocity flag."""
    txns = [
        Transaction(
            transaction_id="txn_v1",
            amount=499.0,
            merchant="Electronics Express 1",
            timestamp="2026-11-19T14:00:00Z",
            customer_id="cust_velocity"
        ),
        Transaction(
            transaction_id="txn_v2",
            amount=499.0,
            merchant="Electronics Express 2",
            timestamp="2026-11-19T14:02:30Z",  # 2.5 minutes later
            customer_id="cust_velocity"
        )
    ]
    req = RiskAnalysisRequest(transactions=txns)
    summary = agent.analyze(req)

    assert summary.total_anomalies_flagged >= 1
    flag_categories = [f.anomaly_category for f in summary.flagged_transactions]
    assert AnomalyCategory.VELOCITY_SPIKE in flag_categories
