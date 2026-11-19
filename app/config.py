"""Configuration settings and multi-tenant resolution for the Risk Monitor Agent.

Supports environment-based configuration, tenant-specific policy overrides,
and automatic fallback between AWS Bedrock Converse API and local mock execution.
"""

import json
import os
from pathlib import Path
from typing import Dict, Optional
from pydantic import BaseModel, Field


class TenantConfig(BaseModel):
    """Customer-specific isolation parameters."""
    customer_id: str
    webhook_url: str
    hmac_secret: str = "default-tenant-secret-key-32chars!"
    risk_threshold_amount: float = 2500.0
    allowed_high_risk_categories: list[str] = Field(default_factory=list)


class Settings(BaseModel):
    """Global agent runtime configuration."""
    agent_version: str = os.getenv("AGENT_VERSION", "1.0.0")
    environment: str = os.getenv("APP_ENV", "development")
    port: int = int(os.getenv("PORT", "8080"))

    # AWS & Bedrock settings
    aws_region: str = os.getenv("AWS_REGION", "us-east-1")
    bedrock_model_id: str = os.getenv(
        "BEDROCK_MODEL_ID",
        "anthropic.claude-3-5-sonnet-20241022-v2:0"
    )
    # Execution mode: 'bedrock', 'mock', or 'auto' (detects AWS credentials)
    execution_mode: str = os.getenv("AGENT_EXECUTION_MODE", "auto")

    # Webhook defaults
    default_webhook_url: str = os.getenv(
        "DEFAULT_WEBHOOK_URL",
        "http://localhost:8081/webhook/risk-alerts"
    )
    default_hmac_secret: str = os.getenv(
        "WEBHOOK_HMAC_SECRET",
        "cloudiqs-risk-monitor-signing-key-v1"
    )

    # Prompt config directory
    prompts_dir: Path = Path(__file__).resolve().parent.parent / "config" / "prompts"
    tenant_config_path: Path = Path(__file__).resolve().parent.parent / "config" / "tenant_config.json"


# Global singleton settings
settings = Settings()


class TenantRegistry:
    """Manages tenant isolation, ensuring data, webhooks, and secrets are separated."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or settings.tenant_config_path
        self._tenants: Dict[str, TenantConfig] = {}
        self._load_tenants()

    def _load_tenants(self) -> None:
        """Load tenant configurations from disk or establish defaults."""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data.get("tenants", []):
                        tenant = TenantConfig(**item)
                        self._tenants[tenant.customer_id] = tenant
            except Exception as e:
                print(f"[WARN] Failed loading tenant config: {e}. Using fallback defaults.")

        # Ensure baseline customer tenants exist for testing & assessment
        if "customer_alpha" not in self._tenants:
            self._tenants["customer_alpha"] = TenantConfig(
                customer_id="customer_alpha",
                webhook_url="http://mock-webhook-receiver:8081/webhook/customer-alpha",
                hmac_secret="secret-key-customer-alpha-987654321",
                risk_threshold_amount=3000.0
            )
        if "customer_beta" not in self._tenants:
            self._tenants["customer_beta"] = TenantConfig(
                customer_id="customer_beta",
                webhook_url="http://mock-webhook-receiver:8081/webhook/customer-beta",
                hmac_secret="secret-key-customer-beta-123456789",
                risk_threshold_amount=1500.0
            )

    def get_tenant(self, customer_id: Optional[str]) -> TenantConfig:
        """Retrieve tenant configuration by customer_id or return default tenant."""
        if customer_id and customer_id in self._tenants:
            return self._tenants[customer_id]

        # Default fallback tenant
        return TenantConfig(
            customer_id=customer_id or "default_customer",
            webhook_url=settings.default_webhook_url,
            hmac_secret=settings.default_hmac_secret,
            risk_threshold_amount=2000.0
        )

    def register_tenant(self, tenant: TenantConfig) -> None:
        """Dynamically register or update a tenant."""
        self._tenants[tenant.customer_id] = tenant


tenant_registry = TenantRegistry()
