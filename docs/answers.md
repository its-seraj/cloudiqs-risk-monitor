# Part 4 — Architectural & Operational Written Questions

This document provides authoritative answers to the operational, reliability, and security questions posed in Part 4 of the CloudiQS / Agentic Bakery assessment.

---

### Question 1: Shipping Prompt Updates Without Breaking Existing Users & Versioning Strategy

> **"You update the agent’s prompt logic. How do you ship this without breaking existing users? What does versioning look like?"**

#### 1. Versioning Architecture
Prompt logic is treated as **immutable, version-controlled software assets** rather than unversioned runtime variables. We implement a **Two-Tier Semantic Versioning Strategy**:

* **Tier 1: Prompt Artifact Versioning (`config/prompts/risk_analyzer_vX.Y.Z.yaml`)**:
  * **MAJOR (`v2.0.0`)**: Breaking contract changes (e.g., changes to output schemas, added mandatory tool arguments, or fundamental changes to anomaly taxonomy).
  * **MINOR (`v1.1.0`)**: Backward-compatible prompt additions (e.g., adding whitelist clauses, tuning few-shot examples, refining boundary criteria).
  * **PATCH (`v1.0.1`)**: Non-semantic phrasing adjustments, formatting improvements, or typo fixes.
* **Tier 2: Amazon Bedrock AgentCore Runtime Versions & Aliases**:
  * Every container build generates an immutable, numerical **Runtime Version** (e.g., `Version 1`, `Version 2`).
  * Traffic reaches the agent through **Aliases** (e.g., `prod`, `staging`, `canary`), which point to specific versions rather than mutable code pointers.

#### 2. Safe Zero-Downtime Rollout Workflow
```
Code Push (New Prompt YAML)
   │
   ▼
Automated Eval Gate (Precision & Recall CI Benchmark)
   │
   ├─► [FAILS GATE] ──► Build Fails, PR Blocked (No deployment)
   │
   ▼ [PASSES GATE]
Container Build & ECR Push (Tagged with commit SHA + version)
   │
   ▼
Deploy to Bedrock AgentCore as Immutable Version (e.g., Version 5)
   │
   ▼
Traffic Shifting (AgentCore Gateway / CloudFront / ALB):
   • 90% traffic routed to Alias 'prod' (points to Version 4)
   • 10% traffic routed to Alias 'canary' (points to Version 5)
   │
   ▼
Canary Verification (CloudWatch Error Rates, Webhook Delivery Rates, Anomaly Spike Alarms)
   │
   ▼
100% Traffic Promotion: Alias 'prod' updated to point to Version 5
```

1. **Pre-Deployment Regression Testing (Eval Gate)**:
   - Before any prompt change merges, the CI pipeline (`tests/test_evals.py`) runs the new prompt against a **golden evaluation dataset** of verified historic transactions.
   - The build is blocked automatically if **Recall drops below 90%** or **False Positive Rate (FPR) exceeds 10%**.
2. **Canary & Traffic Shifting**:
   - The new version is deployed alongside the existing version.
   - Using AgentCore Gateway / API Gateway routing weights, traffic is shifted incrementally (e.g., 10% canary for 1 hour).
   - If CloudWatch anomaly or alert alarms remain stable, the alias pointer is updated to 100%.
3. **Explicit Client Version Pinning**:
   - Enterprise callers can pin their desired agent version via an HTTP header (e.g., `X-Agent-Version: 1.0.0`). The gateway routes pinned callers to their verified version, preventing unexpected behavior changes.

---

### Question 2: Rapid Rollback for False Positives

> **"The new version starts producing false positives. How do you roll back quickly?"**

When an AI agent produces false positives in a financial or risk-monitoring domain, speed is paramount: legitimate customers get blocked, cards get flagged, and risk operations teams get inundated with false alert tickets.

#### 1. Instant Pointer / Alias Rollback (< 5 Seconds)
Because Bedrock AgentCore Runtime utilizes immutable versioned deployments with named aliases, **no container rebuild or redeployment is required to roll back**.
* **AWS CLI Execution**:
  ```bash
  aws bedrock-agentcore update-agent-alias \
    --runtime-name risk-monitor-agent \
    --alias-name prod \
    --routing-configuration '[{"agentVersion": "4", "weight": 100}]'
  ```
* The `prod` alias shifts 100% of incoming invocations back to the known-healthy version (`Version 4`) instantaneously at the DNS/routing layer.

#### 2. Automated Circuit Breakers & Rollback Triggers
Instead of waiting for human operators to notice false positives, automated CloudWatch metric alarms trigger automated rollbacks:
* **Metric 1: Anomaly Frequency Anomaly**: If the percentage of flagged transactions spikes above $3\sigma$ of the 30-day moving average (e.g., jumping from 2% to 18%), CloudWatch triggers an alarm.
* **Metric 2: Webhook Dispatch Velocity Alarm**: Sudden spike in `notify_risk_team` tool invocations.
* **Automated Action**: The CloudWatch alarm invokes an AWS Step Functions state machine or Lambda function that executes the alias rollback command within seconds.

#### 3. Dynamic Configuration Hot-Reloading / Kill Switches
* For minor rule adjustments without redeploying containers, prompt thresholds (such as `risk_threshold_amount: 2500.0`) are backed by AWS AppConfig or AWS Secrets Manager.
* A live config change (e.g. tightening threshold or suppressing a faulty merchant pattern) propagates to running containers within 15 seconds without container restart.

---

### Question 3: Multi-Tenancy, Customer Data, Webhook URLs & Results Isolation

> **"Two customers use this agent. How do you keep their data, webhook URLs, and results completely separate?"**

In an enterprise AWS environment (like Agentic Bakery / CloudiQS customer accounts), multi-tenant isolation must be enforced across all four architectural dimensions: **Execution, Storage, Secrets/Webhooks, and Observability**.

```
                           Tenant Invocations
                 ┌───────────────────────────────────┐
                 │ Customer Alpha    │ Customer Beta │
                 └─────────┬─────────┴───────┬───────┘
                           │                 │
                           ▼                 ▼
             ┌───────────────────────────────────────────┐
             │       API Gateway / AgentCore Gateway     │
             │   (Validates mTLS / JWT / X-Customer-ID)  │
             └─────────────────────┬─────────────────────┘
                                   │
              Tenant Context Injected (Thread-Safe Context)
                                   │
       ┌───────────────────────────┴───────────────────────────┐
       ▼                                                       ▼
┌───────────────────────────────┐       ┌───────────────────────────────┐
│     Customer Alpha Scope      │       │     Customer Beta Scope       │
│ • Webhook: /customer-alpha    │       │ • Webhook: /customer-beta     │
│ • HMAC: Alpha Secret (KMS-A)  │       │ • HMAC: Beta Secret (KMS-B)   │
│ • Threshold: $3,000.00        │       │ • Threshold: $1,500.00        │
│ • State: Zero Cross-Leakage   │       │ • State: Zero Cross-Leakage   │
└──────────────┬────────────────┘       └──────────────┬────────────────┘
               │                                       │
               ▼                                       ▼
┌───────────────────────────────┐       ┌───────────────────────────────┐
│ CloudWatch Stream: /alpha/*   │       │ CloudWatch Stream: /beta/*    │
└───────────────────────────────┘       └───────────────────────────────┘
```

#### 1. Identity & Request Scoping at the Ingress Layer
* Invocations require authentication via API Keys or JWT tokens issued by Amazon Cognito / OAuth.
* The API Gateway extracts the verified tenant identity and injects an authenticated `X-Customer-ID` header.
* Callers cannot spoof another customer's ID: the gateway rejects requests where the token's claims do not match the payload customer identifier.

#### 2. Isolated Secret & Webhook Management
* Webhook destination URLs and HMAC-SHA256 signing keys are **never hardcoded** and **never shared**.
* Each tenant's configurations are stored in **AWS Secrets Manager** under path-based isolation:
  - `cloudiqs/tenants/customer_alpha/webhook`
  - `cloudiqs/tenants/customer_beta/webhook`
* Secrets are encrypted under separate AWS KMS Customer Managed Keys (CMKs), with IAM resource policies granting decryption only to authorized execution context.

#### 3. Ephemeral, Stateless Agent Execution (Zero Cross-Tenant Leakage)
* The Strands Agent is invoked in **ephemeral, stateless mode** for transaction analysis batches:
  - Conversation memory and tool dispatch stores (`clear_dispatch_store()`) are reset on every invocation.
  - The agent holds **no persistent cross-invocation conversational cache** in memory that Customer Beta could access.
  - Bedrock AgentCore MicroVM runtime provides hypervisor-level process isolation, preventing memory scraping between invocations.

#### 4. Cryptographic Webhook Security (HMAC-SHA256)
* When the agent detects an anomaly for Customer Alpha:
  - It retrieves Customer Alpha's dedicated webhook URL and secret key.
  - It calculates an HMAC-SHA256 digest of the payload.
  - Customer Alpha's receiving server verifies `X-Signature-SHA256: sha256=<digest>` with their private secret key, guaranteeing that no spoofed or cross-tenant alerts can be accepted.

#### 5. Audit Logging & Observability Partitioning
* CloudWatch Logs and OpenTelemetry spans are tagged with `tenant_id` and structured into customer-specific log streams (`/aws/agentcore/risk-monitor/customer_alpha`).
* CloudWatch IAM policies ensure that compliance officers from Customer Alpha only have read permissions (`logs:FilterLogEvents`) for their own log stream prefix.
