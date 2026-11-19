"""AgentCore Runtime and REST API Service for CloudiQS Risk Monitor Agent.

Complies strictly with the Amazon Bedrock AgentCore Runtime specification:
- POST /invocations (AgentCore microservice invocation contract)
- GET /ping (MicroVM container health and readiness probe)
- POST /api/v1/analyze-risk (RESTful client endpoint)
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict
from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from app.agent import risk_monitor_agent
from app.config import settings
from app.models import RiskAnalysisRequest, RiskAnalysisSummary, Transaction

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("risk_monitor.runtime")

# Initialize FastAPI app
app = FastAPI(
    title="CloudiQS Risk Monitor Agent",
    description="Enterprise Anomaly Detection AI Agent built with Strands Agents & AWS Bedrock",
    version=settings.agent_version,
    docs_url="/docs",
    redoc_url="/redoc"
)

# Enable CORS for web portals and dashboards
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/ping", tags=["AgentCore System"])
@app.get("/healthz", tags=["System"])
def health_check() -> Dict[str, Any]:
    """Bedrock AgentCore /ping health probe and readiness check."""
    return {
        "status": "Healthy",
        "agent": "Strands-RiskMonitor",
        "version": settings.agent_version,
        "mode": settings.execution_mode,
        "region": settings.aws_region,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.post("/invocations", tags=["AgentCore Runtime"])
async def agentcore_invocations(
    request: Request,
    x_customer_id: str | None = Header(default=None, alias="X-Customer-ID")
) -> Dict[str, Any]:
    """Standard Amazon Bedrock AgentCore Runtime /invocations endpoint.

    Accepts payload formatted as either:
    1. Standard RiskAnalysisRequest: {"transactions": [...], "customer_id": "...", "webhook_url": "..."}
    2. Bedrock AgentCore prompt schema: {"prompt": "...", "transactions": [...]}
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Malformed JSON body: {exc}"
        )

    # Extract transactions from either top-level or nested input
    transactions_raw = body.get("transactions", [])
    if not transactions_raw and "input" in body and isinstance(body["input"], dict):
        transactions_raw = body["input"].get("transactions", [])

    if not transactions_raw:
        raise HTTPException(
            status_code=422,
            detail="Payload must contain a non-empty 'transactions' list."
        )

    customer_id = body.get("customer_id") or x_customer_id
    webhook_url = body.get("webhook_url")

    try:
        parsed_request = RiskAnalysisRequest(
            transactions=[Transaction(**t) for t in transactions_raw],
            customer_id=customer_id,
            webhook_url=webhook_url
        )
    except ValidationError as val_err:
        raise HTTPException(
            status_code=422,
            detail=val_err.errors()
        )

    summary: RiskAnalysisSummary = risk_monitor_agent.analyze(parsed_request)
    return summary.model_dump()


@app.post(
    "/api/v1/analyze-risk",
    response_model=RiskAnalysisSummary,
    status_code=status.HTTP_200_OK,
    tags=["Risk Monitoring"]
)
def analyze_risk_endpoint(
    payload: RiskAnalysisRequest,
    x_customer_id: str | None = Header(default=None, alias="X-Customer-ID")
) -> RiskAnalysisSummary:
    """REST API endpoint to evaluate transaction batches for anomalous risk patterns."""
    if x_customer_id and not payload.customer_id:
        payload.customer_id = x_customer_id

    return risk_monitor_agent.analyze(payload)


def run_server() -> None:
    """Launch the AgentCore web runtime using Uvicorn."""
    import uvicorn
    logger.info("Starting CloudiQS Risk Monitor on port %d...", settings.port)
    uvicorn.run(app, host="0.0.0.0", port=settings.port, access_log=True)


if __name__ == "__main__":
    run_server()
