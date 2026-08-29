"""Fail-open, data-minimized LangSmith tracing for advisory case intelligence."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Protocol, cast

from langsmith import Client, trace, tracing_context

from revenueguard_agents.contracts import CaseIntelligenceRequest, CaseIntelligenceResult

_TRACE_SCHEMA_VERSION = "revenueguard-case-intelligence-trace-1.0"


@dataclass(frozen=True, slots=True, kw_only=True)
class LangSmithTracingConfig:
    """Configuration kept separate from case data and provider credentials."""

    enabled: bool = False
    project_name: str = "revenueguard-case-intelligence"
    api_key: str | None = None

    def __post_init__(self) -> None:
        if not self.project_name.strip():
            raise ValueError("LangSmith project name is required")
        if self.enabled and (self.api_key is None or not self.api_key.strip()):
            raise ValueError("LangSmith API key is required when tracing is enabled")


class CaseIntelligenceTraceRun(Protocol):
    """Minimal trace surface that cannot alter case reasoning or execution."""

    def record_result(self, result: CaseIntelligenceResult) -> None: ...


class CaseIntelligenceTracer(Protocol):
    """Observability boundary for the advisory graph."""

    def case_run(
        self, request: CaseIntelligenceRequest
    ) -> AbstractContextManager[CaseIntelligenceTraceRun]: ...

    def suppress_automatic_child_traces(self) -> AbstractContextManager[None]: ...


class _NoopTraceRun:
    def record_result(self, result: CaseIntelligenceResult) -> None:
        del result


class _LangSmithRun(Protocol):
    def end(self, *, outputs: Mapping[str, object]) -> None: ...


class DisabledCaseIntelligenceTracer:
    """Default tracer with no network, storage, or graph behavior."""

    @contextmanager
    def case_run(self, request: CaseIntelligenceRequest) -> Iterator[CaseIntelligenceTraceRun]:
        del request
        yield _NoopTraceRun()

    @contextmanager
    def suppress_automatic_child_traces(self) -> Iterator[None]:
        with _automatic_tracing_suppressed():
            yield


class _LangSmithTraceRun:
    """Records only a fixed, reviewed output projection and never raises outward."""

    def __init__(self, run: _LangSmithRun) -> None:
        self._run = run

    def record_result(self, result: CaseIntelligenceResult) -> None:
        try:
            self._run.end(outputs=_trace_result(result))
        except Exception:
            # Observability cannot block a deterministic safety path.
            return

    def record_failure(self, error: BaseException) -> None:
        try:
            self._run.end(
                outputs={
                    "trace_schema_version": _TRACE_SCHEMA_VERSION,
                    "status": "ERROR",
                    "error_class": type(error).__name__,
                }
            )
        except Exception:
            return


class LangSmithCaseIntelligenceTracer:
    """Manual tracing that prevents automatic LangGraph state capture.

    LangGraph's automatic tracing can serialize the full graph state. RevenueGuard instead emits
    one manually controlled trace with a deliberately narrow input/output projection. Trace setup,
    submission, and teardown all fail open so they cannot affect advisory output or policy flow.
    """

    def __init__(self, config: LangSmithTracingConfig) -> None:
        self._config = config
        self._client = Client(api_key=config.api_key) if config.enabled else None

    @contextmanager
    def case_run(self, request: CaseIntelligenceRequest) -> Iterator[CaseIntelligenceTraceRun]:
        if not self._config.enabled or self._client is None:
            yield _NoopTraceRun()
            return

        tracing_scope: AbstractContextManager[object] | None = None
        run_scope: AbstractContextManager[object] | None = None
        try:
            tracing_scope = tracing_context(enabled=True)
            tracing_scope.__enter__()
            run_scope = trace(
                "revenueguard.case_intelligence",
                "chain",
                project_name=self._config.project_name,
                client=self._client,
                inputs=_trace_request(request),
                metadata={
                    "application": "RevenueGuard",
                    "trace_schema_version": _TRACE_SCHEMA_VERSION,
                    "data_classification": "redacted_operational_metadata",
                },
            )
            run = run_scope.__enter__()
        except Exception:
            self._close_scope(run_scope)
            self._close_scope(tracing_scope)
            yield _NoopTraceRun()
            return

        handle = _LangSmithTraceRun(cast(_LangSmithRun, run))
        try:
            yield handle
        except BaseException as error:
            handle.record_failure(error)
            raise
        finally:
            self._close_scope(run_scope)
            self._close_scope(tracing_scope)

    @contextmanager
    def suppress_automatic_child_traces(self) -> Iterator[None]:
        """Keep LangGraph from serializing its full internal state as child runs."""

        with _automatic_tracing_suppressed():
            yield

    @staticmethod
    def _close_scope(scope: AbstractContextManager[object] | None) -> None:
        if scope is None:
            return
        try:
            scope.__exit__(None, None, None)
        except Exception:
            return


def _trace_request(request: CaseIntelligenceRequest) -> dict[str, object]:
    """Return the complete LangSmith input contract; never add identifiers or free text."""

    return {
        "trace_schema_version": _TRACE_SCHEMA_VERSION,
        "workflow_type": request.workflow_type.value,
        "subject_type": request.subject_type.value,
        "currency": request.currency,
        "diagnosis_code": request.diagnosis_code,
        "terminal_diagnosis": request.terminal_diagnosis,
        "retry_count": request.retry_count,
        "contact_count": request.contact_count,
        "candidate_action_types": [candidate.action_type.value for candidate in request.candidates],
        "evidence_count": len(request.evidence),
        "evidence_event_types": sorted({item.event_type for item in request.evidence}),
        "evidence_failure_categories": sorted({item.failure_category for item in request.evidence}),
        "feature_version": request.feature_version,
    }


@contextmanager
def _automatic_tracing_suppressed() -> Iterator[None]:
    """Disable framework auto-tracing without allowing observability to break the graph."""

    try:
        scope = tracing_context(enabled=False)
        scope.__enter__()
    except Exception:
        yield
        return
    try:
        yield
    except BaseException as error:
        try:
            scope.__exit__(type(error), error, error.__traceback__)
        except Exception:
            pass
        raise
    else:
        try:
            scope.__exit__(None, None, None)
        except Exception:
            return


def _trace_result(result: CaseIntelligenceResult) -> dict[str, object]:
    """Return aggregate advisory outcome metadata without action targets or money values."""

    return {
        "trace_schema_version": _TRACE_SCHEMA_VERSION,
        "status": "FALLBACK" if result.fallback_used else "SUCCEEDED",
        "diagnosis_code": result.diagnosis_code,
        "confidence_basis_points": result.confidence_basis_points,
        "ranked_action_types": [candidate.action_type.value for candidate in result.candidates],
        "model_version": result.model_version,
        "prompt_version": result.prompt_version,
        "schema_version": result.schema_version,
        "feature_version": result.feature_version,
        "nodes": [
            {
                "node": prediction.node.value,
                "status": prediction.status.value,
                "failure_code": prediction.failure_code,
                "latency_ms": prediction.latency_ms,
                "input_tokens": prediction.input_tokens,
                "output_tokens": prediction.output_tokens,
            }
            for prediction in result.predictions
        ],
    }
