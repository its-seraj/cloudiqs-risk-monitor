"""Prompt Evaluation & Regression Gate for CI/CD.

Evaluates the agent's anomaly detection precision, recall, and false positive
rate across a curated golden benchmark dataset before allowing production deployment.
"""

from unittest.mock import patch, MagicMock
import httpx
import pytest

from app.models import Transaction, RiskAnalysisRequest
from app.agent import RiskMonitorAgent
from app.tools import clear_dispatch_store

# Golden benchmark dataset
BENCHMARK_CASES = [
    # --- 5 Ground Truth Legitimate Cases (Should NOT be flagged) ---
    {"amount": 12.50, "merchant": "Starbucks Coffee", "timestamp": "2026-11-19T08:30:00Z", "expected_flag": False},
    {"amount": 85.00, "merchant": "Shell Oil Station", "timestamp": "2026-11-19T11:15:00Z", "expected_flag": False},
    {"amount": 145.20, "merchant": "Amazon Marketplace", "timestamp": "2026-11-19T13:45:00Z", "expected_flag": False},
    {"amount": 35.00, "merchant": "Uber Trip", "timestamp": "2026-11-19T18:20:00Z", "expected_flag": False},
    {"amount": 62.10, "merchant": "CVS Pharmacy", "timestamp": "2026-11-19T19:00:00Z", "expected_flag": False},

    # --- 5 Ground Truth Fraudulent / Suspicious Cases (MUST be flagged) ---
    {"amount": 8500.00, "merchant": "Monte Carlo Casino", "timestamp": "2026-11-19T03:10:00Z", "expected_flag": True},
    {"amount": 3200.00, "merchant": "Coinbase Instant Buy", "timestamp": "2026-11-19T15:00:00Z", "expected_flag": True},
    {"amount": 9500.00, "merchant": "High-Value Wire Remittance", "timestamp": "2026-11-19T14:00:00Z", "expected_flag": True},
    {"amount": 5400.00, "merchant": "Luxury Watch Boutique", "timestamp": "2026-11-19T17:30:00Z", "expected_flag": True},
    {"amount": 2800.00, "merchant": "Offshore Betting Services", "timestamp": "2026-11-19T04:00:00Z", "expected_flag": True},
]


def test_prompt_eval_gate_precision_and_recall():
    """Run regression test on golden dataset to enforce model accuracy gates."""
    clear_dispatch_store()
    agent = RiskMonitorAgent()

    true_positives = 0
    false_positives = 0
    true_negatives = 0
    false_negatives = 0

    with patch("httpx.Client.post") as mock_post:
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.text = '{"status": "received"}'
        mock_post.return_value = mock_resp

        for idx, case in enumerate(BENCHMARK_CASES):
            clear_dispatch_store()
            txn = Transaction(
                transaction_id=f"eval_txn_{idx}",
                amount=case["amount"],
                merchant=case["merchant"],
                timestamp=case["timestamp"],
                customer_id="eval_customer"
            )
            req = RiskAnalysisRequest(transactions=[txn])
            summary = agent.analyze(req)
            was_flagged = summary.total_anomalies_flagged > 0
            expected = case["expected_flag"]

            if was_flagged and expected:
                true_positives += 1
            elif was_flagged and not expected:
                false_positives += 1
            elif not was_flagged and not expected:
                true_negatives += 1
            elif not was_flagged and expected:
                false_negatives += 1

    total_actual_positives = true_positives + false_negatives
    total_actual_negatives = true_negatives + false_positives

    recall = true_positives / total_actual_positives if total_actual_positives > 0 else 0.0
    precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) > 0 else 0.0
    false_positive_rate = false_positives / total_actual_negatives if total_actual_negatives > 0 else 0.0

    print(
        f"\n[EVAL GATE RESULTS] TP: {true_positives}, FP: {false_positives}, "
        f"TN: {true_negatives}, FN: {false_negatives} | "
        f"Recall: {recall:.2%}, Precision: {precision:.2%}, FPR: {false_positive_rate:.2%}"
    )

    # Production gate assertions
    assert recall >= 0.90, f"Recall {recall:.2%} dropped below acceptable threshold (90%)!"
    assert false_positive_rate <= 0.10, f"False Positive Rate {false_positive_rate:.2%} exceeded maximum allowed (10%)!"
    assert precision >= 0.90, f"Precision {precision:.2%} dropped below minimum allowed (90%)!"
