"""
Sandbox Management API Routes

Provides endpoints for:
- Task queue management (view/cancel/retry)
- Run logs streaming
- Resource metrics streaming
- System status
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ..error_handling import (
    get_request_trace_id,
    json_internal_error_response,
    public_exception_message,
)
from ..streaming import StreamEvent, sse_response

router = APIRouter()


# --- Request/Response Models ---


class CancelResponse(BaseModel):
    """Response for job cancellation"""

    status: str
    job_id: str
    message: str = ""
    trace_id: Optional[str] = None


class RetryResponse(BaseModel):
    """Response for job retry"""

    status: str
    old_job_id: str
    new_job_id: Optional[str] = None
    message: str = ""
    trace_id: Optional[str] = None


# --- Queue Management ---


@router.get("/sandbox/queue")
async def get_queue_status(
    http_request: Request,
    completed_limit: int = Query(20, ge=1, le=100, description="Max completed jobs to return"),
):
    """
    Get current queue status.

    Returns pending, running, and recently completed jobs.
    """
    try:
        from paperbot.infrastructure.queue.job_manager import JobManager

        manager = JobManager()
        await manager.connect()
        try:
            status = await manager.get_queue_status(completed_limit=completed_limit)
            return status.to_dict()
        finally:
            await manager.close()
    except ImportError:
        return {
            "error": "Redis/ARQ not configured",
            "pending": [],
            "running": [],
            "completed": [],
            "stats": {},
        }
    except Exception as e:
        return json_internal_error_response(
            http_request,
            exc=e,
            extra={
                "error": public_exception_message(e),
                "pending": [],
                "running": [],
                "completed": [],
                "stats": {},
            },
            log_message="Failed to load sandbox queue status",
        )


@router.get("/sandbox/jobs/{job_id}")
async def get_job_info(job_id: str, http_request: Request):
    """Get information about a specific job."""
    try:
        from paperbot.infrastructure.queue.job_manager import JobManager

        manager = JobManager()
        await manager.connect()
        try:
            info = await manager.get_job_info(job_id)
            if info is None:
                raise HTTPException(status_code=404, detail="Job not found")
            return info.to_dict()
        finally:
            await manager.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=public_exception_message(e),
        ) from e


@router.post("/sandbox/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, http_request: Request) -> CancelResponse:
    """
    Cancel a pending job.

    Note: Running jobs cannot be cancelled.
    """
    try:
        from paperbot.infrastructure.queue.job_manager import JobManager

        manager = JobManager()
        await manager.connect()
        try:
            success = await manager.cancel_job(job_id)
            if success:
                return CancelResponse(
                    status="cancelled", job_id=job_id, message="Job cancelled successfully"
                )
            else:
                return CancelResponse(
                    status="failed",
                    job_id=job_id,
                    message="Cannot cancel job (may be running or completed)",
                )
        finally:
            await manager.close()
    except Exception as e:
        return CancelResponse(
            status="error",
            job_id=job_id,
            message=public_exception_message(e),
            trace_id=get_request_trace_id(http_request),
        )


@router.post("/sandbox/jobs/{job_id}/retry")
async def retry_job(job_id: str, http_request: Request) -> RetryResponse:
    """Retry a failed or completed job."""
    try:
        from paperbot.infrastructure.queue.job_manager import JobManager

        manager = JobManager()
        await manager.connect()
        try:
            new_job_id = await manager.retry_job(job_id)
            if new_job_id:
                return RetryResponse(
                    status="enqueued",
                    old_job_id=job_id,
                    new_job_id=new_job_id,
                    message="Job re-enqueued successfully",
                )
            else:
                return RetryResponse(
                    status="failed",
                    old_job_id=job_id,
                    message="Could not retry job (original job not found)",
                )
        finally:
            await manager.close()
    except Exception as e:
        return RetryResponse(
            status="error",
            old_job_id=job_id,
            message=public_exception_message(e),
            trace_id=get_request_trace_id(http_request),
        )


# --- Log Streaming ---


async def _log_stream_generator(run_id: str):
    """Generate SSE events for log streaming."""
    from paperbot.infrastructure.logging.execution_logger import get_execution_logger

    logger = get_execution_logger()

    async for entry in logger.stream_logs(run_id):
        yield StreamEvent(
            type="log",
            data=entry.to_dict(),
        )

    yield StreamEvent(type="done", message="Log stream ended")


@router.get("/sandbox/runs/{run_id}/logs/stream")
async def stream_logs(run_id: str, http_request: Request):
    """
    Stream logs for a run in real-time (SSE).

    Returns Server-Sent Events with log entries.
    """
    return sse_response(
        _log_stream_generator(run_id),
        workflow="sandbox_logs",
        timeout_seconds=None,
    )


@router.get("/sandbox/runs/{run_id}/logs")
async def get_logs(
    run_id: str,
    http_request: Request,
    level: Optional[str] = Query(None, description="Filter by log level"),
    limit: int = Query(500, ge=1, le=5000, description="Max logs to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    """Get historical logs for a run."""
    try:
        from paperbot.infrastructure.logging.execution_logger import get_execution_logger

        logger = get_execution_logger()
        logs = logger.get_logs_dict(run_id, level=level, limit=limit, offset=offset)
        return {"run_id": run_id, "logs": logs, "count": len(logs)}
    except Exception as e:
        return json_internal_error_response(
            http_request,
            exc=e,
            extra={
                "run_id": run_id,
                "logs": [],
                "error": public_exception_message(e),
            },
            log_message="Failed to load sandbox logs",
        )


# --- Resource Metrics ---


async def _metrics_stream_generator(run_id: str):
    """Generate SSE events for metrics streaming."""
    from paperbot.infrastructure.monitoring.resource_monitor import get_resource_monitor

    monitor = get_resource_monitor()

    async for metrics in monitor.stream_metrics(run_id):
        yield StreamEvent(
            type="metrics",
            data=metrics.to_dict(),
        )

    yield StreamEvent(type="done", message="Metrics stream ended")


@router.get("/sandbox/runs/{run_id}/metrics/stream")
async def stream_metrics(run_id: str, http_request: Request):
    """
    Stream resource metrics for a run in real-time (SSE).

    Returns Server-Sent Events with CPU/memory metrics.
    """
    return sse_response(
        _metrics_stream_generator(run_id),
        workflow="sandbox_metrics",
        timeout_seconds=None,
    )


@router.get("/sandbox/runs/{run_id}/metrics")
async def get_metrics(run_id: str, http_request: Request):
    """Get current resource metrics for a run."""
    try:
        from paperbot.infrastructure.monitoring.resource_monitor import get_resource_monitor

        monitor = get_resource_monitor()
        metrics = monitor.get_metrics(run_id)
        if metrics:
            return metrics.to_dict()
        else:
            return {"run_id": run_id, "status": "not_found"}
    except Exception as e:
        return json_internal_error_response(
            http_request,
            exc=e,
            extra={
                "run_id": run_id,
                "error": public_exception_message(e),
            },
            log_message="Failed to load sandbox metrics",
        )


@router.get("/sandbox/runs/{run_id}/metrics/history")
async def get_metrics_history(
    run_id: str,
    http_request: Request,
    limit: int = Query(100, ge=1, le=1000, description="Max metrics to return"),
):
    """Get historical resource metrics for a run."""
    try:
        from paperbot.infrastructure.monitoring.resource_monitor import get_resource_monitor

        monitor = get_resource_monitor()
        history = monitor.get_metrics_history(run_id, limit=limit)
        return {
            "run_id": run_id,
            "metrics": [m.to_dict() for m in history],
            "count": len(history),
        }
    except Exception as e:
        return json_internal_error_response(
            http_request,
            exc=e,
            extra={
                "run_id": run_id,
                "metrics": [],
                "error": public_exception_message(e),
            },
            log_message="Failed to load sandbox metrics history",
        )


# --- System Status ---


@router.get("/sandbox/status")
async def get_system_status(http_request: Request):
    """
    Get overall sandbox system status.

    Returns status for Redis/queue.
    """
    try:
        from paperbot.infrastructure.monitoring.resource_monitor import get_resource_monitor

        monitor = get_resource_monitor()
        status = await monitor.get_system_status()
        return status.to_dict()
    except Exception as e:
        return json_internal_error_response(
            http_request,
            exc=e,
            extra={
                "queue": {
                    "status": "unknown",
                    "error": public_exception_message(e),
                },
            },
            log_message="Failed to load sandbox system status",
        )
