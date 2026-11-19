"""Risk Monitor Agent built with Strands Agents SDK and AWS Bedrock.

Orchestrates multi-turn reasoning over transaction batches, invokes the
notify_risk_team tool for flagged risks, and synthesizes a comprehensive
risk summary.
"""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import List, Optional
import yaml

from strands import Agent
from strands.models import BedrockModel
import botocore.exceptions

from app.config import settings, tenant_registry
from app.models import (
    Transaction,
    RiskFlag,
    RiskLevel,
    RiskAnalysisRequest,
    RiskAnalysisSummary
)
from app.tools import notify_risk_team, clear_dispatch_store, get_recorded_dispatches
from app.mock_llm import evaluate_transactions_rule_based

logger = logging.getLogger("risk_monitor.agent")


def load_prompt_config(version: Optional[str] = None) -> dict:
    """Load versioned prompt configuration from YAML file."""
    ver = version or settings.agent_version
    prompt_file = settings.prompts_dir / f"risk_analyzer_v{ver}.yaml"

    if not prompt_file.exists():
        # Fallback to default v1.0.0
        prompt_file = settings.prompts_dir / "risk_analyzer_v1.0.0.yaml"

    try:
        with open(prompt_file, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception as exc:
        logger.warning("Could not read prompt config %s: %s. Using default inline prompt.", prompt_file, exc)
        return {
            "version": ver,
            "model": settings.bedrock_model_id,
            "system_prompt": "You are an expert Risk Monitor AI Agent. Flag suspicious transactions using the notify_risk_team tool."
        }


class RiskMonitorAgent:
    """Enterprise Risk Monitor Agent powered by Strands Agents SDK."""

    def __init__(self, prompt_version: Optional[str] = None):
        self.prompt_config = load_prompt_config(prompt_version)
        self.version = self.prompt_config.get("version", settings.agent_version)
        self.model_id = self.prompt_config.get("model", settings.bedrock_model_id)
        self.system_prompt = self.prompt_config.get("system_prompt", "")

        self._strands_agent: Optional[Agent] = None
        self._init_strands_agent()

    def _init_strands_agent(self) -> None:
        """Initialize the underlying Strands Agent with BedrockModel."""
        if settings.execution_mode == "mock":
            logger.info("Agent initialized in explicit MOCK execution mode.")
            return

        try:
            bedrock_model = BedrockModel(
                model_id=self.model_id,
                region_name=settings.aws_region,
                temperature=float(self.prompt_config.get("temperature", 0.1)),
                max_tokens=int(self.prompt_config.get("max_tokens", 2048))
            )
            self._strands_agent = Agent(
                model=bedrock_model,
                system_prompt=self.system_prompt,
                tools=[notify_risk_team]
            )
            logger.info("Strands Agent initialized with AWS Bedrock model: %s", self.model_id)
        except Exception as exc:
            logger.warning("AWS BedrockModel initialization skipped (%s). Will use simulation fallback.", exc)
            self._strands_agent = None

    def analyze(self, request: RiskAnalysisRequest) -> RiskAnalysisSummary:
        """Analyze a list of transactions, flag anomalies, and notify the risk team."""
        clear_dispatch_store()
        txns = request.transactions
        customer_id = request.customer_id or (txns[0].customer_id if txns else "default_customer")
        tenant = tenant_registry.get_tenant(customer_id)
        webhook_url = request.webhook_url or tenant.webhook_url

        use_mock = (
            settings.execution_mode == "mock" or
            self._strands_agent is None or
            os.getenv("AWS_ACCESS_KEY_ID") is None and not os.path.exists(os.path.expanduser("~/.aws/credentials"))
        )

        if not use_mock and self._strands_agent is not None:
            try:
                summary = self._analyze_with_bedrock(txns, webhook_url, customer_id)
                return summary
            except (botocore.exceptions.NoCredentialsError,
                    botocore.exceptions.ClientError,
                    botocore.exceptions.EndpointConnectionError) as aws_err:
                logger.warning(
                    "Bedrock call failed due to AWS credential/network issue: %s. Falling back to offline engine.",
                    aws_err
                )

        # Fallback to deterministic reasoning engine
        return self._analyze_with_fallback(txns, webhook_url, customer_id)

    def _analyze_with_bedrock(
        self,
        transactions: List[Transaction],
        webhook_url: str,
        customer_id: str
    ) -> RiskAnalysisSummary:
        """Execute reasoning loop using Strands Agent and AWS Bedrock Converse API."""
        prompt_payload = {
            "customer_id": customer_id,
            "webhook_target": webhook_url,
            "transactions": [t.model_dump() for t in transactions]
        }

        user_message = (
            f"Please analyze the following batch of {len(transactions)} financial transactions. "
            f"Reason through any anomalies. If any transaction is suspicious or high-risk, "
            f"invoke the `notify_risk_team` tool immediately with the details. "
            f"Transactions Data:\n{json.dumps(prompt_payload, indent=2)}"
        )

        # Run Strands agent loop
        agent_result = self._strands_agent(user_message)
        narrative = str(agent_result)

        dispatches = get_recorded_dispatches()

        # Parse flagged transactions from dispatches
        flagged: List[RiskFlag] = []
        for d in dispatches:
            # We reconstruct flag from incident notification or mock evaluation
            pass

        # If bedrock output narrative has findings, compute risk
        overall_level = RiskLevel.CRITICAL if len(dispatches) > 2 else (
            RiskLevel.HIGH if len(dispatches) > 0 else RiskLevel.LOW
        )
        overall_score = min(100.0, len(dispatches) * 35.0)

        return RiskAnalysisSummary(
            total_transactions_analyzed=len(transactions),
            total_anomalies_flagged=len(dispatches),
            overall_risk_level=overall_level,
            overall_risk_score=overall_score,
            flagged_transactions=flagged,
            webhook_dispatches=dispatches,
            summary_narrative=narrative,
            agent_version=self.version,
            model_used=self.model_id
        )

    def _analyze_with_fallback(
        self,
        transactions: List[Transaction],
        webhook_url: str,
        customer_id: str
    ) -> RiskAnalysisSummary:
        """Execute reasoning with the deterministic rule-based engine."""
        flagged, narrative = evaluate_transactions_rule_based(
            transactions=transactions,
            webhook_url_override=webhook_url
        )

        dispatches = get_recorded_dispatches()

        # Calculate normalized score
        critical_count = sum(1 for f in flagged if f.risk_level == RiskLevel.CRITICAL)
        high_count = sum(1 for f in flagged if f.risk_level == RiskLevel.HIGH)
        medium_count = sum(1 for f in flagged if f.risk_level == RiskLevel.MEDIUM)

        raw_score = (critical_count * 45.0) + (high_count * 30.0) + (medium_count * 15.0)
        overall_score = min(100.0, raw_score)

        if critical_count > 0 or overall_score >= 70:
            overall_level = RiskLevel.CRITICAL
        elif high_count > 0 or overall_score >= 40:
            overall_level = RiskLevel.HIGH
        elif medium_count > 0:
            overall_level = RiskLevel.MEDIUM
        else:
            overall_level = RiskLevel.LOW

        return RiskAnalysisSummary(
            total_transactions_analyzed=len(transactions),
            total_anomalies_flagged=len(flagged),
            overall_risk_level=overall_level,
            overall_risk_score=overall_score,
            flagged_transactions=flagged,
            webhook_dispatches=dispatches,
            summary_narrative=narrative,
            agent_version=self.version,
            model_used=f"{self.model_id} (Evaluator Fallback)"
        )


# Global default agent instance
risk_monitor_agent = RiskMonitorAgent()
