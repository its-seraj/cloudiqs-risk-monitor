"""Unit tests for Strands Risk Monitor Tools and Webhook Security."""

import hashlib
import hmac
import json
from unittest.mock import patch, MagicMock
import httpx
import pytest

from app.models import RiskLevel, AnomalyCategory
from app.tools import (
    notify_risk_team,
    generate_hmac_signature,
    dispatch_webhook_http,
    clear_dispatch_store,
    get_recorded_dispatches
)
from app.config import tenant_registry, TenantConfig


@pytest.fixture(autouse=True)
def cleanup_store():
    """Ensure clean dispatch store before each test."""
    clear_dispatch_store()
    yield
    clear_dispatch_store()


def test_hmac_signature_generation():
    """Verify HMAC-SHA256 signature generation produces cryptographic match."""
    secret = "test-secret-key-12345"
    payload = b'{"test": "value"}'
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    assert generate_hmac_signature(secret, payload) == expected


def test_notify_risk_team_success():
    """Verify notify_risk_team succeeds when HTTP POST returns 200 OK."""
    with patch("httpx.Client.post") as mock_post:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.is_success = True
        mock_response.status_code = 200
        mock_response.text = '{"status": "received"}'
        mock_post.return_value = mock_response

        # notify_risk_team is wrapped by Strands @tool. Access underlying func
        tool_fn = getattr(notify_risk_team, "func", notify_risk_team)
        result = tool_fn(
            transaction_id="txn_test_001",
            customer_id="customer_alpha",
            amount=4200.0,
            merchant="Casino Monte Carlo",
            timestamp="2026-11-19T14:00:00Z",
            risk_level="CRITICAL",
            anomaly_category="HIGH_RISK_MERCHANT",
            reason="Gambling transaction exceeding threshold",
            webhook_url="http://mock-webhook-receiver:8081/webhook/customer-alpha"
        )

        assert "SUCCESS: Alert dispatched to risk team webhook" in result
        assert "txn_test_001" in result

        dispatches = get_recorded_dispatches()
        assert len(dispatches) == 1
        assert dispatches[0].dispatched is True
        assert dispatches[0].status_code == 200
        assert dispatches[0].destination_url == "http://mock-webhook-receiver:8081/webhook/customer-alpha"


def test_notify_risk_team_retry_on_failure():
    """Verify tool retries 3 times on connection errors."""
    with patch("httpx.Client.post", side_effect=httpx.ConnectError("Connection refused")) as mock_post:
        tool_fn = getattr(notify_risk_team, "func", notify_risk_team)
        result = tool_fn(
            transaction_id="txn_test_fail",
            customer_id="customer_alpha",
            amount=1500.0,
            merchant="Crypto Offramp",
            timestamp="2026-11-19T14:00:00Z",
            risk_level="HIGH",
            anomaly_category="HIGH_RISK_MERCHANT",
            reason="Crypto purchase"
        )

        assert "WARNING: Risk team notification recorded locally, but webhook delivery failed" in result
        assert mock_post.call_count == 3  # 3 attempts made

        dispatches = get_recorded_dispatches()
        assert len(dispatches) == 1
        assert dispatches[0].dispatched is False
        assert dispatches[0].attempt_count == 3


def test_tenant_isolation_in_webhook_resolution():
    """Verify customer_alpha and customer_beta resolve distinct webhook URLs and HMAC keys."""
    tenant_alpha = tenant_registry.get_tenant("customer_alpha")
    tenant_beta = tenant_registry.get_tenant("customer_beta")

    assert tenant_alpha.webhook_url != tenant_beta.webhook_url
    assert tenant_alpha.hmac_secret != tenant_beta.hmac_secret
    assert "customer-alpha" in tenant_alpha.webhook_url
    assert "customer-beta" in tenant_beta.webhook_url
