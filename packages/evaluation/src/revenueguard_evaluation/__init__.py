"""Evaluation package for reproducible RevenueGuard experiments."""

from revenueguard_evaluation.batch import (
    BatchEvaluationReport,
    EvaluationStrategy,
    HeldOutManifest,
    load_held_out_manifest,
    run_batch_evaluation,
)
from revenueguard_evaluation.integrated import (
    HttpIntegratedApi,
    IntegratedEvaluationError,
    run_integrated_batch,
)

__all__ = [
    "BatchEvaluationReport",
    "EvaluationStrategy",
    "HeldOutManifest",
    "HttpIntegratedApi",
    "IntegratedEvaluationError",
    "load_held_out_manifest",
    "run_batch_evaluation",
    "run_integrated_batch",
]
