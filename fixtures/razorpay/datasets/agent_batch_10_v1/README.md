# Agent batch 10 v1

This directory contains ten independent, explicitly synthetic Razorpay-shaped failure events. The batch covers failed-subscription, payment-degradation, and B2B promise-to-pay case identities across five normalized failure categories.

Send all ten webhooks concurrently to the local API:

```bash
set -a
source .env
set +a
uv run python scripts/replay_webhooks.py burst \
  --dataset fixtures/razorpay/datasets/agent_batch_10_v1/dataset.json \
  --burst-size 10 \
  --max-workers 10 \
  --merchant-id merchant_demo_001
```

The first run should report:

```json
{"accepted": 10, "duplicates": 0, "failures": 0, "mode": "burst", "received": 10, "rejected": 0}
```

The webhook inbox uses stable event IDs derived from each fixture. Replaying the unchanged batch should therefore report ten duplicates instead of creating duplicate business effects.

Webhook delivery is concurrent, but downstream worker and local-model capacity control processing speed. Run only one intended Celery worker cluster so every case uses the same LangSmith configuration. The manifest's `expected` fields describe deterministic normalization and rule-engine outputs; LLM node status and latency are observable in each generated trace.
