"""Phase 1 diagnostic worker task."""

from typing import Literal, TypedDict

from revenueguard_worker.celery_app import celery_app


class PingResult(TypedDict):
    status: Literal["ok"]
    service: str


@celery_app.task(name="revenueguard.system.ping")  # type: ignore[untyped-decorator]
def ping() -> PingResult:
    """Prove worker registration without touching financial state."""

    return {"status": "ok", "service": "revenueguard-worker"}
