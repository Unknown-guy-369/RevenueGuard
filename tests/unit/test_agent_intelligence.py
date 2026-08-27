from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime

from pydantic import BaseModel
from revenueguard_agents import (
    AGENT_MAY_EXECUTE_EXTERNAL_ACTIONS,
    AgentBudget,
    BoundedCaseIntelligence,
    CaseIntelligenceRequest,
    EvidenceItem,
    ModelResponse,
    redact_model_payload,
)
from revenueguard_domain import (
    ActionType,
    CandidateAction,
    ContactChannel,
    PredictionStatus,
    SubjectType,
    WorkflowType,
)

NOW = datetime(2026, 8, 27, 10, tzinfo=UTC)


class ScriptedModel:
    def __init__(
        self,
        responses: Mapping[str, Mapping[str, object]],
        *,
        delay_seconds: float = 0,
    ) -> None:
        self.responses = responses
        self.delay_seconds = delay_seconds
        self.calls: list[tuple[str, Mapping[str, object], int]] = []

    @property
    def model_version(self) -> str:
        return "test-model-1"

    async def generate(
        self,
        *,
        node: str,
        payload: Mapping[str, object],
        response_schema: type[BaseModel],
        max_output_tokens: int,
    ) -> ModelResponse:
        del response_schema
        self.calls.append((node, payload, max_output_tokens))
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        return ModelResponse(
            payload=self.responses[node],
            input_tokens=25,
            output_tokens=40,
        )


def _request() -> CaseIntelligenceRequest:
    return CaseIntelligenceRequest(
        run_id="intelligence_run_001",
        case_id="case_001",
        merchant_id="merchant_secret_001",
        correlation_id="correlation_001",
        workflow_type=WorkflowType.FAILED_SUBSCRIPTION,
        subject_type=SubjectType.SUBSCRIPTION,
        target="subscription_secret_001",
        amount_minor=10_000,
        currency="INR",
        diagnosis_code="EXPIRED_PAYMENT_METHOD",
        diagnosis_confidence_basis_points=9_700,
        candidates=(
            CandidateAction(
                action_type=ActionType.REQUEST_PAYMENT_METHOD_UPDATE,
                recovery_probability_basis_points=8_000,
                expected_net_recovery_minor=7_500,
                rank=1,
                target="subscription_secret_001",
                channel=ContactChannel.EMAIL,
            ),
            CandidateAction(
                action_type=ActionType.CREATE_PAYMENT_LINK,
                recovery_probability_basis_points=6_200,
                expected_net_recovery_minor=5_500,
                rank=2,
                target="subscription_secret_001",
            ),
            CandidateAction(
                action_type=ActionType.NO_ACTION,
                recovery_probability_basis_points=0,
                expected_net_recovery_minor=0,
                rank=3,
                target="subscription_secret_001",
            ),
        ),
        retry_count=0,
        contact_count=0,
        evidence=(
            EvidenceItem(
                reference="event_private_001",
                event_type="payment.failed",
                failure_category="EXPIRED_PAYMENT_METHOD",
                summary=(
                    "Customer jane@example.com called +91 98765 43210; "
                    "credential rzp_test_ABC123SECRET was removed."
                ),
                occurred_at=NOW,
            ),
        ),
        feature_version="features-v1",
        evaluated_at=NOW,
    )


def _successful_outputs() -> dict[str, Mapping[str, object]]:
    return {
        "DIAGNOSIS_ASSISTANCE": {
            "diagnosis_code": "EXPIRED_PAYMENT_METHOD",
            "confidence_basis_points": 9_200,
            "rationale": "The permitted evidence matches an expired method.",
        },
        "STRATEGY_GENERATION": {
            "strategies": [
                {
                    "action_type": "REQUEST_PAYMENT_METHOD_UPDATE",
                    "recovery_probability_basis_points": 7_900,
                    "expected_net_recovery_minor": 7_100,
                    "channel": "EMAIL",
                },
                {
                    "action_type": "CREATE_PAYMENT_LINK",
                    "recovery_probability_basis_points": 7_000,
                    "expected_net_recovery_minor": 6_000,
                    "channel": None,
                },
            ]
        },
        "RANKING": {
            "ordered_action_types": [
                "CREATE_PAYMENT_LINK",
                "REQUEST_PAYMENT_METHOD_UPDATE",
                "NO_ACTION",
            ]
        },
        "EXPLANATION": {
            "explanation": (
                "A test-mode payment link for jane@example.com has the strongest expected recovery."
            )
        },
    }


async def test_typed_graph_ranks_supported_candidates_and_captures_versions() -> None:
    model = ScriptedModel(_successful_outputs())
    result = await BoundedCaseIntelligence(model).recommend(_request())

    assert result.fallback_used is False
    assert result.diagnosis_code == "EXPIRED_PAYMENT_METHOD"
    assert result.confidence_basis_points == 9_200
    assert [candidate.action_type for candidate in result.candidates] == [
        ActionType.CREATE_PAYMENT_LINK,
        ActionType.REQUEST_PAYMENT_METHOD_UPDATE,
        ActionType.NO_ACTION,
    ]
    assert len(result.predictions) == 4
    assert all(item.status is PredictionStatus.SUCCEEDED for item in result.predictions)
    assert all(item.model_version == "test-model-1" for item in result.predictions)
    assert all(item.prompt_version == result.prompt_version for item in result.predictions)
    assert all(item.schema_version == result.schema_version for item in result.predictions)
    assert all(item.feature_version == "features-v1" for item in result.predictions)
    assert all(call[2] == 512 for call in model.calls)
    assert "jane@example.com" not in result.explanation
    assert "[REDACTED_EMAIL]" in result.explanation


async def test_model_payloads_are_minimized_and_redacted_before_every_call() -> None:
    model = ScriptedModel(_successful_outputs())
    await BoundedCaseIntelligence(model).recommend(_request())

    serialized = repr([payload for _, payload, _ in model.calls])
    assert "merchant_secret_001" not in serialized
    assert "subscription_secret_001" not in serialized
    assert "event_private_001" not in serialized
    assert "jane@example.com" not in serialized
    assert "+91 98765 43210" not in serialized
    assert "rzp_test_ABC123SECRET" not in serialized
    assert "[REDACTED_EMAIL]" in serialized
    assert "[REDACTED_PHONE]" in serialized
    assert "[REDACTED_CREDENTIAL]" in serialized


async def test_malformed_outputs_fall_back_without_blocking_policy_candidates() -> None:
    malformed = {node: {"unexpected": "field"} for node in _successful_outputs()}
    model = ScriptedModel(malformed)
    result = await BoundedCaseIntelligence(
        model,
        budget=AgentBudget(max_model_retries=0),
    ).recommend(_request())

    assert result.fallback_used is True
    assert result.candidates == _request().candidates
    assert len(result.predictions) == 4
    assert all(item.status is PredictionStatus.FALLBACK for item in result.predictions)
    assert all(item.failure_code == "MODEL_OUTPUT_INVALID" for item in result.predictions)
    assert len(model.calls) == 4


async def test_unavailable_model_falls_back_without_retrying_or_blocking() -> None:
    result = await BoundedCaseIntelligence().recommend(_request())

    assert result.fallback_used is True
    assert result.candidates == _request().candidates
    assert len(result.predictions) == 4
    assert all(item.failure_code == "MODEL_UNAVAILABLE" for item in result.predictions)


async def test_per_node_timeout_is_bounded_and_falls_back() -> None:
    model = ScriptedModel(_successful_outputs(), delay_seconds=0.03)
    result = await BoundedCaseIntelligence(
        model,
        budget=AgentBudget(
            model_timeout_seconds=0.01,
            workflow_timeout_seconds=0.2,
            max_model_retries=0,
        ),
    ).recommend(_request())

    assert result.fallback_used is True
    assert len(result.predictions) == 4
    assert all(item.failure_code == "MODEL_TIMEOUT" for item in result.predictions)


async def test_slow_graph_has_a_single_traceable_deterministic_fallback() -> None:
    model = ScriptedModel(_successful_outputs(), delay_seconds=0.015)
    result = await BoundedCaseIntelligence(
        model,
        budget=AgentBudget(
            model_timeout_seconds=0.02,
            workflow_timeout_seconds=0.05,
            max_model_retries=0,
        ),
    ).recommend(_request())

    assert result.fallback_used is True
    assert result.candidates == _request().candidates
    assert len(result.predictions) == 1
    assert result.predictions[0].failure_code == "GRAPH_TIMEOUT"


def test_agent_surface_has_no_execution_authority_and_redacts_sensitive_keys() -> None:
    intelligence = BoundedCaseIntelligence()

    assert AGENT_MAY_EXECUTE_EXTERNAL_ACTIONS is False
    assert not hasattr(intelligence, "execute")
    assert not hasattr(intelligence, "send_message")
    assert redact_model_payload(
        {"authorization": "Bearer secret", "nested": {"customer_id": "customer_1"}}
    ) == {
        "authorization": "[REDACTED]",
        "nested": {"customer_id": "[REDACTED]"},
    }
