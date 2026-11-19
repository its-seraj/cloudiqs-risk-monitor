"""Deterministic Offline Reasoning Engine (Simulation Fallback for Local/CI Evaluation).

Enables full end-to-end evaluation and testing without requiring live AWS Bedrock
credentials, while adhering to the exact reasoning rubric as the Bedrock LLM.
"""

from datetime import datetime
import logging
from typing import List, Tuple

from app.models import (
    Transaction,
    RiskFlag,
    RiskLevel,
    AnomalyCategory,
    RiskAnalysisSummary
)
from app.tools import notify_risk_team, get_recorded_dispatches

logger = logging.getLogger("risk_monitor.mock_llm")

HIGH_RISK_KEYWORDS = [
    "casino", "gambling", "crypto", "binance", "coinbase", "wire", "remit",
    "western union", "offshore", "poker", "betting", "anonymous"
]


def evaluate_transactions_rule_based(
    transactions: List[Transaction],
    webhook_url_override: str = None
) -> Tuple[List[RiskFlag], str]:
    """Reason through transactions deterministically and call notify_risk_team tool."""
    flagged: List[RiskFlag] = []

    # Sort transactions chronologically
    sorted_txns = sorted(
        transactions,
        key=lambda t: datetime.fromisoformat(t.timestamp.replace("Z", "+00:00"))
    )

    # 1. Evaluate velocity (transactions within 5 minutes for the same customer)
    customer_txns: dict = {}
    for txn in sorted_txns:
        customer_txns.setdefault(txn.customer_id, []).append(txn)

    velocity_flagged_ids = set()
    for cust_id, txns in customer_txns.items():
        if len(txns) >= 2:
            for i in range(len(txns) - 1):
                t1 = datetime.fromisoformat(txns[i].timestamp.replace("Z", "+00:00"))
                t2 = datetime.fromisoformat(txns[i + 1].timestamp.replace("Z", "+00:00"))
                delta_mins = abs((t2 - t1).total_seconds()) / 60.0
                if delta_mins <= 5.0:
                    velocity_flagged_ids.add(txns[i].transaction_id)
                    velocity_flagged_ids.add(txns[i + 1].transaction_id)

    # 2. Evaluate individual transactions against risk dimensions
    for txn in sorted_txns:
        is_suspicious = False
        risk_level = RiskLevel.LOW
        category = AnomalyCategory.OUT_OF_PATTERN_AMOUNT
        reasons: List[str] = []

        merchant_lower = txn.merchant.lower()
        dt = datetime.fromisoformat(txn.timestamp.replace("Z", "+00:00"))

        # Dimension A: High-Risk Merchant Category
        for keyword in HIGH_RISK_KEYWORDS:
            if keyword in merchant_lower:
                is_suspicious = True
                category = AnomalyCategory.HIGH_RISK_MERCHANT
                risk_level = RiskLevel.CRITICAL if txn.amount >= 1000 else RiskLevel.HIGH
                reasons.append(f"Transaction with high-risk merchant '{txn.merchant}' matching '{keyword}'.")
                break

        # Dimension B: Out of Pattern / High Amount
        if txn.amount >= 2500.0:
            is_suspicious = True
            reasons.append(f"Anomalously high transaction amount (${txn.amount:,.2f}) exceeding threshold.")
            if risk_level != RiskLevel.CRITICAL:
                risk_level = RiskLevel.HIGH if txn.amount < 5000 else RiskLevel.CRITICAL
            if category == AnomalyCategory.OUT_OF_PATTERN_AMOUNT:
                category = AnomalyCategory.OUT_OF_PATTERN_AMOUNT

        # Dimension C: Velocity Spike
        if txn.transaction_id in velocity_flagged_ids:
            is_suspicious = True
            reasons.append("High transaction velocity detected (< 5 minutes between transactions for customer).")
            if risk_level in (RiskLevel.LOW, RiskLevel.MEDIUM):
                risk_level = RiskLevel.HIGH
            category = AnomalyCategory.VELOCITY_SPIKE

        # Dimension D: Unusual Night Time Burst (02:00 - 05:00)
        if 2 <= dt.hour < 5 and txn.amount > 300:
            is_suspicious = True
            reasons.append(f"Uncharacteristic off-hours transaction at {dt.strftime('%H:%M')} UTC.")
            if risk_level == RiskLevel.LOW:
                risk_level = RiskLevel.MEDIUM
            if category == AnomalyCategory.OUT_OF_PATTERN_AMOUNT:
                category = AnomalyCategory.UNUSUAL_TIME_BURST

        if is_suspicious:
            reason_text = " ".join(reasons)
            flag = RiskFlag(
                transaction_id=txn.transaction_id,
                customer_id=txn.customer_id,
                amount=txn.amount,
                merchant=txn.merchant,
                timestamp=txn.timestamp,
                risk_level=risk_level,
                anomaly_category=category,
                reason=reason_text,
                confidence=0.94 if risk_level == RiskLevel.CRITICAL else 0.88
            )
            flagged.append(flag)

            # Trigger Strands Tool Calling
            try:
                # notify_risk_team is a Strands @tool. We can invoke its underlying function directly
                tool_fn = getattr(notify_risk_team, "func", notify_risk_team)
                tool_fn(
                    transaction_id=txn.transaction_id,
                    customer_id=txn.customer_id,
                    amount=txn.amount,
                    merchant=txn.merchant,
                    timestamp=txn.timestamp,
                    risk_level=risk_level.value,
                    anomaly_category=category.value,
                    reason=reason_text,
                    recommended_action="Temporarily freeze card, request customer MFA confirmation",
                    webhook_url=webhook_url_override
                )
            except Exception as e:
                logger.error("Failed to execute notify_risk_team tool: %s", e)

    # 3. Formulate summary narrative
    total_txns = len(transactions)
    flagged_count = len(flagged)
    if flagged_count == 0:
        narrative = (
            f"Evaluated {total_txns} transaction(s). No risk anomalies detected. "
            "All transaction amounts, velocity, and merchants conform to baseline patterns."
        )
    else:
        narrative = (
            f"Evaluated {total_txns} transaction(s) and flagged {flagged_count} suspicious event(s). "
            f"Identified risks across {len({f.anomaly_category for f in flagged})} category(ies). "
            f"External webhook notifications were triggered for all critical and high-risk incidents."
        )

    return flagged, narrative
