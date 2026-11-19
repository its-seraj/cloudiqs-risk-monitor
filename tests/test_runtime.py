"""Integration tests for the AgentCore Runtime and REST API endpoints."""

from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
import httpx
import pytest

from app.runtime import app
from app.tools import clear_dispatch_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_state():
    clear_dispatch_store()
    yield
    clear_dispatch_store()


def test_agentcore_ping_health():
    """Verify Bedrock AgentCore /ping health check returns 200 OK and Healthy status."""
    response = client.get("/ping")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "Healthy"
    assert data["agent"] == "Strands-RiskMonitor"
    assert "version" in data


def test_agentcore_invocations_endpoint():
    """Verify POST /invocations processes transactions batch compliant with AgentCore contract."""
    payload = {
        "customer_id": "customer_alpha",
        "transactions": [
            {
                "amount": 55.0,
                "merchant": "Trader Joe's",
                "timestamp": "2026-11-19T10:00:00Z",
                "customer_id": "customer_alpha"
            }
        ]
    }
    response = client.post("/invocations", json=payload)
    assert response.status_code == 200
    result = response.json()
    assert result["total_transactions_analyzed"] == 1
    assert result["overall_risk_level"] == "LOW"
    assert result["total_anomalies_flagged"] == 0


def test_agentcore_invocations_invalid_payload():
    """Verify POST /invocations rejects empty or malformed transactions."""
    response = client.post("/invocations", json={"transactions": []})
    assert response.status_code == 422


def test_rest_analyze_risk_endpoint():
    """Verify POST /api/v1/analyze-risk endpoint flags high-risk transactions."""
    with patch("httpx.Client.post") as mock_post:
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.text = '{"status": "received"}'
        mock_post.return_value = mock_resp

        payload = {
            "customer_id": "customer_alpha",
            "transactions": [
                {
                    "amount": 9200.0,
                    "merchant": "VIP Casino Bellagio",
                    "timestamp": "2026-11-19T03:30:00Z",
                    "customer_id": "customer_alpha"
                }
            ]
        }
        response = client.post(
            "/api/v1/analyze-risk",
            json=payload,
            headers={"X-Customer-ID": "customer_alpha"}
        )
        assert response.status_code == 200
        result = response.json()
        assert result["total_anomalies_flagged"] == 1
        assert result["overall_risk_level"] in ("HIGH", "CRITICAL")
        assert len(result["webhook_dispatches"]) == 1
