"""Mock Webhook Receiver Server.

Simulates the external Risk Operations / SIEM webhook endpoint for local
development, Docker Compose testing, and CI evaluation.
"""

from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
from typing import Any, Dict, List
from fastapi import FastAPI, Header, HTTPException, Request, Response, status
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MOCK-WEBHOOK] %(levelname)s: %(message)s"
)
logger = logging.getLogger("mock_webhook")

app = FastAPI(
    title="CloudiQS Mock Webhook Receiver",
    description="Captures and verifies incoming risk alerts dispatched by the agent."
)

received_alerts: List[Dict[str, Any]] = []


@app.get("/ping")
@app.get("/healthz")
def health() -> Dict[str, str]:
    """Health check endpoint."""
    return {"status": "Mock Webhook Receiver Healthy"}


@app.get("/webhook/recent-alerts")
def get_recent_alerts(limit: int = 20) -> List[Dict[str, Any]]:
    """Retrieve captured alerts for verification."""
    return received_alerts[-limit:]


@app.delete("/webhook/clear")
def clear_alerts() -> Dict[str, str]:
    """Clear recorded alerts store."""
    received_alerts.clear()
    return {"status": "Alerts store cleared"}


@app.post("/webhook/{path:path}")
async def receive_webhook(
    path: str,
    request: Request,
    x_signature_sha256: str | None = Header(default=None, alias="X-Signature-SHA256"),
    x_customer_id: str | None = Header(default=None, alias="X-Customer-ID"),
    x_incident_id: str | None = Header(default=None, alias="X-Incident-ID"),
) -> Dict[str, Any]:
    """Capture incoming risk notifications dispatched by the agent."""
    raw_body = await request.body()
    try:
        data = json.loads(raw_body.decode("utf-8"))
    except Exception:
        data = {"raw_payload": raw_body.decode("utf-8", errors="ignore")}

    alert_record = {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "path": f"/webhook/{path}",
        "customer_id": x_customer_id or data.get("customer_id"),
        "incident_id": x_incident_id or data.get("incident_id"),
        "signature_header": x_signature_sha256,
        "payload": data
    }

    received_alerts.append(alert_record)

    logger.info(
        "ALERT RECEIVED on /webhook/%s | Customer: %s | Incident: %s",
        path, alert_record["customer_id"], alert_record["incident_id"]
    )

    return {
        "status": "received",
        "incident_id": alert_record["incident_id"],
        "received_at": alert_record["received_at"],
        "message": f"Risk alert successfully ingested for incident {alert_record['incident_id']}."
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8081)
