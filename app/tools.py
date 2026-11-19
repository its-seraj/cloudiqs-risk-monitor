"""Strands Tools for the Risk Monitor Agent.

Implements the `@tool` notify_risk_team function to alert the risk operations
center via an external webhook with HMAC-SHA256 cryptographic signing and retries.
"""

from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import time
from typing import Dict, List, Optional
import uuid
import httpx
from strands import tool

from app.config import tenant_registry, settings
from app.models import (
    RiskFlag,
    RiskLevel,
    AnomalyCategory,
    WebhookDispatchResult,
    WebhookNotificationPayload
)

logger = logging.getLogger("risk_monitor.tools")

# In-memory execution registry to capture all dispatches during an agent run
_dispatches_store: List[WebhookDispatchResult] = []


def clear_dispatch_store() -> None:
    """Clear recorded dispatches (useful between agent invocations)."""
    global _dispatches_store
    _dispatches_store.clear()


def get_recorded_dispatches() -> List[WebhookDispatchResult]:
    """Retrieve all webhook dispatches recorded during the current cycle."""
    return list(_dispatches_store)


def generate_hmac_signature(secret: str, payload_bytes: bytes) -> str:
    """Compute HMAC-SHA256 hex digest for webhook security."""
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()


def dispatch_webhook_http(
    url: str,
    payload: dict,
    hmac_secret: str,
    customer_id: str,
    max_retries: int = 3,
    timeout: float = 4.0
) -> WebhookDispatchResult:
    """Deliver webhook notification over HTTP with retries and HMAC signature."""
    payload_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
    signature = generate_hmac_signature(hmac_secret, payload_bytes)
    incident_id = payload.get("incident_id", f"inc_{uuid.uuid4().hex[:10]}")

    headers = {
        "Content-Type": "application/json",
        "X-Signature-SHA256": f"sha256={signature}",
        "X-Customer-ID": customer_id,
        "X-Incident-ID": incident_id,
        "X-Event-Type": "risk.anomaly.detected",
        "User-Agent": f"CloudiQS-RiskMonitorAgent/{settings.agent_version}"
    }

    last_error = ""
    status_code: Optional[int] = None
    response_body: Optional[str] = None

    for attempt in range(1, max_retries + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, content=payload_bytes, headers=headers)
                status_code = response.status_code
                response_body = response.text[:200]

                if response.is_success:
                    logger.info("Webhook delivered successfully to %s on attempt %d", url, attempt)
                    result = WebhookDispatchResult(
                        dispatched=True,
                        incident_id=incident_id,
                        destination_url=url,
                        status_code=status_code,
                        response_body=response_body,
                        attempt_count=attempt,
                        message=f"Webhook successfully delivered to {url} (HTTP {status_code})."
                    )
                    _dispatches_store.append(result)
                    return result
                else:
                    last_error = f"HTTP {status_code}: {response_body}"
                    logger.warning("Webhook returned error status %d on attempt %d", status_code, attempt)

        except Exception as exc:
            last_error = str(exc)
            logger.warning("Webhook dispatch exception on attempt %d: %s", attempt, last_error)

        if attempt < max_retries:
            time.sleep(0.3 * (2 ** (attempt - 1)))  # Exponential backoff: 0.3s, 0.6s

    # If all attempts failed
    result = WebhookDispatchResult(
        dispatched=False,
        incident_id=incident_id,
        destination_url=url,
        status_code=status_code,
        response_body=response_body,
        attempt_count=max_retries,
        message=f"Failed to deliver webhook to {url} after {max_retries} attempts. Last error: {last_error}"
    )
    _dispatches_store.append(result)
    return result


@tool
def notify_risk_team(
    transaction_id: str,
    customer_id: str,
    amount: float,
    merchant: str,
    timestamp: str,
    risk_level: str,
    anomaly_category: str,
    reason: str,
    recommended_action: str = "Block card and contact customer for verification",
    webhook_url: Optional[str] = None
) -> str:
    """Notify the external Risk Operations team via webhook when a suspicious transaction is detected.

    Args:
        transaction_id: Unique identifier of the flagged transaction.
        customer_id: Identifier of the customer/cardholder.
        amount: Transaction amount.
        merchant: Name or category of merchant.
        timestamp: ISO-8601 transaction timestamp.
        risk_level: Risk severity ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW').
        anomaly_category: Category ('VELOCITY_SPIKE', 'OUT_OF_PATTERN_AMOUNT', 'HIGH_RISK_MERCHANT', 'UNUSUAL_TIME_BURST', 'STRUCTURAL_FRAUD').
        reason: Clear rationale explaining why the transaction is flagged.
        recommended_action: Suggested remediation for the risk team.
        webhook_url: Optional explicit webhook URL. If omitted, uses tenant configuration.

    Returns:
        Confirmation message detailing webhook delivery status and incident ID.
    """
    # Normalize risk level and category
    normalized_risk = risk_level.upper() if risk_level else "HIGH"
    if normalized_risk not in RiskLevel.__members__:
        normalized_risk = "HIGH"

    normalized_cat = anomaly_category.upper() if anomaly_category else "OUT_OF_PATTERN_AMOUNT"
    if normalized_cat not in AnomalyCategory.__members__:
        normalized_cat = "OUT_OF_PATTERN_AMOUNT"

    # Resolve tenant config for isolation
    tenant = tenant_registry.get_tenant(customer_id)
    target_url = webhook_url or tenant.webhook_url
    hmac_secret = tenant.hmac_secret

    risk_flag = RiskFlag(
        transaction_id=transaction_id,
        customer_id=customer_id,
        amount=amount,
        merchant=merchant,
        timestamp=timestamp,
        risk_level=RiskLevel(normalized_risk),
        anomaly_category=AnomalyCategory(normalized_cat),
        reason=reason,
        confidence=0.92
    )

    incident_id = f"inc_{uuid.uuid4().hex[:10]}"
    notification_payload = WebhookNotificationPayload(
        incident_id=incident_id,
        customer_id=customer_id,
        flagged_transaction=risk_flag,
        recommended_action=recommended_action,
        source_agent="Strands-RiskMonitor-Agent"
    )

    logger.info(
        "Invoking webhook for transaction %s (Customer: %s, Amount: $%.2f, Risk: %s)",
        transaction_id, customer_id, amount, normalized_risk
    )

    dispatch_res = dispatch_webhook_http(
        url=target_url,
        payload=notification_payload.model_dump(),
        hmac_secret=hmac_secret,
        customer_id=customer_id
    )

    if dispatch_res.dispatched:
        return (
            f"SUCCESS: Alert dispatched to risk team webhook at {target_url}. "
            f"Incident ID: {incident_id}. Transaction ID: {transaction_id}. "
            f"Risk Level: {normalized_risk}. Status: Delivered (HTTP {dispatch_res.status_code})."
        )
    else:
        return (
            f"WARNING: Risk team notification recorded locally, but webhook delivery failed. "
            f"Incident ID: {incident_id}. Transaction ID: {transaction_id}. "
            f"Details: {dispatch_res.message}."
        )
