"""Pydantic data models for the Risk Monitor Agent.

Defines transaction inputs, risk levels, flagged anomalies, webhook payloads,
and summary responses.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
import uuid
from pydantic import BaseModel, Field, field_validator


class RiskLevel(str, Enum):
    """Severity classification of detected risks."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AnomalyCategory(str, Enum):
    """Categorization of detected behavioral anomalies."""
    VELOCITY_SPIKE = "VELOCITY_SPIKE"
    OUT_OF_PATTERN_AMOUNT = "OUT_OF_PATTERN_AMOUNT"
    HIGH_RISK_MERCHANT = "HIGH_RISK_MERCHANT"
    UNUSUAL_TIME_BURST = "UNUSUAL_TIME_BURST"
    RAPID_GEO_OR_FORMAT_INCONSISTENCY = "RAPID_GEO_OR_FORMAT_INCONSISTENCY"
    STRUCTURAL_FRAUD = "STRUCTURAL_FRAUD"


class Transaction(BaseModel):
    """Incoming transaction schema as required by the specification."""
    transaction_id: str = Field(
        default_factory=lambda: f"txn_{uuid.uuid4().hex[:10]}",
        description="Unique identifier for the transaction (auto-generated if not supplied)."
    )
    amount: float = Field(
        ...,
        gt=0,
        description="Transaction monetary amount (must be positive)."
    )
    merchant: str = Field(
        ...,
        min_length=1,
        description="Name or identifier of the merchant."
    )
    timestamp: str = Field(
        ...,
        description="ISO-8601 formatted transaction timestamp (e.g. 2026-11-19T14:30:00Z)."
    )
    customer_id: str = Field(
        ...,
        min_length=1,
        description="Customer or account identifier."
    )

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp_format(cls, value: str) -> str:
        """Validate or normalize ISO timestamp strings."""
        try:
            # Parses standard ISO formats including Z and timezone offsets
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return value
        except Exception as exc:
            raise ValueError(f"Invalid ISO-8601 timestamp '{value}': {exc}")


class RiskFlag(BaseModel):
    """Details of a single suspicious transaction identified by the agent."""
    transaction_id: str
    customer_id: str
    amount: float
    merchant: str
    timestamp: str
    risk_level: RiskLevel
    anomaly_category: AnomalyCategory
    reason: str = Field(
        ...,
        description="Clear LLM reasoning explaining why this transaction is suspicious."
    )
    confidence: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Model confidence score for the detected risk."
    )


class WebhookNotificationPayload(BaseModel):
    """Payload dispatched to the risk team's external webhook."""
    incident_id: str = Field(
        default_factory=lambda: f"inc_{uuid.uuid4().hex[:12]}",
        description="Unique incident ID generated for this risk event."
    )
    customer_id: str
    alert_timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    flagged_transaction: RiskFlag
    recommended_action: str
    source_agent: str = "Strands-RiskMonitor-Agent"


class WebhookDispatchResult(BaseModel):
    """Log entry of a webhook delivery attempt."""
    dispatched: bool
    incident_id: str
    destination_url: str
    status_code: Optional[int] = None
    response_body: Optional[str] = None
    attempt_count: int = 1
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    message: str


class RiskAnalysisRequest(BaseModel):
    """API payload accepted by the agent endpoint."""
    transactions: List[Transaction] = Field(
        ...,
        min_length=1,
        description="List of transactions to evaluate."
    )
    customer_id: Optional[str] = Field(
        default=None,
        description="Optional customer scope identifier for multi-tenant routing."
    )
    webhook_url: Optional[str] = Field(
        default=None,
        description="Optional explicit webhook URL override (otherwise resolved from tenant config)."
    )


class RiskAnalysisSummary(BaseModel):
    """Comprehensive summary returned to the client after risk analysis."""
    total_transactions_analyzed: int
    total_anomalies_flagged: int
    overall_risk_level: RiskLevel
    overall_risk_score: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Normalized risk score (0-100)."
    )
    flagged_transactions: List[RiskFlag] = Field(default_factory=list)
    webhook_dispatches: List[WebhookDispatchResult] = Field(default_factory=list)
    summary_narrative: str = Field(
        ...,
        description="Executive summary of findings synthesized by the agent."
    )
    analysis_timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    agent_version: str = "1.0.0"
    model_used: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"
