# RevenueGuard — Unified Agentic Revenue Recovery Control Plane
## Production-Grade Architecture Document for Razorpay AI Revenue Recovery

**Project Track:** AI Revenue Recovery  
**Project Type:** Real-time, event-driven, agentic fintech platform  
**Primary Objective:** Detect revenue at risk, diagnose the cause, determine the best safe recovery action, execute it through approved channels, verify the outcome, and measure actual money recovered.

---

# 1. Executive Summary

Payment revenue does not disappear only because a transaction fails. Revenue leakage happens across multiple stages:

- payment degradation
- failed recurring subscriptions
- abandoned checkouts
- failed mandates
- overdue B2B invoices
- broken promises-to-pay
- payment-method expiry
- temporary issuer or gateway outages

Most existing systems treat these as separate problems.

A traditional payment recovery system usually follows:

```text
Failure
→ fixed retry
→ reminder
→ retry again
→ stop
```

A more recent AI recovery system may improve this to:

```text
Failure
→ AI diagnosis
→ choose recovery action
→ contact customer
```

**RevenueGuard goes one level higher.**

It is a **Unified Revenue Recovery Control Plane** where all revenue-risk events enter the same platform, become standardized recovery cases, and are processed through reusable agentic recovery workflows.

The architecture combines:

```text
Real-time Event Processing
+
Per-Case Agentic Reasoning
+
Merchant-Wide Portfolio Intelligence
+
ML Recovery Scoring
+
Deterministic Policy-as-Code
+
Human Approval
+
Idempotent Financial Execution
+
Outcome Verification
+
Tamper-Evident Audit
+
Batch Evaluation
```

The design principle is:

> **AI can reason and recommend. Policy decides whether an action is allowed. Deterministic services execute money-related actions. Razorpay or verified source events determine whether an action actually succeeded.**

This philosophy aligns strongly with Razorpay Agent Studio, where agents operate within merchant-defined boundaries, actions undergo independent validation, sensitive actions may require approval, and each action is logged.

---

# 2. Problem Statement

A merchant may process hundreds or thousands of transactions daily.

Consider a subscription business with:

```text
100 subscription charges
```

Result:

```text
40 successful
60 failed
```

A basic recovery system treats the 60 failures independently:

```text
60 failures
→ 60 retries
→ 60 messages
```

But that may be wrong.

Suppose:

```text
53 of the 60 failures
occurred within 4 minutes

Payment method:
UPI

Same failure family:
issuer / gateway unavailable
```

The problem may not be 53 customers.

The problem may be a **systemic payment degradation event**.

Blind recovery can therefore:

- create unnecessary retries
- annoy customers
- increase communication cost
- create duplicate recovery attempts
- worsen issuer throttling
- reduce trust
- produce misleading metrics
- potentially create financial inconsistencies

RevenueGuard therefore reasons at **two levels simultaneously**:

```text
Case Level
"What should happen for this customer?"

Portfolio Level
"What is happening to this merchant overall?"
```

---

# 3. Existing Approaches and Their Limitations

## 3.1 Traditional Dunning / Retry Systems

Typical architecture:

```text
Payment Failed
      ↓
Fixed delay
      ↓
Retry
      ↓
Reminder
      ↓
Retry
```

### Strength

Simple, predictable and cheap.

### Limitation

It generally does not understand:

- root cause
- customer history
- merchant-wide failure spikes
- expected economic value
- whether contacting the customer is appropriate
- whether the failure is systemic
- interactions between multiple active recovery workflows

---

# 3.2 Single-Transaction AI Recovery Agents

Modern recovery agents improve the process:

```text
Failure
→ AI diagnosis
→ Strategy
→ Customer contact
→ Retry
```

This gives better personalization.

However, these systems can still be strongly **case-centric**.

They may not know:

```text
another 100 customers
are failing for exactly the same reason
```

They can also become unsafe if the LLM itself has too much authority over financial actions.

---

# 3.3 Deterministic Policy Recovery Systems

Systems such as the deterministic architecture pattern represented by Sanjivini introduce important improvements:

```text
Finite playbooks
+
policy-as-code
+
stopping rules
+
compliance guards
+
auditability
```

This is a strong architecture for safe financial automation.

Its limitation can be that intelligence remains mostly inside predefined playbooks and the system may have limited adaptive optimization across a merchant's complete recovery portfolio.

---

# 3.4 Product-Centric AI Recovery Platforms

The RecoverAI-style approach demonstrates another strength:

```text
strong dashboard
+
payment failure demo
+
communication
+
promise tracking
+
recovery analytics
```

This makes the system easy to understand and demonstrate.

However, a production-grade system must additionally prove that its underlying financial behavior is:

```text
durable
idempotent
verifiable
persistent
failure-aware
non-simulated where financial truth matters
```

---

# 4. RevenueGuard Innovation

RevenueGuard is not another:

> “payment failed → AI sends WhatsApp”

system.

Its primary innovations are the following.

## 4.1 Unified Revenue Recovery Control Plane

All supported recovery scenarios enter one infrastructure:

```text
payment.failed
subscription.pending
subscription.halted
checkout.abandoned
invoice.overdue
mandate.failure
promise.broken
```

They are converted into one standardized abstraction:

```text
RevenueRiskEvent
```

and eventually:

```text
RecoveryCase
```

New recovery scenarios therefore become **plug-in playbooks**, rather than completely separate applications.

---

## 4.2 Dual-Level Intelligence

RevenueGuard has:

```text
Case Recovery Intelligence
+
Portfolio Intelligence
```

Case intelligence answers:

> What should we do about REC-10291?

Portfolio intelligence answers:

> Why have 53 similar failures happened in the last five minutes?

This allows RevenueGuard to identify systemic degradation before launching unnecessary individual recovery actions.

---

## 4.3 Decision Governor

RevenueGuard does not blindly accept the first recovery strategy generated by an agent.

Candidate actions are generated:

```text
RETRY
WAIT
PAYMENT_LINK
UPDATE_PAYMENT_METHOD
EMAIL
WHATSAPP
PROMISE_TO_PAY
HUMAN_ESCALATION
STOP
```

The Decision Governor ranks only **allowed** actions using expected business value.

Conceptually:

\[
ExpectedRecovery = AmountAtRisk \times P(Recovery)
\]

RevenueGuard extends this to:

\[
NetRecoveryUtility =
ExpectedRecovery
-
InterventionCost
-
RiskPenalty
-
CustomerFriction
\]

Therefore the objective is not:

> maximize number of retries.

It is:

> **maximize safely recovered net revenue.**

---

# 5. High-Level Architecture

```text
                    ┌───────────────────────────┐
                    │          RAZORPAY         │
                    │                           │
                    │ Payments                  │
                    │ Subscriptions             │
                    │ Payment Links             │
                    └────────────┬──────────────┘
                                 │
                              Webhooks
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────┐
│                    REVENUEGUARD CONTROL PLANE                  │
│                                                              │
│  1. Webhook / Event Gateway                                 │
│             ↓                                                │
│  2. Signature Verification                                  │
│             ↓                                                │
│  3. Idempotent Event Inbox                                  │
│             ↓                                                │
│  4. Durable Event Queue                                     │
│             ↓                                                │
│  5. Event Normalizer                                        │
│             ↓                                                │
│  6. Universal Recovery Case Engine                          │
│             │                                                │
│       ┌─────┴─────────────┐                                 │
│       ▼                   ▼                                  │
│  7A. Case Recovery     7B. Portfolio                        │
│      Intelligence          Intelligence                      │
│       │                   │                                  │
│       └──────────┬────────┘                                  │
│                  ▼                                           │
│  8. LangGraph Recovery Workflow                             │
│                  ↓                                           │
│  9. Diagnosis / ML Recovery Scoring                         │
│                  ↓                                           │
│ 10. Decision Governor                                       │
│                  ↓                                           │
│ 11. Deterministic Guardian / Policy Engine                  │
│                  ↓                                           │
│ 12. Human Approval if Required                              │
│                  ↓                                           │
│ 13. Durable Action Outbox                                   │
│                  ↓                                           │
│ 14. Action Executor                                         │
│                  ↓                                           │
│            Razorpay / Contact APIs                           │
│                  ↓                                           │
│ 15. Outcome Verification                                    │
│                  ↓                                           │
│     RECOVERED / DEFERRED / STOPPED / ESCALATED / UNKNOWN    │
│                                                              │
│ Everything → Decision Receipts → Audit Ledger → Analytics    │
└──────────────────────────────────────────────────────────────┘
```

---

# 6. Stage 1 — Webhook and Event Gateway

Razorpay is the primary external financial-event source.

Examples include:

```text
payment.authorized
payment.captured

subscription.pending
subscription.charged
subscription.halted

payment_link.paid
```

Razorpay's subscription Test Mode can simulate successful and failed recurring charges. A failed recurring charge can move the subscription into `pending`; after retries are exhausted it can become `halted`.

The RevenueGuard endpoint could be:

```text
POST /api/v1/webhooks/razorpay
```

The gateway performs only ingestion-related work.

It must **not** execute an LLM or complete the recovery workflow synchronously.

---

# 7. Stage 2 — Webhook Signature Verification

Incoming Razorpay webhooks are validated before entering the system.

Razorpay includes:

```text
X-Razorpay-Signature
```

and uses HMAC-SHA256 with the webhook secret. Validation must use the **raw request body**, not a reserialized parsed payload.

Flow:

```text
Webhook Received
      ↓
Raw body retained
      ↓
Signature extracted
      ↓
HMAC verification
      ↓
┌───────────┐
│ VALID?    │
└─────┬─────┘
      │
 YES  │  NO
 ↓    │   ↓
Store │ Reject
```

### Why?

Without verification, anyone who discovers the endpoint could attempt to submit fake events such as:

```text
payment.failed
amount ₹50,000
```

and trigger recovery actions.

---

# 8. Stage 3 — Idempotent Event Inbox

Webhook systems follow asynchronous delivery semantics.

Razorpay explicitly notes that the same webhook event may be delivered more than once and provides `x-razorpay-event-id` to identify duplicates. Razorpay also warns that webhook events may arrive out of order.

Therefore RevenueGuard maintains:

```text
webhook_events
---------------------------------
id
provider
provider_event_id UNIQUE
event_type
entity_id
raw_payload
received_at
processing_state
processed_at
```

Example:

```text
evt_XYZ → first request  → ACCEPTED
evt_XYZ → second request → DUPLICATE
evt_XYZ → third request  → DUPLICATE
```

Only one event enters business processing.

### Why database uniqueness?

Because:

```text
in-memory Set()
```

cannot protect against:

- process restart
- multiple API servers
- deployment
- horizontal scaling

Database uniqueness provides a shared consistency boundary.

---

# 9. Stage 4 — Durable Event Queue

After the event is safely persisted:

```text
Webhook
↓
Persist
↓
Return 2xx
↓
Queue
```

Heavy work happens asynchronously.

Queue responsibilities:

```text
buffer event spikes
control concurrency
support delayed jobs
retry software failures
support dead-letter handling
protect downstream APIs
```

Example:

```text
Normal:
5 failures / minute

Outage:
5,000 failures / minute
```

Without a queue:

```text
5,000 workflows
→ simultaneous LLM/API calls
→ system collapse
```

With a queue:

```text
5,000 events
      ↓
 Durable Queue
      ↓
 Controlled Workers
```

### Prototype

```text
Redis + Celery
```

### Production mapping

```text
AWS SQS + worker cluster
```

---

# 10. Stage 5 — Event Normalization

RevenueGuard should not let every internal component understand Razorpay-specific payload structures.

The adapter converts the provider event into:

```text
RevenueRiskEvent
```

Example:

```json
{
  "event_id": "evt_0029",
  "merchant_id": "mer_001",
  "provider": "RAZORPAY",
  "event_family": "SUBSCRIPTION_FAILURE",
  "customer_id": "cust_92",
  "entity_id": "sub_287",
  "payment_id": "pay_827",
  "amount_at_risk": 499900,
  "currency": "INR",
  "failure_code": "INSUFFICIENT_FUNDS",
  "occurred_at": "2026-08-24T10:15:00Z"
}
```

This makes the control plane extensible.

Future sources could include:

```text
merchant checkout
ERP
invoice service
CRM
commerce platform
```

without changing the recovery engine.

---

# 11. Stage 6 — Universal Recovery Case

Each actionable revenue-risk event creates or updates a:

```text
RecoveryCase
```

Example:

```text
Case:
REC-82931

Type:
FAILED_SUBSCRIPTION

Revenue at risk:
₹4,999

Customer:
cust_92

Current state:
DIAGNOSING
```

Therefore:

```text
60 failed subscriptions
```

become:

```text
60 RecoveryCases
```

They are processed independently but are visible to the merchant-wide Portfolio Controller.

---

# 12. Recovery Case State Machine

Financial workflows should not be free-running conversations.

RevenueGuard uses explicit state transitions:

```text
DETECTED
   ↓
DIAGNOSING
   ↓
PLANNING
   ↓
POLICY_CHECK
   ↓
READY
   ↓
EXECUTING
   ↓
VERIFYING
   ↓
┌──────────────┬────────────┬─────────────┬───────────┐
RECOVERED    DEFERRED      ESCALATED    STOPPED    UNKNOWN
```

### Why a finite state machine?

It prevents invalid behavior.

Example:

```text
RECOVERED
→ RETRY
```

must never occur.

State machines also improve:

```text
debugging
testing
recovery after crashes
auditability
human investigation
```

---

# 13. Unified LangGraph Workflow

RevenueGuard uses **one workflow platform**, not one unrelated application per problem.

The master LangGraph flow:

```text
START
  ↓
Load Recovery Case
  ↓
Load Merchant Context
  ↓
Classify Recovery Type
  ↓
             ROUTER
        ┌──────┼────────┬─────────┐
        ▼      ▼        ▼         ▼
     Payment Subscription Checkout Receivable
     Playbook  Playbook  Playbook  Playbook
        └──────┴────────┴─────────┘
                    ↓
             Recovery Scoring
                    ↓
             Decision Governor
                    ↓
               Policy Gate
                    ↓
        PROCEED / DEFER / STOP
                    ↓
                 Execute
                    ↓
                 Verify
                    ↓
                   END
```

The infrastructure is shared.

Only the recovery-specific subgraph changes.

---

# 14. Core Recovery Playbooks

## 14.1 Payment Degradation

```text
payment failures
      ↓
cluster recent events
      ↓
issuer / payment method /
failure-code correlation
      ↓
systemic incident?
```

If NO:

```text
process individual case
```

If YES:

```text
pause related individual retries
↓
create degradation incident
↓
monitor recovery
↓
resume only when safe
```

---

# 14.2 Failed Subscription

```text
subscription.pending
      ↓
load previous attempts
      ↓
determine failure category
      ↓
estimate recoverability
      ↓
policy
      ↓
wait / payment link /
update method / escalation
```

Razorpay already performs automatic subscription retries in certain failure scenarios, so RevenueGuard must understand Razorpay's subscription state rather than blindly introducing a second retry process.

This is an important production detail.

---

# 14.3 Checkout Abandonment

Merchant-side checkout instrumentation generates:

```text
checkout.started
checkout.payment_attempted
checkout.abandoned
checkout.completed
```

The recovery case uses:

```text
cart amount
checkout stage
customer history
time since abandonment
previous recovery contact
merchant offers
```

to determine whether recovery is economically useful.

---

# 14.4 B2B Receivables / Promise-to-Pay

```text
invoice overdue
      ↓
contact policy
      ↓
approved message
      ↓
customer response
      ↓
LLM intent extraction
```

Example:

```text
"I'll make the payment on Friday morning."
```

Structured output:

```json
{
  "intent": "PROMISE_TO_PAY",
  "date": "2026-08-28",
  "confidence": 0.94
}
```

Then:

```text
deterministic scheduler
↓
promise tracking
↓
payment received?
   ├── yes → KEPT
   └── no  → BROKEN
```

If:

```text
"I dispute this invoice."
```

the workflow becomes:

```text
STOP AUTOMATION
→ HUMAN ESCALATION
```

---

# 15. Where AI Is Used

RevenueGuard intentionally avoids the:

```text
LLM everywhere
```

architecture.

AI is used only where probabilistic intelligence provides clear value.

## LLM Responsibilities

```text
ambiguous root-cause explanation
customer response understanding
promise-to-pay extraction
communication drafting
merchant-facing summaries
candidate recovery reasoning
```

## ML Responsibilities

```text
recovery propensity
payment degradation anomaly detection
case prioritization
strategy effectiveness estimation
```

## Deterministic Services

```text
money amount
retry limits
consent
permissions
contact frequency
idempotency
financial calculations
state transitions
policy enforcement
API execution
```

Razorpay similarly emphasizes that Agent Studio works on verified merchant data and validates actions independently for compliance, amount correctness, scope and other boundaries.

---

# 16. Case Recovery Agent

One LangGraph execution instance handles one recovery case.

For:

```text
60 failed subscriptions
```

we conceptually create:

```text
60 independent case workflows
```

but **not necessarily 60 concurrent workers**.

For example:

```text
60 queued cases

Worker concurrency = 10
```

Execution:

```text
Workers 1–10  → Cases 1–10
complete
Workers 1–10  → Cases 11–20
...
```

This provides:

```text
controlled API traffic
controlled LLM spending
rate-limit safety
stable infrastructure
```

---

# 17. Portfolio Intelligence

The Portfolio Controller sees all active cases across a merchant.

Its responsibilities include:

```text
failure-spike detection
issuer clustering
payment-method degradation
common failure-code analysis
recovery-budget monitoring
customer-overcontact detection
strategy effectiveness
```

Example:

```text
100 subscriptions attempted

40 success
60 failure
```

Portfolio analysis:

```text
53 failures
same payment method
same error family
same 4-minute window

Systemic degradation confidence = 0.94
```

Decision:

```text
DEFER 53 individual retries

Reason:
Likely infrastructure issue
rather than 53 independent
customer failures.
```

The remaining seven cases can continue individually.

This **case + portfolio intelligence architecture** is one of RevenueGuard's major differentiators.

---

# 18. Cross-Workflow Coordination

A customer may simultaneously have:

```text
failed subscription
+
abandoned checkout
+
overdue invoice
```

Independent agents could contact the same person three times.

RevenueGuard correlates cases using:

```text
merchant_id
customer_id
```

The Decision Governor may convert:

```text
3 potential interventions
```

into:

```text
1 coordinated intervention
```

This reduces:

```text
customer fatigue
communication cost
duplicate recovery actions
```

---

# 19. Recovery Scoring

For each case, RevenueGuard estimates:

```text
P(recovery | current context, proposed strategy)
```

Possible model features:

```text
amount
payment method
failure code
historical success rate
previous retry count
previous recovery outcome
customer tenure
time of day
day of month
subscription state
time since failure
number of contacts
merchant category
systemic degradation signal
```

A simple model such as:

```text
LightGBM
XGBoost
Logistic Regression
```

is preferable to unnecessarily complex deep learning.

---

# 20. Decision Governor

The agent generates candidate actions.

Example:

```text
A. Retry now
B. Wait 12 hours
C. Generate payment link
D. Request payment-method update
E. Contact customer
F. Escalate
G. Stop
```

Each receives:

```text
expected recovery probability
expected value
cost
risk
customer friction
policy eligibility
```

The Governor selects:

```text
highest-value ALLOWED action
```

not simply:

```text
highest-probability action
```

---

# 21. Deterministic Guardian Engine

No recovery action can execute directly from LangGraph.

Every candidate must pass:

```text
Guardian Engine
```

Possible checks:

| Guardian | Function |
|---|---|
| Case State | Block recovered/closed cases |
| Duplicate Action | Prevent duplicate execution |
| Retry Limit | Stop runaway retries |
| Contact Limit | Prevent spam |
| Consent | Respect communication consent |
| Quiet Hours | Defer inappropriate contact |
| Merchant Permission | Enforce allowed capabilities |
| Amount Integrity | Prevent modification of payable amount |
| Risk Threshold | Escalate sensitive cases |
| Economic ROI | Prevent recovery costing more than value |
| Systemic Incident | Prevent individual retries during degradation |
| Human Approval | Gate sensitive operations |
| Rate Limit | Protect Razorpay/communication providers |

Output:

```text
PROCEED
DEFER
SKIP
STOP
REQUIRE_APPROVAL
```

---

# 22. Human-in-the-Loop

Razorpay Agent Studio emphasizes merchant control and supports review-first behavior for sensitive actions.

RevenueGuard therefore supports:

```text
Agent Proposal
      ↓
Policy:
REQUIRE_APPROVAL
      ↓
Merchant Dashboard
      ↓
Approve / Reject
      ↓
Execution
```

LangGraph can pause while waiting for the decision and resume afterward.

---

# 23. Action Outbox

A financial system must handle partial software failures.

Example:

```text
Database updated
↓
server crashes
↓
API never called
```

Or:

```text
API called successfully
↓
server crashes
↓
database not updated
```

RevenueGuard therefore writes executable actions to:

```text
action_outbox
```

Example schema:

```text
action_id
case_id
action_type
idempotency_key
payload
status
attempt_count
scheduled_at
executed_at
result_reference
```

Workers execute from this durable store.

---

# 24. Action Executor

Possible actions include:

```text
CREATE_PAYMENT_LINK
SEND_APPROVED_NOTIFICATION
WAIT
REQUEST_PAYMENT_METHOD_UPDATE
SCHEDULE_FOLLOWUP
ESCALATE_TO_HUMAN
STOP_RECOVERY
```

Payment-link actions use Razorpay's supported APIs rather than invented local links. Razorpay supports Payment Link creation by API and emits webhook events such as `payment_link.paid`.

---

# 25. Outcome Verification

This is a critical differentiator.

RevenueGuard never assumes:

```text
API call completed
=
money recovered
```

Instead:

```text
Action executed
      ↓
WAIT FOR VERIFIED RESULT
      ↓
Webhook / provider API
      ↓
verified state
```

Only then:

```text
RECOVERED
```

---

# 26. The UNKNOWN State

Suppose:

```text
POST /payment_links
```

times out.

There are two possibilities:

```text
Request never reached Razorpay
```

or:

```text
Razorpay created the resource,
but response was lost
```

RevenueGuard must NOT immediately retry blindly.

State becomes:

```text
OUTCOME_UNKNOWN
```

Then:

```text
verification
↓
resource exists?
```

If yes:

```text
continue existing action
```

If no:

```text
safe retry
```

This prevents duplicate business effects.

---

# 27. Out-of-Order Events

Razorpay explicitly warns that webhook events may not arrive in chronological order.

Therefore RevenueGuard does not use:

```text
last webhook received
=
current state
```

Instead state transitions consider:

```text
event type
provider entity state
event timestamp
existing terminal state
version/history
```

For example:

```text
RECOVERED
```

must not regress because an older failure event arrives later.

---

# 28. Audit and Decision Receipts

Every agent decision generates a structured receipt.

Example:

```text
Decision ID:
DEC-82917

Recovery Case:
REC-81092

Input event:
subscription.pending

Revenue at risk:
₹4,999

Diagnosis:
INSUFFICIENT_FUNDS

Recovery prediction:
0.72

Candidate actions:
RETRY_NOW
WAIT_12H
PAYMENT_LINK

Selected:
WAIT_12H

Reason:
Higher expected recovery
with lower customer friction

Portfolio signal:
NO_SYSTEMIC_INCIDENT

Guardian result:
PROCEED

Model:
recovery-model-v2

Policy:
merchant-policy-v17

Execution:
scheduled

Final outcome:
RECOVERED

Recovered amount:
₹4,999
```

---

# 29. Tamper-Evident Ledger

Audit entries can additionally form a hash chain:

```text
Hash(N) =
SHA256(
    Hash(N-1)
    + Event
    + Decision
    + PolicyVersion
    + Timestamp
)
```

If historical information changes:

```text
verification fails
```

This should be described as:

> **tamper-evident audit logging**

rather than claiming absolute immutability.

---

# 30. Production Database Design

PostgreSQL is the source of truth.

Core tables:

```text
merchants
merchant_policies

webhook_events

customers
payments
subscriptions

recovery_cases
case_transitions

portfolio_incidents

model_predictions

decision_receipts

recovery_actions
action_attempts

communication_consent

promise_to_pay

audit_entries
```

### Why PostgreSQL?

The problem domain contains:

```text
money
transactions
uniqueness
relationships
constraints
state transitions
idempotency
```

which strongly fits a relational transactional database.

Redis is used only for:

```text
queueing
caching
rate limiting
distributed locks
```

It is not the financial source of truth.

---

# 31. Testing Data Strategy

RevenueGuard should use **three levels of test data**.

This is important because we need both realistic Razorpay behavior and enough data to evaluate the system statistically.

---

# 32. Level A — Real Razorpay Test-Mode Events

First, generate genuine Razorpay Test Mode transactions.

Razorpay states that test webhook events use the same payload structure as Live Mode, making stage testing useful for validating integration behavior.

Capture:

```text
payment success
payment failure

subscription.pending
subscription.charged
subscription.halted

payment_link.paid
payment_link.cancelled
payment_link.expired
```

These events are stored as:

```text
fixtures/razorpay/
```

Example:

```text
payment_failed_01.json
subscription_pending_01.json
subscription_halted_01.json
payment_link_paid_01.json
```

These fixtures validate:

```text
signature handling
normalization
parser behavior
state transitions
integration correctness
```

---

# 33. Level B — Webhook Replay Harness

Real webhook payloads are replayed through the ingestion system.

Testing commands could include:

```text
replay normal
replay duplicate
replay out-of-order
replay delayed
replay burst
replay invalid-signature
```

Example:

```text
payment.failed
payment.failed
payment.failed
```

using the same event ID.

Expected:

```text
events received        3
events processed       1
duplicates ignored     2
financial actions      1
```

---

# 34. Level C — Synthetic Merchant Portfolio Generator

Razorpay Test Mode should not be abused to generate thousands of Payment Links or transactions purely for ML evaluation. For example, Razorpay currently documents a Test Mode limit of 30 Payment Links per business.

Therefore RevenueGuard generates synthetic events that follow the **real Razorpay schema** but are clearly marked:

```text
source = SYNTHETIC
```

A generator might create:

```text
10,000 transactions
```

with configurable:

```text
merchant size
payment methods
amount distribution
subscription age
customer history
failure-code distribution
retry history
issuer health
time-of-day effects
systemic outages
customer response behavior
```

---

# 35. Synthetic Scenario Generation

Example normal merchant:

```text
Transactions: 5,000

success               91%
insufficient funds     3%
issuer decline         2%
gateway timeout        1%
authentication         2%
other                  1%
```

Then inject an incident:

```text
14:00–14:12

UPI gateway degradation

Failure probability:
8% → 58%
```

RevenueGuard should detect the cluster.

---

# 36. Ground-Truth Recovery Simulation

Each synthetic customer contains hidden ground truth.

Example:

```json
{
  "customer_id": "C102",
  "amount": 4999,
  "failure": "INSUFFICIENT_FUNDS",
  "retry_now_success_probability": 0.15,
  "retry_12h_success_probability": 0.68,
  "payment_link_success_probability": 0.43,
  "contact_friction": 0.2
}
```

The agent does **not** see these hidden probabilities.

The simulator uses them to determine outcomes.

This gives us measurable experiments.

Important disclosure:

> Synthetic results demonstrate system behavior and comparative strategy performance under controlled simulated distributions; they are not claimed as real merchant production recovery rates.

This makes our evaluation honest.

---

# 37. ML Dataset

Training rows may contain:

```text
case_id
amount
payment_method
failure_category
customer_history_score
previous_attempts
customer_tenure
time_since_failure
time_of_day
day_of_month
subscription_state
contact_count
portfolio_failure_rate
issuer_degradation_score

label:
recovered_within_24h
```

Split:

```text
70% training
15% validation
15% held-out test
```

For stronger evaluation, also create an **out-of-distribution incident set** containing failure patterns not seen during training.

---

# 38. Metrics

Do not report only:

```text
accuracy
```

We should report model and business metrics.

## Model Metrics

```text
Precision
Recall
F1
ROC-AUC
PR-AUC
Brier Score / calibration
```

## Business Metrics

```text
Revenue at risk
Gross revenue recovered
Net revenue recovered
Recovery rate
Recovery cost
Cost per recovered ₹
Unnecessary interventions
Customer contacts
Human escalations
Unsafe actions blocked
Duplicate executions
Policy violations
Unknown outcomes
```

---

# 39. Batch Evaluation

Example:

```text
Synthetic evaluation batch
----------------------------

Cases evaluated             1,000

Revenue at risk          ₹24,80,000

Actions attempted              617
Cases deferred                 148
Cases stopped                   71
Cases escalated                 53

Recovered cases                421

Gross recovered          ₹13,28,500

Recovery cost               ₹38,700

Net recovered            ₹12,89,800

Recovery rate                 52.0%

Duplicate executions              0

Policy violations                 0
```

Then compare:

```text
Baseline static retry:
₹9,10,000 recovered

RevenueGuard:
₹12,89,800 net recovered
```

The goal is to prove:

> **measured money recovered across a batch**

which is explicitly part of the Track 03 bar.

---

# 40. Critical Edge Cases

## Duplicate Webhook

```text
same event delivered 5 times
```

Expected:

```text
1 business effect
```

---

## Out-of-Order Events

```text
success arrives
then older failure arrives
```

Expected:

```text
case remains RECOVERED
```

---

## Invalid Signature

Expected:

```text
reject
no queue event
no case
```

---

## Razorpay API Timeout

Expected:

```text
OUTCOME_UNKNOWN
↓
verify
↓
never blindly duplicate
```

---

## Worker Crash

Expected:

```text
durable queue/outbox
allows resume
```

---

## LLM Failure

Examples:

```text
timeout
invalid JSON
hallucinated action
unknown tool
```

Expected:

```text
schema validation
↓
deterministic fallback
or
human escalation
```

Money flow never depends solely on LLM availability.

---

## Duplicate Recovery Actions

Example:

```text
two workers attempt
CREATE_PAYMENT_LINK
for same case
```

Expected:

```text
idempotency constraint
↓
only one executable action
```

---

## Customer Already Paid

Before recovery action:

```text
re-fetch current case/payment state
```

If paid:

```text
STOP
```

---

## Customer Opt-Out

Expected:

```text
STOP all automated outreach
```

Razorpay's Agent Studio principles similarly state that outbound communication should respect consent and opt-outs.

---

## Customer Disputes Invoice

Expected:

```text
STOP automation
↓
HUMAN ESCALATION
```

---

## Systemic Failure Spike

Expected:

```text
Portfolio Controller
↓
create INCIDENT
↓
suppress related individual retries
```

---

## Same Customer, Multiple Cases

Expected:

```text
Decision Governor
↓
coordinate communication
```

---

## Policy Changes Mid-Workflow

Decision receipts store:

```text
policy_version
```

The next action is evaluated using the current approved merchant policy while historical decisions remain traceable to their original policy version.

---

## Queue Overload

Expected:

```text
backpressure
priority scheduling
worker autoscaling
```

High-value and time-sensitive cases can be prioritized without losing lower-priority cases.

---

# 41. Observability

Three kinds of observability are required.

## Infrastructure

```text
API latency
queue depth
worker utilization
database latency
errors
```

## Agent

```text
model latency
token usage
tool calls
structured-output failures
agent fallback rate
```

## Financial Workflow

```text
revenue at risk
revenue recovered
recovery action latency
duplicate actions
unknown outcomes
policy blocks
escalations
```

Recommended tools:

```text
OpenTelemetry
Prometheus/Grafana or hosted equivalent
LangSmith for LangGraph traces
structured application logs
```

---

# 42. Production Deployment

Prototype:

```text
Next.js
   ↓
Vercel

FastAPI
   ↓
Render / Railway / equivalent

PostgreSQL
Redis
Celery
```

Production mapping:

```text
                    Load Balancer
                         │
            ┌────────────┴────────────┐
            ▼                         ▼
       FastAPI API               Worker Fleet
            │                         │
            └────────────┬────────────┘
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
   PostgreSQL           SQS             Redis
        │
        ▼
 Financial Source
    of Truth
```

Secrets such as:

```text
Razorpay API keys
webhook secret
LLM keys
```

must live in a secret-management system and never in source control.

---

# 43. How RevenueGuard Works in the Real World

A merchant activates RevenueGuard.

Configuration:

```text
Merchant connects Razorpay
↓
Webhook endpoint configured
↓
Merchant chooses permissions
↓
Merchant recovery policy configured
↓
RevenueGuard activated
```

Merchant policy might state:

```text
Maximum retries              2

Maximum automated contacts   2

Voice calls                  disabled

Payment links                allowed

High-value threshold         ₹25,000

Above ₹25,000                human approval

Quiet hours                  21:00–08:00

Systemic degradation         pause retries
```

Now the platform continuously processes real financial events.

---

# 44. Real-World Example — One Failed Subscription

```text
10:01:00
subscription.pending

10:01:00
Webhook received

10:01:00
Signature valid

10:01:00
Event stored

10:01:00
2xx returned

10:01:01
Worker receives event

10:01:01
REC-10021 created

10:01:02
Diagnosis:
INSUFFICIENT_FUNDS

10:01:02
Portfolio signal:
NORMAL

10:01:03
Recovery score:
Retry now        0.19
Retry later      0.67
Payment link     0.42

10:01:03
Decision Governor:
WAIT

10:01:03
Policy:
PROCEED

10:01:03
Delayed workflow scheduled
```

When the case wakes:

```text
policy is re-evaluated
```

If permitted, a recovery action is taken.

Success is not assumed.

RevenueGuard waits for confirmed provider state before reporting:

```text
₹4,999 recovered
```

---

# 45. Real-World Example — 60 Failures Out of 100

```text
100 subscription charges

40 success
60 failure
```

All sixty become individual cases.

At the same time:

```text
Portfolio Controller
↓
aggregate recent failures
```

It discovers:

```text
53 / 60

same method
same issuer family
same error
same time window
```

Response:

```text
PAYMENT DEGRADATION INCIDENT

Cases affected:
53

Revenue at risk:
₹1,72,400

Decision:
DEFER AUTOMATIC RETRY

Individual customer messages:
SUPPRESSED

Reason:
Systemic degradation highly likely
```

Seven unrelated failures continue through their own workflows.

Once degradation resolves:

```text
Portfolio policy changes
↓
deferred case graphs resume
```

This is where RevenueGuard moves beyond a simple transaction recovery agent.

---

# 46. Why LangGraph Instead of a Free-Running Agent

LangGraph is used because recovery workflows are:

```text
stateful
branching
long-running
interruptible
human-gated
```

But LangGraph is **not** the financial source of truth.

Correct architecture:

```text
PostgreSQL
=
truth

LangGraph
=
reasoning/workflow context
```

The agent can propose:

```text
CREATE_PAYMENT_LINK
```

but only:

```text
Policy Engine
+
Action Executor
```

can actually execute it.

---

# 47. Why Not Use Only LangChain?

LangChain is useful inside nodes for:

```text
model integration
prompting
retrieval
structured output
```

LangGraph is more suitable as the orchestration layer because RevenueGuard requires:

```text
state transitions
conditional routing
pause/resume
human approval
long-lived workflows
```

Neither framework replaces:

```text
queueing
database transactions
idempotency
policy enforcement
financial verification
```

---

# 48. Final Technology Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js + TypeScript |
| Backend | FastAPI + Python |
| Agent Orchestration | LangGraph |
| LLM Utilities | LangChain where useful |
| LLM | Claude or Gemini with structured output |
| ML | LightGBM / XGBoost / Logistic Regression |
| Database | PostgreSQL |
| Queue Prototype | Redis + Celery |
| Production Queue | SQS-style durable queue |
| Cache / Locking | Redis |
| Payments | Razorpay Test Mode |
| Validation | Pydantic |
| ORM | SQLAlchemy |
| Observability | OpenTelemetry + LangSmith |
| Containerization | Docker |
| CI/CD | GitHub Actions |
| Audit | PostgreSQL + SHA-256 hash chain |

---

# 49. What Makes RevenueGuard Unique

RevenueGuard is differentiated by the combination of:

### Unified Control Plane

One infrastructure supports multiple forms of revenue loss.

### Case + Portfolio Intelligence

The platform reasons about both individual customers and merchant-wide failure patterns.

### Recovery Decision Governor

Actions are optimized using expected net revenue rather than blindly maximizing retries.

### Agentic but Bounded

LangGraph controls reasoning flow, not financial authority.

### Policy-as-Code

Money-related boundaries are deterministic and testable.

### Exactly-Once Business Effect

Duplicate external events may arrive, but idempotency ensures one intended financial action.

### Explicit UNKNOWN State

The platform never fabricates success when an external financial outcome is uncertain.

### Verified Outcomes

Only confirmed provider/customer results count as recovered money.

### Cross-Workflow Coordination

Multiple recovery cases for the same customer do not become multiple uncontrolled agents.

### Honest Batch Evaluation

Real Razorpay Test Mode validates integration fidelity; synthetic portfolios provide reproducible large-scale evaluation.

---

# 50. Final System Principle

RevenueGuard should be summarized with one line:

> **Observe every revenue-risk event, reason about both the customer and the merchant portfolio, select the highest-value permitted intervention, execute it safely, verify the financial outcome, and prove every decision through an audit trail.**

The project therefore goes beyond a simple:

```text
failed payment → agent → retry
```

architecture.

It becomes:

```text
Revenue Risk
      ↓
Unified Event Control Plane
      ↓
Case + Portfolio Intelligence
      ↓
Agentic Recovery Planning
      ↓
Deterministic Financial Governance
      ↓
Safe Execution
      ↓
Verified Recovery
      ↓
Measured ₹ Impact
```

That is the architecture RevenueGuard should implement and defend as a production-grade AI revenue-recovery system.