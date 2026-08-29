"""Bounded LangGraph orchestration for advisory case intelligence."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from time import monotonic
from typing import TypedDict, TypeVar, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ValidationError
from revenueguard_domain import (
    ActionType,
    CandidateAction,
    ModelNode,
    ModelPrediction,
    PredictionStatus,
)

from revenueguard_agents.contracts import (
    AGENT_SCHEMA_VERSION,
    DEFAULT_PROMPT_VERSION,
    CaseIntelligenceRequest,
    CaseIntelligenceResult,
    DiagnosisOutput,
    EvidenceItem,
    ExplanationOutput,
    ModelResponse,
    RankingOutput,
    ReadOnlyCaseTools,
    SanitizedCaseContext,
    StrategyOutput,
    StrategyOutputItem,
    StructuredModel,
)
from revenueguard_agents.redaction import redact_model_payload
from revenueguard_agents.tracing import (
    CaseIntelligenceTracer,
    DisabledCaseIntelligenceTracer,
)

OutputT = TypeVar("OutputT", bound=BaseModel)


class ModelUnavailableError(RuntimeError):
    """A configured model is unavailable without indicating workflow failure."""


class UnavailableStructuredModel:
    """Safe default used until an explicit model adapter is configured."""

    @property
    def model_version(self) -> str:
        return "UNAVAILABLE"

    async def generate(
        self,
        *,
        node: str,
        payload: Mapping[str, object],
        response_schema: type[BaseModel],
        max_output_tokens: int,
    ) -> ModelResponse:
        del node, payload, response_schema, max_output_tokens
        raise ModelUnavailableError("no structured model adapter is configured")


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentBudget:
    model_timeout_seconds: float = 2.0
    workflow_timeout_seconds: float = 8.0
    max_model_retries: int = 1
    max_output_tokens: int = 512
    max_graph_steps: int = 8

    def __post_init__(self) -> None:
        if self.model_timeout_seconds <= 0 or self.workflow_timeout_seconds <= 0:
            raise ValueError("model and workflow timeouts must be positive")
        if self.workflow_timeout_seconds < self.model_timeout_seconds:
            raise ValueError("workflow timeout cannot be shorter than a model timeout")
        if not 0 <= self.max_model_retries <= 3:
            raise ValueError("model retry limit must be between zero and three")
        if not 64 <= self.max_output_tokens <= 4_096:
            raise ValueError("output token limit must be between 64 and 4096")
        if not 8 <= self.max_graph_steps <= 32:
            raise ValueError("graph step limit must be between 8 and 32")


class _GraphState(TypedDict, total=False):
    request: CaseIntelligenceRequest
    context: SanitizedCaseContext
    evidence: tuple[EvidenceItem, ...]
    diagnosis: DiagnosisOutput
    strategies: StrategyOutput
    candidates: tuple[CandidateAction, ...]
    ranking: RankingOutput
    ranked_candidates: tuple[CandidateAction, ...]
    explanation: ExplanationOutput
    predictions: tuple[ModelPrediction, ...]
    fallback_used: bool


class _RequestTools:
    """Read-only adapter over an immutable, already-authorized case snapshot."""

    def __init__(self, request: CaseIntelligenceRequest) -> None:
        self._request = request

    async def load_context(self) -> SanitizedCaseContext:
        return SanitizedCaseContext(
            workflow_type=self._request.workflow_type,
            subject_type=self._request.subject_type,
            amount_minor=self._request.amount_minor,
            currency=self._request.currency,
            diagnosis_code=self._request.diagnosis_code,
            diagnosis_confidence_basis_points=(self._request.diagnosis_confidence_basis_points),
            retry_count=self._request.retry_count,
            contact_count=self._request.contact_count,
            supported_action_types=tuple(
                candidate.action_type for candidate in self._request.candidates
            ),
        )

    async def load_evidence(self) -> tuple[EvidenceItem, ...]:
        return self._request.evidence


class BoundedCaseIntelligence:
    """Run typed advisory nodes; all failure paths return deterministic candidates."""

    def __init__(
        self,
        model: StructuredModel | None = None,
        *,
        budget: AgentBudget | None = None,
        prompt_version: str = DEFAULT_PROMPT_VERSION,
        schema_version: str = AGENT_SCHEMA_VERSION,
        tools_factory: Callable[[CaseIntelligenceRequest], ReadOnlyCaseTools] | None = None,
        tracer: CaseIntelligenceTracer | None = None,
    ) -> None:
        if not prompt_version or not schema_version:
            raise ValueError("prompt and schema versions are required")
        self._model = model or cast(StructuredModel, UnavailableStructuredModel())
        self._budget = budget or AgentBudget()
        self._prompt_version = prompt_version
        self._schema_version = schema_version
        self._tools_factory = tools_factory or _RequestTools
        self._tracer = tracer or DisabledCaseIntelligenceTracer()

    async def recommend(self, request: CaseIntelligenceRequest) -> CaseIntelligenceResult:
        with self._tracer.case_run(request) as trace_run:
            result = await self._recommend_untraced(request)
            trace_run.record_result(result)
            return result

    async def _recommend_untraced(self, request: CaseIntelligenceRequest) -> CaseIntelligenceResult:
        tools = self._tools_factory(request)
        graph = self._build_graph(tools)
        try:
            # Do not let LangGraph auto-trace its full state. The enclosing tracer records only
            # a reviewed projection with no merchant/case identifiers, prompts, or evidence text.
            with self._tracer.suppress_automatic_child_traces():
                async with asyncio.timeout(self._budget.workflow_timeout_seconds):
                    state = cast(
                        _GraphState,
                        await graph.ainvoke(
                            {"request": request, "predictions": (), "fallback_used": False},
                            config={"recursion_limit": self._budget.max_graph_steps},
                        ),
                    )
        except TimeoutError:
            return self._graph_fallback(request, "GRAPH_TIMEOUT")
        except Exception:
            # A graph/framework defect maps to a traceable, side-effect-free fallback.
            return self._graph_fallback(request, "GRAPH_ERROR")

        diagnosis = state["diagnosis"]
        return CaseIntelligenceResult(
            diagnosis_code=diagnosis.diagnosis_code,
            confidence_basis_points=diagnosis.confidence_basis_points,
            candidates=state["ranked_candidates"],
            explanation=state["explanation"].explanation,
            predictions=state["predictions"],
            model_version=self._model.model_version,
            prompt_version=self._prompt_version,
            schema_version=self._schema_version,
            feature_version=request.feature_version,
            fallback_used=state["fallback_used"],
        )

    def _build_graph(
        self, tools: ReadOnlyCaseTools
    ) -> CompiledStateGraph[_GraphState, None, _GraphState, _GraphState]:
        builder = StateGraph(_GraphState)

        async def load_context(state: _GraphState) -> dict[str, object]:
            del state
            return {"context": await tools.load_context()}

        async def load_evidence(state: _GraphState) -> dict[str, object]:
            del state
            return {"evidence": await tools.load_evidence()}

        async def diagnose(state: _GraphState) -> dict[str, object]:
            request = state["request"]
            fallback = DiagnosisOutput(
                diagnosis_code=request.diagnosis_code,
                confidence_basis_points=request.diagnosis_confidence_basis_points,
                rationale="Deterministic diagnosis retained.",
            )

            def validate(output: DiagnosisOutput) -> None:
                if request.terminal_diagnosis and output.diagnosis_code != request.diagnosis_code:
                    raise ValueError("terminal deterministic diagnosis cannot be changed")
                if output.confidence_basis_points > request.diagnosis_confidence_basis_points:
                    raise ValueError("model confidence cannot exceed deterministic confidence")

            output, prediction, used_fallback = await self._invoke_model(
                request=request,
                node=ModelNode.DIAGNOSIS_ASSISTANCE,
                payload={
                    "context": state["context"].model_dump(mode="json"),
                    "evidence": [self._model_evidence(item) for item in state["evidence"]],
                    "task": "Assist diagnosis using only supplied evidence.",
                },
                schema=DiagnosisOutput,
                fallback=fallback,
                validate=validate,
            )
            return self._node_result(state, "diagnosis", output, prediction, used_fallback)

        async def generate_strategies(state: _GraphState) -> dict[str, object]:
            request = state["request"]
            fallback = StrategyOutput(
                strategies=tuple(
                    StrategyOutputItem(
                        action_type=candidate.action_type,
                        recovery_probability_basis_points=(
                            candidate.recovery_probability_basis_points
                        ),
                        expected_net_recovery_minor=candidate.expected_net_recovery_minor,
                        channel=candidate.channel,
                    )
                    for candidate in request.candidates
                    if candidate.action_type is not ActionType.NO_ACTION
                )
            )
            templates: dict[ActionType, CandidateAction] = {
                candidate.action_type: candidate
                for candidate in request.candidates
                if candidate.action_type is not ActionType.NO_ACTION
            }

            def validate(output: StrategyOutput) -> None:
                for strategy in output.strategies:
                    template = templates.get(strategy.action_type)
                    if template is None:
                        raise ValueError("model proposed an unsupported action type")
                    if strategy.channel is not template.channel:
                        raise ValueError("model cannot change the deterministic contact channel")
                    if (
                        not -request.amount_minor
                        <= strategy.expected_net_recovery_minor
                        <= (request.amount_minor)
                    ):
                        raise ValueError("model expected value exceeds case amount bounds")

            output, prediction, used_fallback = await self._invoke_model(
                request=request,
                node=ModelNode.STRATEGY_GENERATION,
                payload={
                    "context": state["context"].model_dump(mode="json"),
                    "diagnosis": state["diagnosis"].model_dump(mode="json"),
                    "supported_candidates": [
                        self._model_candidate(candidate) for candidate in request.candidates
                    ],
                    "task": "Score only the supported candidate action types.",
                },
                schema=StrategyOutput,
                fallback=fallback,
                validate=validate,
            )
            candidates = self._hydrate_candidates(request, output)
            result = self._node_result(state, "strategies", output, prediction, used_fallback)
            result["candidates"] = candidates
            return result

        async def rank(state: _GraphState) -> dict[str, object]:
            candidates = state["candidates"]
            fallback = RankingOutput(
                ordered_action_types=tuple(candidate.action_type for candidate in candidates)
            )

            def validate(output: RankingOutput) -> None:
                expected = {candidate.action_type for candidate in candidates}
                if set(output.ordered_action_types) != expected:
                    raise ValueError("ranking must contain every generated action exactly once")
                if output.ordered_action_types[-1] is not ActionType.NO_ACTION:
                    raise ValueError("NO_ACTION must remain the final safe fallback")

            output, prediction, used_fallback = await self._invoke_model(
                request=state["request"],
                node=ModelNode.RANKING,
                payload={
                    "diagnosis": state["diagnosis"].model_dump(mode="json"),
                    "candidates": [self._model_candidate(item) for item in candidates],
                    "task": "Rank all candidates; keep NO_ACTION last.",
                },
                schema=RankingOutput,
                fallback=fallback,
                validate=validate,
            )
            by_action = {candidate.action_type: candidate for candidate in candidates}
            ranked = tuple(
                CandidateAction(
                    action_type=action_type,
                    recovery_probability_basis_points=(
                        by_action[action_type].recovery_probability_basis_points
                    ),
                    expected_net_recovery_minor=(
                        by_action[action_type].expected_net_recovery_minor
                    ),
                    rank=index,
                    target=by_action[action_type].target,
                    logical_attempt=by_action[action_type].logical_attempt,
                    channel=by_action[action_type].channel,
                )
                for index, action_type in enumerate(output.ordered_action_types, start=1)
            )
            result = self._node_result(state, "ranking", output, prediction, used_fallback)
            result["ranked_candidates"] = ranked
            return result

        async def explain(state: _GraphState) -> dict[str, object]:
            diagnosis = state["diagnosis"]
            fallback = ExplanationOutput(
                explanation=(
                    f"Deterministic fallback retained {diagnosis.diagnosis_code}; "
                    "the deterministic policy engine makes the final authorization decision."
                )
            )
            output, prediction, used_fallback = await self._invoke_model(
                request=state["request"],
                node=ModelNode.EXPLANATION,
                payload={
                    "diagnosis": diagnosis.model_dump(mode="json"),
                    "ranked_candidates": [
                        self._model_candidate(item) for item in state["ranked_candidates"]
                    ],
                    "evidence_summaries": [item.summary for item in state["evidence"]],
                    "task": "Explain the advisory ranking without inventing evidence.",
                },
                schema=ExplanationOutput,
                fallback=fallback,
            )
            return self._node_result(state, "explanation", output, prediction, used_fallback)

        builder.add_node("load_context", load_context)
        builder.add_node("load_evidence", load_evidence)
        builder.add_node("diagnose", diagnose)
        builder.add_node("generate_strategies", generate_strategies)
        builder.add_node("rank", rank)
        builder.add_node("explain", explain)
        builder.add_edge(START, "load_context")
        builder.add_edge("load_context", "load_evidence")
        builder.add_edge("load_evidence", "diagnose")
        builder.add_edge("diagnose", "generate_strategies")
        builder.add_edge("generate_strategies", "rank")
        builder.add_edge("rank", "explain")
        builder.add_edge("explain", END)
        return builder.compile()

    async def _invoke_model(
        self,
        *,
        request: CaseIntelligenceRequest,
        node: ModelNode,
        payload: Mapping[str, object],
        schema: type[OutputT],
        fallback: OutputT,
        validate: Callable[[OutputT], None] | None = None,
    ) -> tuple[OutputT, ModelPrediction, bool]:
        safe_payload_object = redact_model_payload(payload)
        if not isinstance(safe_payload_object, Mapping):
            raise AssertionError("model payload root must remain a mapping")
        safe_payload = {str(key): value for key, value in safe_payload_object.items()}
        input_digest = self._digest(safe_payload)
        started = monotonic()
        failure_code = "MODEL_ERROR"
        input_tokens = 0
        output_tokens = 0
        for attempt in range(self._budget.max_model_retries + 1):
            del attempt
            try:
                async with asyncio.timeout(self._budget.model_timeout_seconds):
                    response = await self._model.generate(
                        node=node.value,
                        payload=safe_payload,
                        response_schema=schema,
                        max_output_tokens=self._budget.max_output_tokens,
                    )
                if response.output_tokens > self._budget.max_output_tokens:
                    raise ValueError("model output exceeded configured token limit")
                input_tokens = response.input_tokens
                output_tokens = response.output_tokens
                output = schema.model_validate(response.payload)
                sanitized_output = redact_model_payload(output.model_dump(mode="json"))
                output = schema.model_validate(sanitized_output)
                if validate is not None:
                    validate(output)
                return (
                    output,
                    self._prediction(
                        request=request,
                        node=node,
                        status=PredictionStatus.SUCCEEDED,
                        input_digest=input_digest,
                        output=output,
                        latency_ms=self._latency_ms(started),
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    ),
                    False,
                )
            except ModelUnavailableError:
                failure_code = "MODEL_UNAVAILABLE"
                break
            except TimeoutError:
                failure_code = "MODEL_TIMEOUT"
            except ValidationError:
                failure_code = "MODEL_OUTPUT_INVALID"
            except ValueError as exc:
                failure_code = (
                    "MODEL_TOKEN_LIMIT_EXCEEDED"
                    if "token limit" in str(exc)
                    else "MODEL_OUTPUT_REJECTED"
                )
            except Exception:
                # Provider errors are deliberately collapsed without persisting sensitive detail.
                failure_code = "MODEL_ERROR"
        return (
            fallback,
            self._prediction(
                request=request,
                node=node,
                status=PredictionStatus.FALLBACK,
                input_digest=input_digest,
                output=fallback,
                latency_ms=self._latency_ms(started),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                failure_code=failure_code,
            ),
            True,
        )

    def _prediction(
        self,
        *,
        request: CaseIntelligenceRequest,
        node: ModelNode,
        status: PredictionStatus,
        input_digest: str,
        output: BaseModel,
        latency_ms: int,
        input_tokens: int,
        output_tokens: int,
        failure_code: str | None = None,
    ) -> ModelPrediction:
        material = f"{request.run_id}:{node.value}:{self._prompt_version}:{self._schema_version}"
        return ModelPrediction(
            prediction_id=f"prediction_{sha256(material.encode()).hexdigest()[:32]}",
            run_id=request.run_id,
            case_id=request.case_id,
            merchant_id=request.merchant_id,
            correlation_id=request.correlation_id,
            node=node,
            status=status,
            input_sha256=input_digest,
            output_payload=cast(
                Mapping[str, object], redact_model_payload(output.model_dump(mode="json"))
            ),
            model_version=self._model.model_version,
            prompt_version=self._prompt_version,
            schema_version=self._schema_version,
            feature_version=request.feature_version,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            failure_code=failure_code,
            created_at=request.evaluated_at,
        )

    def _graph_fallback(
        self, request: CaseIntelligenceRequest, failure_code: str
    ) -> CaseIntelligenceResult:
        fallback = ExplanationOutput(
            explanation=(
                f"Deterministic fallback retained {request.diagnosis_code}; "
                "the deterministic policy engine makes the final authorization decision."
            )
        )
        prediction = self._prediction(
            request=request,
            node=ModelNode.GRAPH,
            status=PredictionStatus.FALLBACK,
            input_digest=self._digest(
                {
                    "workflow_type": request.workflow_type.value,
                    "subject_type": request.subject_type.value,
                    "diagnosis_code": request.diagnosis_code,
                }
            ),
            output=fallback,
            latency_ms=round(self._budget.workflow_timeout_seconds * 1_000),
            input_tokens=0,
            output_tokens=0,
            failure_code=failure_code,
        )
        return CaseIntelligenceResult(
            diagnosis_code=request.diagnosis_code,
            confidence_basis_points=request.diagnosis_confidence_basis_points,
            candidates=request.candidates,
            explanation=fallback.explanation,
            predictions=(prediction,),
            model_version=self._model.model_version,
            prompt_version=self._prompt_version,
            schema_version=self._schema_version,
            feature_version=request.feature_version,
            fallback_used=True,
        )

    @staticmethod
    def _hydrate_candidates(
        request: CaseIntelligenceRequest, output: StrategyOutput
    ) -> tuple[CandidateAction, ...]:
        templates = {candidate.action_type: candidate for candidate in request.candidates}
        hydrated = tuple(
            CandidateAction(
                action_type=item.action_type,
                recovery_probability_basis_points=item.recovery_probability_basis_points,
                expected_net_recovery_minor=item.expected_net_recovery_minor,
                rank=index,
                target=templates[item.action_type].target,
                logical_attempt=templates[item.action_type].logical_attempt,
                channel=item.channel,
            )
            for index, item in enumerate(output.strategies, start=1)
        )
        no_action = templates[ActionType.NO_ACTION]
        return (
            *hydrated,
            CandidateAction(
                action_type=ActionType.NO_ACTION,
                recovery_probability_basis_points=0,
                expected_net_recovery_minor=0,
                rank=len(hydrated) + 1,
                target=no_action.target,
                logical_attempt=no_action.logical_attempt,
            ),
        )

    @staticmethod
    def _node_result(
        state: _GraphState,
        key: str,
        output: BaseModel,
        prediction: ModelPrediction,
        used_fallback: bool,
    ) -> dict[str, object]:
        return {
            key: output,
            "predictions": (*state.get("predictions", ()), prediction),
            "fallback_used": state.get("fallback_used", False) or used_fallback,
        }

    @staticmethod
    def _model_evidence(item: EvidenceItem) -> dict[str, object]:
        return {
            "event_type": item.event_type,
            "failure_category": item.failure_category,
            "summary": item.summary,
            "occurred_at": item.occurred_at.isoformat(),
        }

    @staticmethod
    def _model_candidate(candidate: CandidateAction) -> dict[str, object]:
        return {
            "action_type": candidate.action_type.value,
            "recovery_probability_basis_points": (candidate.recovery_probability_basis_points),
            "expected_net_recovery_minor": candidate.expected_net_recovery_minor,
            "rank": candidate.rank,
            "channel": candidate.channel.value if candidate.channel else None,
        }

    @staticmethod
    def _digest(payload: Mapping[str, object]) -> str:
        document = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return sha256(document.encode()).hexdigest()

    @staticmethod
    def _latency_ms(started: float) -> int:
        return max(0, round((monotonic() - started) * 1_000))
