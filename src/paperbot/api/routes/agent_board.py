"""
Agent Board API -- Claude Commander + Codex Workers

Claude decomposes the context pack into tasks, dispatches them to
Codex API workers, reviews results, and manages the Kanban lifecycle.

Phase 1B adds sandbox-as-workspace mode where all agents operate
directly inside the VM.  The file system is the single source of truth.
"""

import asyncio
import json
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...infrastructure.swarm import CodexResult, PersistentSandboxManager, TaskDAG
from ...infrastructure.swarm.agents import ExecutorAgent, KnowledgeManager, PlannerAgent
from ...infrastructure.swarm.paper_slug import paper_slug
from ...infrastructure.swarm.sandbox_runtime import (
    SandboxRunResult,
    SandboxRuntime,
    SandboxVerificationPolicy,
    detect_missing_python_packages,
    summarize_verification_results,
)
from ...infrastructure.swarm.e2e_execution import (
    E2EExecutionPolicy,
    E2EResult,
    run_e2e_with_repair,
)
from ...infrastructure.swarm.sandbox_tool_executor import SandboxToolExecutor
from ...infrastructure.swarm.shared_sandbox import SharedSandbox
from ...infrastructure.swarm.verification import (
    VerificationPolicy,
    run_verification,
)
from ...infrastructure.stores.pipeline_session_store import PipelineSessionStore
from ..streaming import StreamEvent, sse_response

router = APIRouter(prefix="/api/agent-board")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Codex delegation event helpers (Phase 10 / CDX-03)
# ---------------------------------------------------------------------------


def _get_event_log_from_container():
    """Lazily retrieve the event_log from the DI Container.

    Lazy import inside function body prevents circular imports at module load
    time. Returns None if the container is not configured or has no event_log.
    """
    try:
        from paperbot.core.di import Container  # noqa: PLC0415

        return Container.instance().event_log
    except Exception:
        return None


async def _emit_codex_event(
    event_type: str,
    task,
    session,
    extra: dict,
) -> None:
    """Emit a Codex delegation lifecycle event into the event bus.

    Silently returns if the event log is unavailable (None) or if any
    exception occurs. Never raises to callers.

    Args:
        event_type: One of EventType.CODEX_* constants.
        task: The AgentTask being delegated (must have .id, .title, .assignee).
        session: The BoardSession (must have .session_id), or None.
        extra: Additional payload dict merged into the base payload.
    """
    try:
        el = _get_event_log_from_container()
        if el is None:
            return

        from paperbot.application.collaboration.message_schema import (  # noqa: PLC0415
            make_event,
            new_run_id,
            new_trace_id,
        )

        payload = {
            "task_id": task.id,
            "task_title": task.title,
            "session_id": session.session_id if session is not None else None,
            "worker_run_id": task.id,
            "runtime": "codex",
            "control_mode": "managed",
            "interruptible": True,
            "assignee": getattr(task, "assignee", "codex"),
        }
        payload.update(extra)

        env = make_event(
            run_id=new_run_id(),
            trace_id=new_trace_id(),
            workflow="agent_board",
            stage="delegation",
            attempt=0,
            agent_name=getattr(task, "assignee", "codex"),
            role="worker",
            type=event_type,
            payload=payload,
        )
        el.append(env)
    except Exception as exc:  # pragma: no cover
        log.debug("_emit_codex_event failed silently: %s", exc)


# ---------------------------------------------------------------------------
# Persistent session store
# ---------------------------------------------------------------------------
_DEFAULT_WORKSPACE_DIR = Path("/tmp/paperbot-workspace")
_DANGEROUS_WORKSPACE_ROOTS = (
    Path("/etc"),
    Path("/root"),
    Path("/bin"),
    Path("/sbin"),
    Path("/usr"),
    Path("/lib"),
    Path("/lib64"),
    Path("/System"),
    Path("/Library"),
    Path("/Applications"),
)
_board_store: Optional[PipelineSessionStore] = None

# Shared commander instance (preserves wisdom across tasks)
_commander = None
_sandbox_manager = PersistentSandboxManager()


@dataclass
class RunControl:
    session_id: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    state: Literal["running", "paused", "cancelled"] = "running"


_run_controls: Dict[str, RunControl] = {}


async def _check_control(ctrl: RunControl) -> Literal["continue", "cancelled"]:
    """Check pipeline control state; blocks while paused."""
    if ctrl.state == "cancelled":
        return "cancelled"
    while ctrl.state == "paused":
        await asyncio.sleep(1.0)
        if ctrl.state == "cancelled":
            return "cancelled"
    return "continue"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_board_store() -> PipelineSessionStore:
    global _board_store
    if _board_store is None:
        _board_store = PipelineSessionStore(auto_create_schema=True)
    return _board_store


def _get_commander():
    global _commander
    if _commander is None:
        from ...infrastructure.swarm import ClaudeCommander

        _commander = ClaudeCommander()
    return _commander


def _get_dispatcher():
    from ...infrastructure.swarm import CodexDispatcher

    return CodexDispatcher()


def _get_sandbox():
    """Create a sandbox executor for worker run_command, if available."""
    executor_type = os.getenv("PAPERBOT_EXECUTOR", "auto").strip().lower() or "auto"

    if executor_type in {"auto", "e2b"}:
        try:
            from ...repro.e2b_executor import E2BExecutor

            e2b = E2BExecutor()
            if e2b.available():
                return e2b
        except Exception:
            log.debug("E2B executor unavailable", exc_info=True)

    if executor_type in {"auto", "docker"}:
        try:
            from ...repro.docker_executor import DockerExecutor

            docker = DockerExecutor(image="python:3.11-slim", workspace_read_only=False)
            if docker.available():
                return docker
        except Exception:
            log.debug("Docker executor unavailable", exc_info=True)

    if executor_type not in {"auto", "e2b", "docker"}:
        log.warning("Unknown PAPERBOT_EXECUTOR value '%s'; sandbox disabled", executor_type)
    return None


_DEFAULT_GET_SANDBOX = _get_sandbox


def _get_session_sandbox(session: Optional["BoardSession"]) -> Optional[Any]:
    """Resolve sandbox for a session, preferring persistent user sandbox when enabled."""
    if _get_sandbox is not _DEFAULT_GET_SANDBOX:
        # Allow tests to monkeypatch _get_sandbox without matching signature.
        return _get_sandbox()

    mode = _sandbox_mode()
    if mode in {"ephemeral", "legacy"} or session is None:
        return _get_sandbox()

    user_key = _normalize_user_key(session.user_id)
    sandbox, sandbox_id = _sandbox_manager.get_or_create(
        user_key=user_key,
        session_id=session.session_id,
        requested_sandbox_id=session.sandbox_id,
    )
    if sandbox is None:
        return None
    if sandbox_id and sandbox_id != session.sandbox_id:
        session.sandbox_id = sandbox_id
        session.sandbox_executor = sandbox.executor_type
        _append_session_event(
            session,
            event="sandbox_attached",
            level="info",
            message="Session attached to persistent sandbox.",
            details={"sandbox_id": sandbox_id, "executor_type": sandbox.executor_type},
        )
        _persist_session(session, checkpoint="sandbox_attached", status="running")
    return sandbox


def _get_shared_sandbox(session: Optional["BoardSession"]) -> Optional[SharedSandbox]:
    """Get a SharedSandbox for sandbox-as-workspace mode.

    Wraps the raw executor from _get_session_sandbox into a SharedSandbox.
    Returns None if no sandbox is available.
    """
    raw_executor = _get_session_sandbox(session)
    if raw_executor is None:
        return None
    return SharedSandbox(raw_executor)


def _sandbox_workspace_enabled() -> bool:
    """Check if sandbox-as-workspace mode is active (Phase 1B).

    Enabled by default when CODEX_TOOL_USE is not disabled.
    Can be explicitly disabled with PAPERBOT_SANDBOX_WORKSPACE=false.
    """
    explicit = os.getenv("PAPERBOT_SANDBOX_WORKSPACE", "").strip().lower()
    if explicit in {"0", "false", "no", "off"}:
        return False
    if explicit in {"1", "true", "yes", "on"}:
        return True
    # Default: enabled when tool use is enabled
    return os.getenv("CODEX_TOOL_USE", "true").lower() != "false"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class AgentTask(BaseModel):
    id: str
    title: str
    description: str
    status: Literal["planning", "in_progress", "ai_review", "human_review", "done", "paused", "cancelled"] = "planning"
    assignee: str = "claude"
    progress: int = 0
    tags: List[str] = []
    subtasks: List[Dict[str, Any]] = []
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    paper_id: Optional[str] = None
    # Execution metadata
    codex_output: Optional[str] = None
    review_feedback: Optional[str] = None
    generated_files: List[str] = Field(default_factory=list)
    file_snapshots: Dict[str, str] = Field(default_factory=dict)  # path → content for sandbox replay
    execution_log: List[Dict[str, Any]] = Field(default_factory=list)
    human_reviews: List[Dict[str, Any]] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)


class BoardSession:
    def __init__(
        self,
        session_id: str,
        paper_id: str,
        context_pack_id: str,
        workspace_dir: Optional[str] = None,
        user_id: str = "default",
        sandbox_id: Optional[str] = None,
        sandbox_executor: Optional[str] = None,
        paper_slug_name: Optional[str] = None,
        paper_title: Optional[str] = None,
    ):
        self.session_id = session_id
        self.paper_id = paper_id
        self.context_pack_id = context_pack_id
        self.workspace_dir = workspace_dir
        self.user_id = user_id
        self.sandbox_id = sandbox_id
        self.sandbox_executor = sandbox_executor
        self.paper_title = paper_title or ""
        # Generate paper_slug from paper_id + title for sandbox directory namespacing.
        self.paper_slug_name = paper_slug_name or paper_slug(paper_id, self.paper_title)
        self.tasks: List[AgentTask] = []
        self.lifecycle_events: List[Dict[str, Any]] = []
        self.created_at = _now_iso()

    @property
    def sandbox_paper_cwd(self) -> str:
        """Return the sandbox working directory for this paper, e.g. ``/home/user/attn-is-all-you-need-a9b1``."""
        return f"/home/user/{self.paper_slug_name}"

    def to_dict(self):
        return {
            "session_id": self.session_id,
            "paper_id": self.paper_id,
            "context_pack_id": self.context_pack_id,
            "workspace_dir": self.workspace_dir,
            "user_id": self.user_id,
            "sandbox_id": self.sandbox_id,
            "sandbox_executor": self.sandbox_executor,
            "paper_slug_name": self.paper_slug_name,
            "paper_title": self.paper_title,
            "tasks": [t.model_dump() for t in self.tasks],
            "lifecycle_events": self.lifecycle_events,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BoardSession":
        session = cls(
            session_id=str(data.get("session_id", "")),
            paper_id=str(data.get("paper_id", "")),
            context_pack_id=str(data.get("context_pack_id", "")),
            workspace_dir=data.get("workspace_dir"),
            user_id=str(data.get("user_id", "default") or "default"),
            sandbox_id=data.get("sandbox_id"),
            sandbox_executor=data.get("sandbox_executor"),
            paper_slug_name=data.get("paper_slug_name"),
            paper_title=data.get("paper_title", ""),
        )
        session.created_at = str(data.get("created_at", session.created_at))
        tasks_raw = data.get("tasks", [])
        if isinstance(tasks_raw, list):
            session.tasks = [AgentTask.model_validate(item) for item in tasks_raw]
        lifecycle_raw = data.get("lifecycle_events", [])
        if isinstance(lifecycle_raw, list):
            session.lifecycle_events = [entry for entry in lifecycle_raw if isinstance(entry, dict)]
        return session


class PlanRequest(BaseModel):
    paper_id: str
    context_pack_id: str
    workspace_dir: Optional[str] = None
    user_id: str = "default"
    paper_title: Optional[str] = None


class TaskUpdateRequest(BaseModel):
    status: Optional[Literal["planning", "in_progress", "ai_review", "human_review", "done", "paused", "cancelled"]] = None
    progress: Optional[int] = None
    assignee: Optional[str] = None


class CreateTaskRequest(BaseModel):
    title: str
    description: str
    workspace_dir: Optional[str] = None
    assignee: Literal["codex", "opencode"] = "codex"
    tags: List[str] = Field(default_factory=list)


class RunAllRequest(BaseModel):
    workspace_dir: Optional[str] = None
    reset_cancelled: bool = False
    restart: bool = False
    continue_run: bool = False  # Resume: keep done/human_review, reset incomplete


class HumanReviewRequest(BaseModel):
    decision: Literal["approve", "request_changes"]
    notes: Optional[str] = None


class ReleaseSandboxRequest(BaseModel):
    reason: Optional[str] = None


class ArchiveSessionRequest(BaseModel):
    release_sandbox: bool = False
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/sessions")
async def create_session(request: PlanRequest):
    """Create a new agent board session."""
    workspace_dir = (
        str(_sanitize_workspace_dir(request.workspace_dir))
        if request.workspace_dir and request.workspace_dir.strip()
        else None
    )
    session_id = f"board-{uuid.uuid4().hex[:12]}"
    session = BoardSession(
        session_id=session_id,
        paper_id=request.paper_id,
        context_pack_id=request.context_pack_id,
        workspace_dir=workspace_dir,
        user_id=request.user_id,
        paper_title=request.paper_title or "",
    )
    _append_session_event(
        session,
        event="session_created",
        level="info",
        message="Agent board session created.",
        details={"user_id": session.user_id},
    )
    _persist_session(session, checkpoint="created", status="running")
    return session.to_dict()


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Load persisted board session snapshot for UI rehydration."""
    row = _get_board_store().get_session(session_id)
    if not row or row.get("workflow") != "agent_board":
        raise HTTPException(status_code=404, detail="Session not found")

    session = _session_from_store_row(row)
    if not session:
        raise HTTPException(status_code=404, detail="Session snapshot unavailable")
    ctrl = _run_controls.get(session_id)

    return {
        "session_id": session.session_id,
        "paper_id": session.paper_id,
        "context_pack_id": session.context_pack_id,
        "workspace_dir": session.workspace_dir,
        "user_id": session.user_id,
        "sandbox_id": session.sandbox_id,
        "sandbox_executor": session.sandbox_executor,
        "paper_slug_name": session.paper_slug_name,
        "tasks": [task.model_dump() for task in session.tasks],
        "status": str(row.get("status", "running") or "running"),
        "checkpoint": str(row.get("checkpoint", "") or ""),
        "updated_at": row.get("updated_at"),
        "control_state": ctrl.state if ctrl else None,
        "session": session.to_dict(),
    }


@router.get("/sessions/latest/by-paper")
@router.get("/sessions/latest")
async def get_latest_session(paper_id: Optional[str] = None):
    """Return the latest persisted board session, optionally scoped by paper_id."""
    rows = _get_board_store().list_sessions(
        workflow="agent_board",
        limit=_session_scan_limit(),
    )
    for row in rows:
        session = _session_from_store_row(row)
        if not session:
            continue
        if paper_id and session.paper_id != paper_id:
            continue
        ctrl = _run_controls.get(session.session_id)
        return {
            "found": True,
            "session_id": session.session_id,
            "paper_id": session.paper_id,
            "context_pack_id": session.context_pack_id,
            "workspace_dir": session.workspace_dir,
            "user_id": session.user_id,
            "sandbox_id": session.sandbox_id,
            "sandbox_executor": session.sandbox_executor,
            "paper_slug_name": session.paper_slug_name,
            "tasks": [task.model_dump() for task in session.tasks],
            "status": str(row.get("status", "running") or "running"),
            "checkpoint": str(row.get("checkpoint", "") or ""),
            "updated_at": row.get("updated_at"),
            "control_state": ctrl.state if ctrl else None,
            "session": session.to_dict(),
        }

    return {
        "found": False,
        "session_id": None,
        "paper_id": paper_id,
        "context_pack_id": None,
        "workspace_dir": None,
        "user_id": None,
        "sandbox_id": None,
        "sandbox_executor": None,
        "paper_slug_name": None,
        "tasks": [],
        "status": "idle",
        "checkpoint": "",
        "updated_at": None,
        "control_state": None,
        "session": None,
    }


@router.post("/sessions/{session_id}/plan")
async def plan_session(session_id: str):
    """Claude decomposes context pack into tasks (SSE stream)."""
    session = _load_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return sse_response(_plan_stream(session), workflow="agent_board_plan")


@router.post("/sessions/{session_id}/run")
async def run_all_tasks(session_id: str, request: RunAllRequest):
    """Dispatch all planning tasks to Codex workers (SSE stream)."""
    session = _load_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if request.workspace_dir and request.workspace_dir.strip():
        session.workspace_dir = str(_sanitize_workspace_dir(request.workspace_dir))
    elif session.workspace_dir:
        session.workspace_dir = str(_sanitize_workspace_dir(session.workspace_dir))
    else:
        session.workspace_dir = str(_DEFAULT_WORKSPACE_DIR)
    if request.restart:
        # Cancel any previous run for this session so its finally block
        # does not release the sandbox that the new run is about to create.
        old_ctrl = _run_controls.get(session_id)
        if old_ctrl:
            old_ctrl.state = "cancelled"
        # Clear stale sandbox reference so get_or_create builds a fresh one.
        session.sandbox_id = None
        session.sandbox_executor = None
        # Full restart: reset ALL tasks to planning, clear execution artifacts
        for task in session.tasks:
            task.status = "planning"
            task.progress = 0
            task.codex_output = None
            task.review_feedback = None
            task.generated_files = []
            task.file_snapshots = {}
            task.execution_log = []
            task.updated_at = _now_iso()
    elif request.continue_run:
        # Continue: keep completed tasks, reset incomplete ones, fresh sandbox
        old_ctrl = _run_controls.get(session_id)
        if old_ctrl:
            old_ctrl.state = "cancelled"
        session.sandbox_id = None
        session.sandbox_executor = None
        for task in session.tasks:
            if task.status not in ("done", "human_review"):
                task.status = "planning"
                task.progress = 0
                task.codex_output = None
                task.generated_files = []
                task.file_snapshots = {}
                task.execution_log = []
                task.updated_at = _now_iso()
    elif request.reset_cancelled:
        for task in session.tasks:
            if task.status == "cancelled":
                task.status = "planning"
                task.progress = 0
                task.updated_at = _now_iso()

    _persist_session(session, checkpoint="run_requested", status="running")

    ctrl = RunControl(session_id=session_id)
    _run_controls[session_id] = ctrl
    return sse_response(_run_all_stream(session, ctrl), workflow="agent_board_run")


@router.post("/sessions/{session_id}/pause")
async def pause_pipeline(session_id: str):
    """Pause a running pipeline."""
    ctrl = _run_controls.get(session_id)
    if not ctrl:
        raise HTTPException(status_code=409, detail="No active run for this session")
    if ctrl.state != "running":
        raise HTTPException(status_code=409, detail=f"Pipeline is {ctrl.state}, not running")
    ctrl.state = "paused"
    return {"session_id": session_id, "state": "paused"}


@router.post("/sessions/{session_id}/resume")
async def resume_pipeline(session_id: str):
    """Resume a paused pipeline."""
    ctrl = _run_controls.get(session_id)
    if not ctrl:
        raise HTTPException(status_code=409, detail="No active run for this session")
    if ctrl.state != "paused":
        raise HTTPException(status_code=409, detail=f"Pipeline is {ctrl.state}, not paused")
    ctrl.state = "running"
    return {"session_id": session_id, "state": "running"}


@router.post("/sessions/{session_id}/cancel")
async def cancel_pipeline(session_id: str):
    """Cancel a running or paused pipeline.

    If no active RunControl exists (pipeline already finished/errored),
    return success anyway so the frontend can reset its phase state.
    """
    ctrl = _run_controls.get(session_id)
    if not ctrl:
        # Pipeline generator already exited — persist cancelled status so
        # the frontend can recover from its stale "executing" phase.
        session = _load_session(session_id)
        if session:
            _persist_session(session, checkpoint="pipeline_cancelled", status="cancelled")
        return {"session_id": session_id, "state": "cancelled", "already_finished": True}
    ctrl.state = "cancelled"
    return {"session_id": session_id, "state": "cancelled"}


@router.get("/sessions/{session_id}/tasks")
async def list_tasks(session_id: str):
    """List all tasks in a session."""
    session = _load_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return [task.model_dump() for task in session.tasks]


@router.post("/sessions/{session_id}/tasks")
async def create_task(session_id: str, request: CreateTaskRequest):
    """Create an ad-hoc task in an existing session for Studio-triggered delegation."""
    session = _load_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    title = request.title.strip()
    description = request.description.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    if not description:
        raise HTTPException(status_code=400, detail="description is required")
    if request.assignee != "codex":
        raise HTTPException(
            status_code=400,
            detail="Only Codex delegation is wired right now; OpenCode runtime is not available yet.",
        )

    if request.workspace_dir and request.workspace_dir.strip():
        session.workspace_dir = str(_sanitize_workspace_dir(request.workspace_dir))

    tags = [tag.strip() for tag in request.tags if isinstance(tag, str) and tag.strip()]
    if "studio" not in tags:
        tags.append("studio")
    if "ad_hoc" not in tags:
        tags.append("ad_hoc")

    task = AgentTask(
        id=f"task-{uuid.uuid4().hex[:12]}",
        title=title,
        description=description,
        status="planning",
        assignee="claude",
        progress=0,
        tags=tags,
        paper_id=session.paper_id or None,
    )
    session.tasks.append(task)
    _append_session_event(
        session,
        event="task_created",
        level="info",
        message="Studio created an ad-hoc subagent task.",
        details={
            "task_id": task.id,
            "source": "studio_console",
            "assignee_preference": request.assignee,
            "workspace_dir": session.workspace_dir,
        },
    )
    _persist_session(session, checkpoint="task_created", status="running")
    return task.model_dump()


@router.get("/sessions/{session_id}/sandbox")
async def get_session_sandbox(session_id: str, resolve: bool = False):
    """Inspect sandbox metadata for a session/user."""
    session = _load_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    resolved = None
    if resolve:
        resolved = _get_session_sandbox(session)
        if resolved is None:
            _append_session_event(
                session,
                event="sandbox_attach_failed",
                level="warning",
                message="Failed to attach sandbox for session.",
                details={"mode": _sandbox_mode()},
            )
            _persist_session(session, checkpoint="sandbox_attach_failed", status="running")

    mode = _sandbox_mode()
    user_key = _normalize_user_key(session.user_id)
    lease = None
    if mode not in {"ephemeral", "legacy"} and _get_sandbox is _DEFAULT_GET_SANDBOX:
        lease = _sandbox_manager.lease_for_user(user_key)

    lease_payload = _serialize_sandbox_lease(lease)
    sandbox_id = (
        session.sandbox_id
        or (str(getattr(resolved, "sandbox_id", "") or "").strip() or None)
        or (lease_payload.get("sandbox_id") if lease_payload else None)
    )
    executor_type = (
        session.sandbox_executor
        or (str(getattr(resolved, "executor_type", "") or "").strip() or None)
        or (lease_payload.get("executor_type") if lease_payload else None)
    )

    return {
        "session_id": session.session_id,
        "user_id": user_key,
        "mode": mode,
        "resolve_requested": resolve,
        "sandbox": {
            "active": bool(sandbox_id),
            "sandbox_id": sandbox_id,
            "executor_type": executor_type,
            "lease": lease_payload,
        },
    }


@router.post("/sessions/{session_id}/sandbox/release")
async def release_session_sandbox(session_id: str, request: Optional[ReleaseSandboxRequest] = None):
    """Release a user's persistent sandbox and clear stored sandbox metadata."""
    session = _load_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    reason = ((request.reason if request else "") or "").strip() or "manual_release"
    release_result = _release_user_sandbox(session=session, reason=reason)
    refreshed = _load_session(session_id) or session

    return {
        "session_id": refreshed.session_id,
        "user_id": _normalize_user_key(refreshed.user_id),
        "mode": _sandbox_mode(),
        **release_result,
        "session": refreshed.to_dict(),
    }


@router.post("/sessions/{session_id}/archive")
async def archive_session(session_id: str, request: Optional[ArchiveSessionRequest] = None):
    """Archive a session and optionally release the user's sandbox."""
    session = _load_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    release_result = None
    if request and request.release_sandbox:
        release_reason = (request.reason or "").strip() or "archive_with_release"
        release_result = _release_user_sandbox(session=session, reason=release_reason)
        session = _load_session(session_id) or session

    _append_session_event(
        session,
        event="session_archived",
        level="info",
        message="Session archived.",
        details={
            "release_sandbox": bool(request and request.release_sandbox),
            "reason": ((request.reason if request else "") or "").strip() or None,
        },
    )
    _persist_session(session, checkpoint="archived", status="completed")

    return {
        "session_id": session.session_id,
        "status": "completed",
        "checkpoint": "archived",
        "release": release_result,
        "session": session.to_dict(),
    }


# ---------------------------------------------------------------------------
# Sandbox File Browsing (Phase 1B)
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}/sandbox/papers")
async def list_sandbox_papers(session_id: str):
    """List all paper directories in the user's VM."""
    session = _load_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    shared = _get_shared_sandbox(session)
    if not shared or not shared.alive:
        return {"papers": []}
    return {"papers": shared.list_papers()}


@router.get("/sessions/{session_id}/sandbox/files")
async def list_sandbox_files(session_id: str, path: str = "."):
    """List files in the current paper's sandbox directory."""
    session = _load_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    shared = _get_shared_sandbox(session)
    if not shared or not shared.alive:
        raise HTTPException(status_code=503, detail="Sandbox not available")

    files = shared.list_files(session.paper_slug_name, path)
    return {"files": files, "paper_slug": session.paper_slug_name, "path": path}


@router.get("/sessions/{session_id}/sandbox/tree")
async def list_sandbox_tree(session_id: str):
    """Return a recursive file list for rendering a client-side file tree."""
    session = _load_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    shared = _get_shared_sandbox(session)
    if not shared or not shared.alive:
        raise HTTPException(status_code=503, detail="Sandbox not available")

    files = sorted(shared.list_files_recursive(session.paper_slug_name))
    return {
        "files": files,
        "paper_slug": session.paper_slug_name,
    }


@router.get("/sessions/{session_id}/sandbox/file")
async def read_sandbox_file(session_id: str, path: str):
    """Read a file from the current paper's sandbox directory."""
    session = _load_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not path or ".." in path or path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path")

    shared = _get_shared_sandbox(session)
    if not shared or not shared.alive:
        raise HTTPException(status_code=503, detail="Sandbox not available")

    content = shared.read_file(session.paper_slug_name, path)
    if content is None:
        raise HTTPException(status_code=404, detail="File not found")
    return {"path": path, "content": content, "paper_slug": session.paper_slug_name}


@router.post("/tasks/{task_id}/dispatch")
async def dispatch_task(task_id: str):
    """Dispatch a single task to a Codex worker."""
    match = _find_task_with_session(task_id)
    if not match:
        raise HTTPException(status_code=404, detail="Task not found")
    session, task = match

    task.status = "in_progress"
    task.assignee = f"codex-{uuid.uuid4().hex[:4]}"
    task.updated_at = datetime.utcnow().isoformat()
    await _emit_codex_event(
        "codex_dispatched",
        task,
        session,
        {"assignee": task.assignee},
    )
    _persist_session(session, checkpoint="task_dispatched", status="running")

    return task.model_dump()


@router.post("/tasks/{task_id}/execute")
async def execute_task(task_id: str):
    """Execute a single task: Codex runs, Claude reviews (SSE stream)."""
    match = _find_task_with_session(task_id)
    if not match:
        raise HTTPException(status_code=404, detail="Task not found")
    session, task = match

    if session and session.workspace_dir:
        session.workspace_dir = str(_sanitize_workspace_dir(session.workspace_dir))

    return sse_response(_execute_task_stream(task, session), workflow="agent_board_execute")


@router.patch("/tasks/{task_id}")
async def update_task(task_id: str, request: TaskUpdateRequest):
    """Update a task's status or other fields."""
    match = _find_task_with_session(task_id)
    if not match:
        raise HTTPException(status_code=404, detail="Task not found")
    session, task = match

    if request.status:
        task.status = request.status
    if request.progress is not None:
        task.progress = request.progress
    if request.assignee:
        task.assignee = request.assignee
    task.updated_at = datetime.utcnow().isoformat()
    _persist_session(session, checkpoint="task_updated", status="running")

    return task.model_dump()


@router.get("/tasks/{task_id}/workspace-stats")
async def task_workspace_stats(task_id: str):
    """Return workspace file-change stats computed from execution logs."""
    match = _find_task_with_session(task_id)
    if not match:
        raise HTTPException(status_code=404, detail="Task not found")
    _session, task = match

    files_changed = 0
    lines_added = 0
    lines_removed = 0
    last_change: Optional[str] = None
    seen_files: set[str] = set()

    for entry in task.execution_log:
        bt = entry.get("block_type") or _infer_block_type(
            entry.get("phase", ""), entry.get("event", "")
        )
        if bt == "diff":
            fp = (entry.get("details") or {}).get("file_path")
            if fp and fp not in seen_files:
                seen_files.add(fp)
                files_changed += 1
            lines_added += (entry.get("details") or {}).get("lines_added", 0)
            lines_removed += (entry.get("details") or {}).get("lines_removed", 0)
            last_change = entry.get("timestamp") or last_change

    return {
        "files_changed": files_changed,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
        "last_change": last_change,
    }


@router.post("/tasks/{task_id}/human-review")
async def human_review_task(task_id: str, request: HumanReviewRequest):
    """Allow a user to approve or request changes for a human-review task."""
    match = _find_task_with_session(task_id)
    if not match:
        raise HTTPException(status_code=404, detail="Task not found")
    session, task = match

    notes = (request.notes or "").strip()
    review_entry = {
        "id": f"hr-{uuid.uuid4().hex[:8]}",
        "decision": request.decision,
        "notes": notes,
        "timestamp": datetime.utcnow().isoformat(),
    }
    task.human_reviews.append(review_entry)

    if request.decision == "approve":
        task.status = "done"
        task.progress = 100
        task.assignee = "human"
        for sub in task.subtasks:
            sub["done"] = True
        _append_task_log(
            task,
            event="human_approved",
            phase="human_review",
            level="success",
            message="Human approved task.",
            details={"notes": notes},
        )
    else:
        task.status = "planning"
        task.progress = 0
        task.assignee = "claude"
        for sub in task.subtasks:
            sub["done"] = False
        _append_task_log(
            task,
            event="human_requested_changes",
            phase="human_review",
            level="warning",
            message="Human requested changes; task moved back to planning.",
            details={"notes": notes},
        )

    if notes:
        task.review_feedback = _merge_review_feedback(task.review_feedback, notes)

    task.updated_at = datetime.utcnow().isoformat()
    _persist_session(session, checkpoint="human_reviewed", status="running")
    return task.model_dump()


# ---------------------------------------------------------------------------
# SSE Streams
# ---------------------------------------------------------------------------


async def _plan_stream(session: BoardSession) -> AsyncGenerator[StreamEvent, None]:
    """Use Claude to decompose context pack into tasks."""
    yield StreamEvent(
        type="progress",
        data={
            "phase": "planning",
            "message": "Claude is analyzing the context pack...",
        },
    )

    try:
        pack = _load_context_pack(session.context_pack_id)
        if not pack:
            yield StreamEvent(type="error", message="Context pack not found")
            return

        commander = _get_commander()
        tasks_data = await commander.decompose(pack)

        now = datetime.utcnow().isoformat()
        for i, task_data in enumerate(tasks_data):
            task = AgentTask(
                id=f"task-{uuid.uuid4().hex[:8]}",
                title=task_data.get("title", "Untitled"),
                description=task_data.get("description", ""),
                status="planning",
                assignee="claude",
                tags=_build_tags(task_data),
                subtasks=_build_subtasks(i, task_data),
                created_at=now,
                updated_at=now,
                paper_id=session.paper_id,
                dependencies=task_data.get("dependencies", []),
            )
            _append_task_log(
                task,
                event="planned",
                phase="planning",
                level="info",
                message="Task created from context pack decomposition.",
            )
            session.tasks.append(task)
            _persist_session(session, checkpoint="planned", status="running")
            yield StreamEvent(
                type="progress",
                data={"event": "task_created", "task": task.model_dump()},
            )

        # Write plan files to sandbox if available, so ExecutorAgent can
        # read .plan/roadmap.md, .plan/context.md, and .plan/tasks.json.
        # This is best-effort: if the sandbox timed out during planning,
        # the Stage 1.5 fallback in _run_all_stream_sandbox() will write
        # .plan/ from session state before task execution begins.
        try:
            shared = _get_shared_sandbox(session)
            if shared and shared.alive:
                slug = session.paper_slug_name
                shared.ensure_paper_dir(slug)
                planner = PlannerAgent(commander)
                await planner.plan(shared, slug, pack)
                log.info("PlannerAgent wrote .plan/ to sandbox for %s", slug)
        except Exception:
            log.info(
                "Sandbox unavailable during planning (non-fatal); "
                ".plan/ will be written at execution time via fallback guard."
            )

        yield StreamEvent(
            type="result",
            data={
                "tasks_count": len(session.tasks),
                "session_id": session.session_id,
            },
        )
        _persist_session(session, checkpoint="plan_complete", status="running")

    except Exception as exc:
        log.exception("Planning failed")
        _persist_session(session, checkpoint="plan_failed", status="failed")
        yield StreamEvent(type="error", message=str(exc))


async def _run_all_stream(
    session: BoardSession,
    ctrl: RunControl,
) -> AsyncGenerator[StreamEvent, None]:
    """Execute all planning tasks -- routes to sandbox-as-workspace or legacy."""
    # Phase 1B: sandbox-as-workspace pipeline
    if _sandbox_workspace_enabled():
        shared = _get_shared_sandbox(session)
        if shared is not None and shared.alive:
            async for event in _run_all_stream_sandbox(session, shared, ctrl):
                yield event
            return
        else:
            # Fail-closed: no sandbox available in sandbox-as-workspace mode
            yield StreamEvent(
                type="error",
                message=(
                    "Sandbox unavailable. Sandbox-as-workspace mode requires an active "
                    "sandbox (E2B_API_KEY must be set). Set PAPERBOT_SANDBOX_WORKSPACE=false "
                    "to use legacy mode."
                ),
            )
            return

    # Legacy path (Phase 1A): local workspace + sandbox for verification
    async for event in _run_all_stream_legacy(session):
        yield event


async def _run_all_stream_sandbox(
    session: BoardSession,
    shared: SharedSandbox,
    ctrl: RunControl,
) -> AsyncGenerator[StreamEvent, None]:
    """Phase 1B: 6-stage sandbox-as-workspace pipeline.

    Stage 1: Sandbox init (ensure_paper_dir)
    Stage 2: Executors implement code in VM (per-task)
    Stage 3: Per-task verification + repair loop in VM
    Stage 4: End-to-end execution -- run the paper's main entry point,
             Commander-directed repair if it fails
    Stage 5: Knowledge Manager curates .knowledge/
    Stage 6: Download results to local
    """
    try:
        async for event in _run_all_stream_sandbox_inner(session, shared, ctrl):
            yield event
    finally:
        # Only clean up if this run is still the current one.  A newer restart
        # may have replaced _run_controls[session_id] with a fresh RunControl;
        # releasing the sandbox here would kill the new run's sandbox.
        current_ctrl = _run_controls.get(session.session_id)
        if current_ctrl is None or current_ctrl.run_id == ctrl.run_id:
            _run_controls.pop(session.session_id, None)
            try:
                _release_user_sandbox(session=session, reason="pipeline_finished")
            except Exception:
                log.exception("Auto-release sandbox failed for session %s", session.session_id)
        else:
            log.info(
                "Skipping sandbox release for stale run %s (superseded by %s)",
                ctrl.run_id, current_ctrl.run_id,
            )


async def _run_all_stream_sandbox_inner(
    session: BoardSession,
    shared: SharedSandbox,
    ctrl: RunControl,
) -> AsyncGenerator[StreamEvent, None]:
    """Inner body of _run_all_stream_sandbox, wrapped by try/finally for cleanup."""
    slug = session.paper_slug_name
    planning_tasks = [t for t in session.tasks if t.status == "planning"]
    if not planning_tasks:
        yield StreamEvent(type="error", message="No tasks in planning status")
        return

    total = len(planning_tasks)
    commander = _get_commander()
    dispatcher = _get_dispatcher()
    workspace = _sanitize_workspace_dir(session.workspace_dir or str(_DEFAULT_WORKSPACE_DIR))
    max_iterations = _resolve_codex_max_iterations()

    # ── Stage 1: Ensure paper directory ──
    yield StreamEvent(
        type="progress",
        data={
            "phase": "sandbox_init",
            "message": "Initializing sandbox workspace...",
            "paper_slug": slug,
        },
    )
    shared.ensure_paper_dir(slug)
    _append_session_event(
        session,
        event="sandbox_workspace_init",
        level="info",
        message=f"Sandbox workspace initialized at /home/user/{slug}",
        details={"paper_slug": slug, "mode": "sandbox-as-workspace"},
    )
    _persist_session(session, checkpoint="sandbox_init", status="running")

    # ── Stage 1.1: Replay files from completed tasks into new sandbox ──
    # When resuming after a sandbox loss, restore files that were written by
    # previously completed tasks so that new tasks can build on them.
    completed_tasks = [t for t in session.tasks if t.status in ("done", "human_review")]
    if completed_tasks:
        replay_count = 0
        for ct in completed_tasks:
            for fpath, content in (ct.file_snapshots or {}).items():
                shared.write_file(slug, fpath, content)
                replay_count += 1
        if replay_count > 0:
            log.info(
                "Replayed %d file(s) from %d completed task(s) into sandbox",
                replay_count, len(completed_tasks),
            )
            yield StreamEvent(
                type="progress",
                data={
                    "event": "sandbox_files_replayed",
                    "files_replayed": replay_count,
                    "completed_tasks": len(completed_tasks),
                },
            )

    # ── Stage 1.5: Ensure .plan/ exists in VM ──
    # If _plan_stream() ran without a sandbox, .plan/ won't exist yet.
    plan_exists = shared.read_file(slug, ".plan/roadmap.md")
    if not plan_exists:
        log.info("No .plan/ found in VM for %s, writing fallback plan files...", slug)
        pack = _load_context_pack(session.context_pack_id)
        if pack:
            planner = PlannerAgent(commander)
            tasks_data = [
                {
                    "title": t.title,
                    "description": t.description,
                    "difficulty": next(
                        (tag for tag in t.tags if tag in ("easy", "medium", "hard")),
                        "medium",
                    ),
                    "acceptance_criteria": [s.get("title", "") for s in t.subtasks],
                    "dependencies": t.dependencies,
                }
                for t in planning_tasks
            ]
            shared.run_in_paper(slug, "mkdir -p .plan")
            shared.write_file(
                slug, ".plan/roadmap.md", planner._build_roadmap(pack, tasks_data, [])
            )
            shared.write_file(
                slug, ".plan/context.md", planner._build_context_summary(pack)
            )
            shared.write_file(
                slug,
                ".plan/tasks.json",
                json.dumps(tasks_data, indent=2, ensure_ascii=False),
            )

    # ── Stage 2: Execute Tasks via ExecutorAgent (DAG-parallel) ──
    dag = TaskDAG(planning_tasks)
    batches = dag.topological_batches()

    yield StreamEvent(
        type="progress",
        data={
            "phase": "executing",
            "message": f"Executing {total} tasks in {len(batches)} batch(es)...",
            "total": total,
            "batches": len(batches),
            "paper_slug": slug,
        },
    )

    executor_agent = ExecutorAgent(dispatcher)
    _global_task_idx = 0  # running counter for SSE task index

    for batch_idx, batch in enumerate(batches):
        # ── Pipeline control check ──
        sig = await _check_control(ctrl)
        if sig == "cancelled":
            # Cancel all remaining planning/in_progress/paused tasks
            for t in planning_tasks:
                if t.status in ("planning", "in_progress", "paused"):
                    t.status = "cancelled"
                    t.updated_at = _now_iso()
            _persist_session(session, checkpoint="pipeline_cancelled", status="cancelled")
            yield StreamEvent(type="progress", data={
                "event": "pipeline_cancelled",
                "tasks": [t.model_dump() for t in planning_tasks],
            })
            break

        # Refresh sandbox TTL before each batch to prevent timeout during long sessions
        if shared:
            shared.refresh_timeout()

        # ---- Execute all tasks in this batch (parallel if >1) ----
        batch_results: Dict[str, CodexResult] = {}
        batch_task_dicts: Dict[str, dict] = {}

        if len(batch) > 1:
            # -- Parallel execution within batch --
            step_queues: Dict[str, asyncio.Queue[StreamEvent]] = {}
            futures: Dict[str, asyncio.Task] = {}

            for bi, task in enumerate(batch):
                idx = _global_task_idx + bi
                task.status = "in_progress"
                task.assignee = f"codex-{uuid.uuid4().hex[:4]}"
                task.progress = 10
                task.updated_at = _now_iso()
                _append_task_log(
                    task, event="executor_started", phase="executing",
                    level="info", message=f"Executor started for task {idx + 1}/{total} (batch {batch_idx + 1}).",
                )
                _persist_session(session, checkpoint="executor_started", status="running")
                yield StreamEvent(
                    type="progress",
                    data={
                        "event": "executor_started", "task_id": task.id,
                        "task": task.model_dump(), "index": idx, "total": total,
                        "batch": batch_idx, "paper_slug": slug,
                    },
                )

                batch_task_dicts[task.id] = {
                    "title": task.title, "description": task.description,
                    "acceptance_criteria": [s["title"] for s in task.subtasks],
                    "subtasks": [dict(subtask) for subtask in task.subtasks],
                }

                q: asyncio.Queue[StreamEvent] = asyncio.Queue()
                step_queues[task.id] = q

                def _make_on_step(_task: AgentTask, _q: asyncio.Queue) -> Any:
                    async def _on_step(step: int, tool_name: str, args: Dict[str, Any], observation: str) -> None:
                        _task.progress = min(12 + (step * 2), 65)
                        _task.updated_at = _now_iso()
                        obs_preview = observation if len(observation) <= 200 else f"{observation[:200]}..."
                        # Enrich log with typed blocks for UI
                        if tool_name == "write_file":
                            file_path = args.get("path", "")
                            file_content = str(args.get("content", ""))
                            lines_added = file_content.count("\n") + (1 if file_content and not file_content.endswith("\n") else 0)
                            _append_task_log(
                                _task, event="file_write", phase="executing", level="info",
                                message=f"[step {step}] write_file({file_path})",
                                block_type="diff",
                                details={"tool": tool_name, "file_path": file_path, "lines_added": lines_added, "content_preview": file_content[:3000]},
                            )
                        elif tool_name == "task_done":
                            _append_task_log(
                                _task, event="task_done", phase="executing", level="success",
                                message=f"[step {step}] task_done: {args.get('summary', '')}",
                                block_type="result",
                                details={"tool": tool_name, "summary": args.get("summary", "")},
                            )
                        else:
                            _append_task_log(
                                _task, event="tool_call", phase="executing", level="info",
                                message=f"[step {step}] {tool_name}({_summarize_args(args)})",
                                block_type="tool" if tool_name in ("run_command", "read_file", "list_files", "search_files") else None,
                                details={"tool": tool_name, "args_keys": sorted(args.keys()), "observation_preview": obs_preview},
                            )
                        _persist_session(session, checkpoint="tool_step", status="running")
                        await _q.put(StreamEvent(
                            type="progress",
                            data={
                                "event": "tool_step", "task_id": _task.id, "task": _task.model_dump(),
                                "step": step, "tool": tool_name, "observation_preview": obs_preview,
                            },
                        ))
                    return _on_step

                def _make_on_think(_task: AgentTask, _q: asyncio.Queue) -> Any:
                    async def _on_think(step: int, text: str) -> None:
                        _append_task_log(
                            _task, event="thinking", phase="executing", level="info",
                            message=text[:1000],
                            block_type="think",
                        )
                        _persist_session(session, checkpoint="tool_step", status="running")
                        await _q.put(StreamEvent(
                            type="progress",
                            data={
                                "event": "agent_thinking", "task_id": _task.id,
                                "task": _task.model_dump(), "step": step,
                            },
                        ))
                    return _on_think

                wisdom = list(commander.wisdom.learnings) if commander.wisdom.learnings else None
                futures[task.id] = asyncio.create_task(
                    executor_agent.execute(
                        task, shared, slug,
                        on_step=_make_on_step(task, q),
                        on_think=_make_on_think(task, q),
                        wisdom=wisdom, max_iterations=max_iterations,
                    )
                )

            # Drain step-event queues until all futures complete
            _pause_emitted = False
            while any(not f.done() for f in futures.values()) or any(not q.empty() for q in step_queues.values()):
                if ctrl.state == "cancelled":
                    for f in futures.values():
                        f.cancel()
                    break
                if ctrl.state == "paused" and not _pause_emitted:
                    _pause_emitted = True
                    for t in batch:
                        if t.status == "in_progress":
                            t.status = "paused"
                            t.updated_at = _now_iso()
                    yield StreamEvent(type="progress", data={"event": "pipeline_paused", "tasks": [t.model_dump() for t in batch]})
                if ctrl.state == "running" and _pause_emitted:
                    _pause_emitted = False
                    for t in batch:
                        if t.status == "paused":
                            t.status = "in_progress"
                            t.updated_at = _now_iso()
                    yield StreamEvent(type="progress", data={"event": "pipeline_resumed", "tasks": [t.model_dump() for t in batch]})
                for q in step_queues.values():
                    while not q.empty():
                        yield q.get_nowait()
                await asyncio.sleep(0.3)
            for q in step_queues.values():
                while not q.empty():
                    yield q.get_nowait()

            # Collect results
            for task in batch:
                try:
                    result = futures[task.id].result()
                except asyncio.CancelledError:
                    task.status = "cancelled"
                    task.updated_at = _now_iso()
                    result = CodexResult(task_id=task.id, success=False, error="Cancelled")
                except Exception as exc:
                    log.exception("ExecutorAgent failed for task %s", task.id)
                    result = CodexResult(task_id=task.id, success=False, error=str(exc))
                batch_results[task.id] = result

            # If cancelled mid-batch, mark all remaining tasks and emit event
            if ctrl.state == "cancelled":
                for t in planning_tasks:
                    if t.status in ("planning", "in_progress", "paused"):
                        t.status = "cancelled"
                        t.updated_at = _now_iso()
                _persist_session(session, checkpoint="pipeline_cancelled", status="cancelled")
                yield StreamEvent(type="progress", data={
                    "event": "pipeline_cancelled",
                    "tasks": [t.model_dump() for t in planning_tasks],
                })
                break

        # -- Now process each task's result (Stage 3 + review) --
        # For single-task batches, we also execute here.
        for bi, task in enumerate(batch):
            i = _global_task_idx + bi

            task_dict = batch_task_dicts.get(task.id) or {
                "title": task.title, "description": task.description,
                "acceptance_criteria": [s["title"] for s in task.subtasks],
                "subtasks": [dict(subtask) for subtask in task.subtasks],
            }

            if task.id not in batch_results:
                # Single-task batch — execute sequentially (original path)
                task.status = "in_progress"
                task.assignee = f"codex-{uuid.uuid4().hex[:4]}"
                task.progress = 10
                task.updated_at = _now_iso()
                _append_task_log(
                    task, event="executor_started", phase="executing",
                    level="info", message=f"Executor started for task {i + 1}/{total}.",
                )
                _persist_session(session, checkpoint="executor_started", status="running")
                yield StreamEvent(
                    type="progress",
                    data={
                        "event": "executor_started", "task_id": task.id,
                        "task": task.model_dump(), "index": i, "total": total,
                        "paper_slug": slug,
                    },
                )

                seq_queue: asyncio.Queue[StreamEvent] = asyncio.Queue()

                def _make_seq_on_step(_task: AgentTask, _q: asyncio.Queue) -> Any:
                    async def _on_step(step: int, tool_name: str, args: Dict[str, Any], observation: str) -> None:
                        _task.progress = min(12 + (step * 2), 65)
                        _task.updated_at = _now_iso()
                        obs_preview = observation if len(observation) <= 200 else f"{observation[:200]}..."
                        if tool_name == "write_file":
                            file_path = args.get("path", "")
                            file_content = str(args.get("content", ""))
                            lines_added = file_content.count("\n") + (1 if file_content and not file_content.endswith("\n") else 0)
                            _append_task_log(
                                _task, event="file_write", phase="executing", level="info",
                                message=f"[step {step}] write_file({file_path})",
                                block_type="diff",
                                details={"tool": tool_name, "file_path": file_path, "lines_added": lines_added, "content_preview": file_content[:3000]},
                            )
                        elif tool_name == "task_done":
                            _append_task_log(
                                _task, event="task_done", phase="executing", level="success",
                                message=f"[step {step}] task_done: {args.get('summary', '')}",
                                block_type="result",
                                details={"tool": tool_name, "summary": args.get("summary", "")},
                            )
                        else:
                            _append_task_log(
                                _task, event="tool_call", phase="executing", level="info",
                                message=f"[step {step}] {tool_name}({_summarize_args(args)})",
                                block_type="tool" if tool_name in ("run_command", "read_file", "list_files", "search_files") else None,
                                details={"tool": tool_name, "args_keys": sorted(args.keys()), "observation_preview": obs_preview},
                            )
                        _persist_session(session, checkpoint="tool_step", status="running")
                        await _q.put(StreamEvent(
                            type="progress",
                            data={
                                "event": "tool_step", "task_id": _task.id, "task": _task.model_dump(),
                                "step": step, "tool": tool_name, "observation_preview": obs_preview,
                            },
                        ))
                    return _on_step

                def _make_seq_on_think(_task: AgentTask, _q: asyncio.Queue) -> Any:
                    async def _on_think(step: int, text: str) -> None:
                        _append_task_log(
                            _task, event="thinking", phase="executing", level="info",
                            message=text[:1000],
                            block_type="think",
                        )
                        _persist_session(session, checkpoint="tool_step", status="running")
                        await _q.put(StreamEvent(
                            type="progress",
                            data={
                                "event": "agent_thinking", "task_id": _task.id,
                                "task": _task.model_dump(), "step": step,
                            },
                        ))
                    return _on_think

                wisdom = list(commander.wisdom.learnings) if commander.wisdom.learnings else None
                dispatch_future = asyncio.create_task(
                    executor_agent.execute(
                        task, shared, slug,
                        on_step=_make_seq_on_step(task, seq_queue),
                        on_think=_make_seq_on_think(task, seq_queue),
                        wisdom=wisdom, max_iterations=max_iterations,
                    )
                )

                _seq_pause_emitted = False
                while not dispatch_future.done() or not seq_queue.empty():
                    if ctrl.state == "cancelled":
                        dispatch_future.cancel()
                        break
                    if ctrl.state == "paused" and not _seq_pause_emitted:
                        _seq_pause_emitted = True
                        task.status = "paused"
                        task.updated_at = _now_iso()
                        yield StreamEvent(type="progress", data={"event": "pipeline_paused", "tasks": [task.model_dump()]})
                    if ctrl.state == "running" and _seq_pause_emitted:
                        _seq_pause_emitted = False
                        task.status = "in_progress"
                        task.updated_at = _now_iso()
                        yield StreamEvent(type="progress", data={"event": "pipeline_resumed", "tasks": [task.model_dump()]})
                    # While paused, keep draining the queue but don't block
                    if ctrl.state == "paused":
                        await asyncio.sleep(0.5)
                        continue
                    try:
                        event = await asyncio.wait_for(seq_queue.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                    yield event

                try:
                    result = await dispatch_future
                except asyncio.CancelledError:
                    task.status = "cancelled"
                    task.updated_at = _now_iso()
                    result = CodexResult(task_id=task.id, success=False, error="Cancelled")
                except Exception as exc:
                    log.exception("ExecutorAgent failed for task %s", task.id)
                    result = CodexResult(task_id=task.id, success=False, error=str(exc))
            else:
                # Multi-task batch — result already collected
                result = batch_results[task.id]

            # Skip cancelled tasks — don't overwrite their status
            if task.status == "cancelled" or ctrl.state == "cancelled":
                continue

            # ── Common post-execution: update status, verification, review ──
            failure_message, failure_details = _format_codex_failure(result)
            task.generated_files = result.files_generated
            task.file_snapshots = result.file_snapshots or {}
            task.codex_output = result.output if result.success else failure_message
            task.progress = 70
            task.updated_at = _now_iso()
            _persist_session(session, checkpoint="executor_finished", status="running")

            if not result.success:
                task.status = "human_review"
                task.progress = 100
                _append_task_log(
                    task, event="executor_failed", phase="executing",
                    level="error", message=failure_message,
                    details=failure_details or None,
                )
                yield StreamEvent(
                    type="progress",
                    data={
                        "event": "executor_failed", "task_id": task.id,
                        "task": task.model_dump(),
                        "error": failure_message,
                        "diagnostics": result.diagnostics,
                    },
                )
                _persist_session(session, checkpoint="executor_failed", status="running")
                continue

            _append_task_log(
                task, event="executor_finished", phase="executing",
                level="success",
                message=f"Executor completed ({len(result.files_generated)} files).",
                details={"files": result.files_generated},
            )
            yield StreamEvent(
                type="progress",
                data={
                    "event": "executor_finished", "task_id": task.id,
                    "task": task.model_dump(), "files_written": result.files_generated,
                },
            )

            # ── Stage 3: Verification + Repair (per-task) ──
            policy = VerificationPolicy.from_sandbox_env(shared, slug)
            if policy.enabled:
                yield StreamEvent(
                    type="progress",
                    data={
                        "event": "verify_started", "task_id": task.id,
                        "commands": policy.commands,
                    },
                )

                vresult = run_verification(shared, slug, policy, attempt=0)

                if not vresult.passed and policy.max_repair_attempts > 0:
                    repair_tool_exec = SandboxToolExecutor(shared, slug, task)
                    vresult = await asyncio.to_thread(
                        lambda: run_verification(shared, slug, policy, attempt=0)
                    )
                    if not vresult.passed:
                        for repair_attempt in range(1, policy.max_repair_attempts + 1):
                            yield StreamEvent(
                                type="progress",
                                data={
                                    "event": "repair_started", "task_id": task.id,
                                    "attempt": repair_attempt, "reason": vresult.logs[:500],
                                },
                            )
                            repair_prompt = (
                                f"## Repair Attempt {repair_attempt}\n\n"
                                "The previous implementation failed verification.\n\n"
                                f"## Commands: {' && '.join(vresult.commands_run)}\n\n"
                                f"## Failure Output\n```\n{vresult.logs[:6000]}\n```\n\n"
                                "Diagnose the failure, fix the code, and call task_done."
                            )
                            repair_tool_exec = SandboxToolExecutor(shared, slug, task)
                            await dispatcher.dispatch_with_sandbox_tools(
                                task_id=f"repair-{task.id}-{repair_attempt}",
                                prompt=repair_prompt,
                                tool_executor=repair_tool_exec,
                                max_iterations=max_iterations,
                            )
                            vresult = run_verification(shared, slug, policy, attempt=repair_attempt)
                            yield StreamEvent(
                                type="progress",
                                data={
                                    "event": "repair_finished", "task_id": task.id,
                                    "attempt": repair_attempt, "passed": vresult.passed,
                                },
                            )
                            if vresult.passed:
                                break

                yield StreamEvent(
                    type="progress",
                    data={
                        "event": "verify_finished", "task_id": task.id,
                        "passed": vresult.passed, "exit_code": vresult.exit_code,
                        "logs_preview": vresult.logs[:500],
                    },
                )
                _append_task_log(
                    task,
                    event="verify_finished" if vresult.passed else "verify_failed",
                    phase="verification",
                    level="success" if vresult.passed else "warning",
                    message=f"Verification {'passed' if vresult.passed else 'failed'}.",
                    details={"logs": vresult.logs[:2000]},
                )
            else:
                yield StreamEvent(
                    type="progress",
                    data={
                        "event": "verify_skipped", "task_id": task.id,
                        "reason": "no_commands" if not policy.commands else "disabled",
                    },
                )

            # ── Claude Review ──
            task.status = "ai_review"
            task.assignee = "claude"
            task.progress = 85
            task.updated_at = _now_iso()
            _persist_session(session, checkpoint="task_reviewing", status="running")
            yield StreamEvent(
                type="progress",
                data={"event": "task_reviewing", "task_id": task.id, "task": task.model_dump()},
            )

            # Enrich review input with actual file contents from sandbox
            review_input = result.output or ""
            if shared and shared.alive and task.generated_files:
                file_snippets: list[str] = []
                for fpath in task.generated_files[:10]:
                    content = shared.read_file(slug, fpath)
                    if content:
                        file_snippets.append(
                            f"### {fpath}\n```\n{content[:3000]}\n```"
                        )
                if file_snippets:
                    review_input += (
                        "\n\n## Generated Files\n" + "\n\n".join(file_snippets)
                    )

            review = await commander.review(task_dict, review_input)
            task.review_feedback = review.feedback

            if review.approved:
                task.status = "done"
                task.progress = 100
                for sub in task.subtasks:
                    sub["done"] = True
                commander.accumulate_wisdom(task_dict, result.output)
                _append_task_log(
                    task, event="task_approved", phase="ai_review",
                    level="success", message="Claude approved task.",
                    details={"feedback": review.feedback},
                )
            else:
                task.status = "human_review"
                task.progress = 100
                for sub in task.subtasks:
                    sub["done"] = True
                _append_task_log(
                    task, event="task_needs_review", phase="ai_review",
                    level="warning", message="Claude requested human review.",
                    details={"feedback": review.feedback},
                )

            task.updated_at = _now_iso()
            _persist_session(session, checkpoint="task_reviewed", status="running")
            yield StreamEvent(
                type="progress",
                data={
                    "event": "task_reviewed", "task_id": task.id,
                    "task": task.model_dump(), "approved": review.approved,
                    "feedback": review.feedback,
                },
            )

        _global_task_idx += len(batch)

        # If cancelled during this batch, mark remaining tasks and break
        if ctrl.state == "cancelled":
            for t in planning_tasks:
                if t.status in ("planning", "in_progress", "paused"):
                    t.status = "cancelled"
                    t.updated_at = _now_iso()
            _persist_session(session, checkpoint="pipeline_cancelled", status="cancelled")
            yield StreamEvent(type="progress", data={
                "event": "pipeline_cancelled",
                "tasks": [t.model_dump() for t in planning_tasks],
            })
            break

    # If already cancelled from the batch loop, skip remaining stages
    if ctrl.state == "cancelled":
        yield StreamEvent(type="progress", data={"event": "sandbox_auto_released"})
        return

    # ── KV-cache stats ──
    yield StreamEvent(
        type="progress",
        data={
            "event": "cache_stats",
            "phase": "executing",
            "cache_hit_rate": dispatcher.cache_metrics.hit_rate,
            "total_prompt_tokens": dispatcher.cache_metrics.total_prompt_tokens,
            "cached_prompt_tokens": dispatcher.cache_metrics.cached_prompt_tokens,
        },
    )
    log.info("Session %s: %s", session.session_id, dispatcher.cache_metrics.report())

    # ── Control check before Stage 4 ──
    if (await _check_control(ctrl)) == "cancelled":
        for t in session.tasks:
            if t.status in ("planning", "in_progress", "paused"):
                t.status = "cancelled"
                t.updated_at = _now_iso()
        _persist_session(session, checkpoint="pipeline_cancelled", status="cancelled")
        yield StreamEvent(type="progress", data={
            "event": "pipeline_cancelled",
            "tasks": [t.model_dump() for t in session.tasks],
        })
        yield StreamEvent(type="progress", data={"event": "sandbox_auto_released"})
        return

    # ── Stage 3.5: Retry incomplete tasks before E2E ──
    # Tasks that are still in "planning" (never dispatched, e.g. due to API errors)
    # must be attempted before E2E — otherwise E2E runs on incomplete code.
    _MAX_TASK_RETRIES = 2
    for _retry_round in range(_MAX_TASK_RETRIES):
        incomplete = [t for t in session.tasks if t.status == "planning"]
        if not incomplete:
            break
        if (await _check_control(ctrl)) == "cancelled":
            break

        log.info(
            "Session %s: %d incomplete task(s) before E2E, retry round %d",
            session.session_id, len(incomplete), _retry_round + 1,
        )
        yield StreamEvent(type="progress", data={
            "event": "retrying_incomplete_tasks",
            "count": len(incomplete),
            "retry_round": _retry_round + 1,
        })

        if shared:
            shared.refresh_timeout()

        for task in incomplete:
            if (await _check_control(ctrl)) == "cancelled":
                break

            task.status = "in_progress"
            task.assignee = f"codex-retry-{uuid.uuid4().hex[:4]}"
            task.progress = 10
            task.updated_at = _now_iso()
            _append_task_log(
                task, event="executor_retry", phase="executing",
                level="info", message=f"Retrying task (round {_retry_round + 1}).",
            )
            _persist_session(session, checkpoint="executor_retry", status="running")
            yield StreamEvent(type="progress", data={
                "event": "executor_started", "task_id": task.id,
                "task": task.model_dump(), "paper_slug": slug,
            })

            task_dict = {
                "title": task.title, "description": task.description,
                "acceptance_criteria": [s["title"] for s in task.subtasks],
                "subtasks": [dict(s) for s in task.subtasks],
            }
            retry_queue: asyncio.Queue[StreamEvent] = asyncio.Queue()

            def _make_retry_on_step(_task: AgentTask, _q: asyncio.Queue) -> Any:
                async def _on_step(step: int, tool_name: str, args: Dict[str, Any], observation: str) -> None:
                    _task.progress = min(12 + (step * 2), 65)
                    _task.updated_at = _now_iso()
                    obs_preview = observation if len(observation) <= 200 else f"{observation[:200]}..."
                    _persist_session(session, checkpoint="tool_step", status="running")
                    await _q.put(StreamEvent(type="progress", data={
                        "event": "tool_step", "task_id": _task.id, "task": _task.model_dump(),
                        "step": step, "tool": tool_name, "observation_preview": obs_preview,
                    }))
                return _on_step

            wisdom = list(commander.wisdom.learnings) if commander.wisdom.learnings else None
            retry_future = asyncio.create_task(
                executor_agent.execute(
                    task, shared, slug,
                    on_step=_make_retry_on_step(task, retry_queue),
                    wisdom=wisdom, max_iterations=max_iterations,
                )
            )

            while not retry_future.done() or not retry_queue.empty():
                if ctrl.state == "cancelled":
                    retry_future.cancel()
                    break
                try:
                    event = await asyncio.wait_for(retry_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                yield event

            try:
                result = await retry_future
            except asyncio.CancelledError:
                task.status = "cancelled"
                task.updated_at = _now_iso()
                result = CodexResult(task_id=task.id, success=False, error="Cancelled")
            except Exception as exc:
                log.exception("Retry failed for task %s", task.id)
                result = CodexResult(task_id=task.id, success=False, error=str(exc))

            if task.status == "cancelled" or ctrl.state == "cancelled":
                continue

            task.generated_files = result.files_generated
            task.codex_output = result.output if result.success else (result.error or "Retry failed")
            task.progress = 100
            for sub in task.subtasks:
                sub["done"] = True
            if result.success:
                task.status = "done"
                _append_task_log(task, event="task_approved", phase="ai_review", level="success", message="Task completed on retry.")
            else:
                task.status = "human_review"
                _append_task_log(task, event="executor_failed", phase="executing", level="error", message=result.error or "Retry failed")

            task.updated_at = _now_iso()
            _persist_session(session, checkpoint="retry_finished", status="running")
            yield StreamEvent(type="progress", data={
                "event": "task_reviewed", "task_id": task.id, "task": task.model_dump(),
            })

    # ── Stage 4: End-to-End Execution (always runs) ──
    # E2E should never be skipped. If no entry point is detected, ask Commander
    # to instruct Codex to generate one, then proceed with execution + repair loop.
    # Refresh sandbox TTL before E2E — tasks may have taken a long time.
    if shared:
        shared.refresh_timeout()
    context_pack_raw = getattr(session, "context_pack", None)
    e2e_policy = E2EExecutionPolicy.from_context(
        shared, slug, context_pack=context_pack_raw, allow_missing_entry_point=True
    )

    if e2e_policy.enabled and not e2e_policy.entry_point and not e2e_policy.entry_command:
        # No entry point detected — ask Codex to generate one
        yield StreamEvent(
            type="progress",
            data={
                "event": "e2e_generating_entry",
                "paper_slug": slug,
                "message": "No entry point detected, generating main.py...",
            },
        )
        gen_prompt = (
            "The project has no detectable entry point (no main.py, train.py, etc.).\n"
            "Examine the existing code with list_files and read_file, then create a "
            "main.py that imports and runs the core logic end-to-end.\n"
            "The entry point should:\n"
            "1. Import the key modules/classes from the project\n"
            "2. Run the main pipeline/training/experiment with sensible defaults\n"
            "3. Print results to stdout so we can verify success\n"
            "Call task_done when the entry point is ready."
        )
        try:
            gen_tool_exec = SandboxToolExecutor(shared, slug)
            await dispatcher.dispatch_with_sandbox_tools(
                task_id="e2e-generate-entry",
                prompt=gen_prompt,
                tool_executor=gen_tool_exec,
            )
            # Re-detect after generation
            from ...infrastructure.swarm.e2e_execution import detect_entry_point
            detected = detect_entry_point(shared, slug)
            if detected:
                e2e_policy.entry_point = detected
                log.info("Generated entry point: %s", detected)
            else:
                # Fallback: assume main.py was created
                e2e_policy.entry_point = "main.py"
                log.warning("Entry point generation did not produce detectable entry, defaulting to main.py")
        except Exception as exc:
            log.exception("Failed to generate entry point")
            e2e_policy.entry_point = "main.py"

    if e2e_policy.enabled:
        from ...infrastructure.swarm.e2e_execution import build_run_command

        run_cmd = build_run_command(e2e_policy)
        yield StreamEvent(
            type="progress",
            data={
                "event": "e2e_started",
                "paper_slug": slug,
                "entry_point": e2e_policy.entry_point or e2e_policy.entry_command,
                "command": run_cmd,
                "max_attempts": e2e_policy.max_repair_attempts + 1,
            },
        )

        async def _on_e2e_attempt(result: E2EResult) -> None:
            yield_event = StreamEvent(
                type="progress",
                data={
                    "event": "e2e_attempt",
                    "attempt": result.attempt,
                    "success": result.success,
                    "exit_code": result.exit_code,
                    "duration_sec": result.duration_sec,
                    "stdout_preview": result.stdout[:500],
                    "stderr_preview": result.stderr[:500],
                },
            )
            await e2e_events_queue.put(yield_event)

        e2e_events_queue: asyncio.Queue[StreamEvent] = asyncio.Queue()

        def _e2e_tool_exec_factory() -> SandboxToolExecutor:
            return SandboxToolExecutor(shared, slug)

        e2e_future = asyncio.create_task(
            run_e2e_with_repair(
                sandbox=shared,
                paper_slug=slug,
                policy=e2e_policy,
                dispatcher=dispatcher,
                tool_executor_factory=_e2e_tool_exec_factory,
                on_attempt=_on_e2e_attempt,
            )
        )

        _e2e_pause_emitted = False
        while not e2e_future.done() or not e2e_events_queue.empty():
            if ctrl.state == "cancelled":
                e2e_future.cancel()
                break
            if ctrl.state == "paused" and not _e2e_pause_emitted:
                _e2e_pause_emitted = True
                yield StreamEvent(type="progress", data={"event": "pipeline_paused"})
            if ctrl.state == "running" and _e2e_pause_emitted:
                _e2e_pause_emitted = False
                yield StreamEvent(type="progress", data={"event": "pipeline_resumed"})
            if ctrl.state == "paused":
                await asyncio.sleep(0.5)
                continue
            try:
                event = await asyncio.wait_for(e2e_events_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            yield event

        try:
            e2e_result = await e2e_future
        except Exception as exc:
            log.exception("E2E execution failed unexpectedly")
            e2e_result = None
            yield StreamEvent(
                type="progress",
                data={"event": "e2e_error", "error": str(exc)},
            )

        if e2e_result:
            yield StreamEvent(
                type="progress",
                data={
                    "event": "e2e_finished",
                    "success": e2e_result.success,
                    "entry_point": e2e_result.entry_point,
                    "command": e2e_result.command,
                    "exit_code": e2e_result.exit_code,
                    "total_attempts": e2e_result.attempt + 1,
                    "duration_sec": e2e_result.duration_sec,
                    "stdout_preview": e2e_result.stdout[:1000],
                },
            )
            _append_session_event(
                session,
                event="e2e_execution_complete",
                level="success" if e2e_result.success else "warning",
                message=(
                    f"E2E execution {'succeeded' if e2e_result.success else 'failed'} "
                    f"after {e2e_result.attempt + 1} attempt(s): {e2e_result.command}"
                ),
                details={
                    "entry_point": e2e_result.entry_point,
                    "exit_code": e2e_result.exit_code,
                    "repair_history": e2e_result.repair_history,
                },
            )
        _persist_session(session, checkpoint="e2e_complete", status="running")

    # ── Control check before Stage 5 ──
    if (await _check_control(ctrl)) == "cancelled":
        _persist_session(session, checkpoint="pipeline_cancelled", status="cancelled")
        yield StreamEvent(type="progress", data={
            "event": "pipeline_cancelled",
            "tasks": [t.model_dump() for t in session.tasks],
        })
        yield StreamEvent(type="progress", data={"event": "sandbox_auto_released"})
        return

    # ── Stage 5: Knowledge Manager ──
    try:
        km = KnowledgeManager(commander)
        written = await km.curate(shared, slug, planning_tasks)
        yield StreamEvent(
            type="progress",
            data={
                "event": "knowledge_curated",
                "paper_slug": slug,
                "files_written": list(written.values()),
            },
        )
    except Exception as exc:
        log.exception("Knowledge Manager failed")
        yield StreamEvent(
            type="progress",
            data={"event": "knowledge_failed", "error": str(exc)},
        )

    # ── Stage 6: Download results to local ──
    # Download if any task succeeded (done) or executor produced output (human_review)
    done_count = sum(1 for t in session.tasks if t.status == "done")
    has_output = sum(
        1 for t in session.tasks
        if t.status in ("done", "human_review") and t.generated_files
    )
    any_passed = done_count > 0 or has_output > 0

    if any_passed:
        try:
            local_dir = workspace / slug
            downloaded = shared.download_paper(slug, local_dir)
            downloaded_sorted = sorted(downloaded)
            yield StreamEvent(
                type="progress",
                data={
                    "event": "download_complete",
                    "paper_slug": slug,
                    "files_count": len(downloaded),
                    "local_dir": str(local_dir),
                    "files_downloaded": downloaded_sorted,
                },
            )
            _append_session_event(
                session,
                event="download_complete",
                level="success",
                message=f"Downloaded {len(downloaded)} files to {local_dir}",
                details={"files": downloaded_sorted[:20]},
            )
        except Exception as exc:
            log.exception("Download failed")
            yield StreamEvent(
                type="progress",
                data={"event": "download_failed", "error": str(exc)},
            )
    else:
        yield StreamEvent(
            type="progress",
            data={
                "event": "download_skipped",
                "reason": "no_successful_tasks",
                "paper_slug": slug,
            },
        )

    yield StreamEvent(
        type="result",
        data={
            "completed": done_count,
            "total": total,
            "session_id": session.session_id,
            "paper_slug": slug,
            "mode": "sandbox-as-workspace",
        },
    )
    final_status = "completed" if done_count == total else "running"
    _persist_session(session, checkpoint="run_complete", status=final_status)
    yield StreamEvent(type="progress", data={"event": "sandbox_auto_released"})


async def _run_all_stream_legacy(
    session: BoardSession,
) -> AsyncGenerator[StreamEvent, None]:
    """Legacy (Phase 1A): Execute tasks using local workspace + sandbox verification."""
    planning_tasks = [t for t in session.tasks if t.status == "planning"]
    if not planning_tasks:
        yield StreamEvent(type="error", message="No tasks in planning status")
        return

    total = len(planning_tasks)
    yield StreamEvent(
        type="progress",
        data={
            "phase": "dispatching",
            "message": f"Dispatching {total} tasks to Codex workers...",
            "total": total,
        },
    )

    commander = _get_commander()
    dispatcher = _get_dispatcher()
    sandbox = _get_session_sandbox(session)
    if sandbox is not None and hasattr(sandbox, "sandbox_cwd"):
        sandbox.sandbox_cwd = session.sandbox_paper_cwd
    max_iterations = _resolve_codex_max_iterations()
    workspace = _sanitize_workspace_dir(session.workspace_dir or str(_DEFAULT_WORKSPACE_DIR))

    for i, task in enumerate(planning_tasks):
        # --- Dispatch to Codex ---
        task.status = "in_progress"
        task.assignee = f"codex-{uuid.uuid4().hex[:4]}"
        task.updated_at = datetime.utcnow().isoformat()
        task.progress = 10
        _append_task_log(
            task,
            event="task_dispatched",
            phase="dispatching",
            level="info",
            message=f"Dispatched to {task.assignee}.",
        )
        _persist_session(session, checkpoint="task_dispatched", status="running")

        yield StreamEvent(
            type="progress",
            data={
                "event": "task_dispatched",
                "task_id": task.id,
                "task": task.model_dump(),
                "index": i,
                "total": total,
            },
        )

        # Build prompt and execute
        task_dict = {
            "title": task.title,
            "description": task.description,
            "acceptance_criteria": [s["title"] for s in task.subtasks],
            "subtasks": [dict(subtask) for subtask in task.subtasks],
        }
        prompt = await commander.build_codex_prompt(task_dict, workspace)
        result, step_events = await _dispatch_with_step_events(
            dispatcher=dispatcher,
            task=task,
            session=session,
            prompt=prompt,
            workspace=workspace,
            sandbox=sandbox,
            max_iterations=max_iterations,
            include_task_id=True,
        )
        for event in step_events:
            yield event

        result, verify_events = await _run_sandbox_verification_and_repair(
            task=task,
            session=session,
            commander=commander,
            dispatcher=dispatcher,
            task_dict=task_dict,
            current_result=result,
            workspace=workspace,
            sandbox=sandbox,
            max_iterations=max_iterations,
            include_task_id=True,
        )
        for event in verify_events:
            yield event

        failure_message, failure_details = _format_codex_failure(result)
        task.progress = 70
        task.codex_output = result.output if result.success else failure_message
        task.generated_files = list(result.files_generated)
        task.updated_at = datetime.utcnow().isoformat()
        _persist_session(session, checkpoint="task_codex_done", status="running")

        if not result.success:
            task.status = "human_review"
            task.progress = 100
            _append_task_log(
                task,
                event="task_failed",
                phase="codex_running",
                level="error",
                message=failure_message,
                details=failure_details or None,
            )
            yield StreamEvent(
                type="progress",
                data={
                    "event": "task_failed",
                    "task_id": task.id,
                    "task": task.model_dump(),
                    "error": failure_message,
                    "diagnostics": result.diagnostics,
                },
            )
            _persist_session(session, checkpoint="task_failed", status="running")
            continue

        yield StreamEvent(
            type="progress",
            data={
                "event": "task_codex_done",
                "task_id": task.id,
                "task": task.model_dump(),
                "output_length": len(result.output),
            },
        )
        _append_task_log(
            task,
            event="task_codex_done",
            phase="codex_running",
            level="success",
            message=f"Codex output received ({len(result.output)} chars).",
        )
        if task.generated_files:
            _append_task_log(
                task,
                event="files_written",
                phase="codex_running",
                level="success",
                message=f"Wrote {len(task.generated_files)} file(s) to workspace.",
                details={"files": task.generated_files},
            )

        # --- Claude reviews ---
        task.status = "ai_review"
        task.assignee = "claude"
        task.progress = 85
        task.updated_at = datetime.utcnow().isoformat()
        _append_task_log(
            task,
            event="task_reviewing",
            phase="ai_review",
            level="info",
            message="Claude started review.",
        )

        yield StreamEvent(
            type="progress",
            data={
                "event": "task_reviewing",
                "task_id": task.id,
                "task": task.model_dump(),
            },
        )

        # Run review with periodic heartbeats
        review = None
        review_coro = commander.review(task_dict, result.output)
        review_task = asyncio.ensure_future(review_coro)
        while not review_task.done():
            await asyncio.sleep(5)
            if not review_task.done():
                _append_task_log(
                    task,
                    event="heartbeat",
                    phase="review_running",
                    level="info",
                    message="Review still running.",
                )
                yield StreamEvent(
                    type="progress",
                    data={
                        "event": "heartbeat",
                        "task_id": task.id,
                        "task": task.model_dump(),
                        "phase": "review_running",
                    },
                )
        review = review_task.result()
        task.review_feedback = review.feedback

        if review.approved:
            task.status = "done"
            task.progress = 100
            # Mark all subtasks as done
            for sub in task.subtasks:
                sub["done"] = True
            commander.accumulate_wisdom(task_dict, result.output)
            _append_task_log(
                task,
                event="task_reviewed",
                phase="ai_review",
                level="success",
                message="Claude approved task.",
                details={"feedback": review.feedback},
            )
        else:
            task.status = "human_review"
            task.progress = 90
            _append_task_log(
                task,
                event="task_reviewed",
                phase="ai_review",
                level="warning",
                message="Claude requested human review.",
                details={"feedback": review.feedback},
            )

        task.updated_at = datetime.utcnow().isoformat()
        _persist_session(session, checkpoint="task_reviewed", status="running")

        yield StreamEvent(
            type="progress",
            data={
                "event": "task_reviewed",
                "task_id": task.id,
                "task": task.model_dump(),
                "approved": review.approved,
                "feedback": review.feedback,
            },
        )

    done_count = sum(1 for t in session.tasks if t.status == "done")
    yield StreamEvent(
        type="result",
        data={
            "completed": done_count,
            "total": total,
            "session_id": session.session_id,
        },
    )
    final_status = "completed" if done_count == total else "running"
    _persist_session(session, checkpoint="run_complete", status=final_status)


async def _execute_task_stream(
    task: AgentTask,
    session: Optional["BoardSession"],
) -> AsyncGenerator[StreamEvent, None]:
    """Execute a single task -- routes to sandbox-as-workspace or legacy."""
    # Phase 1B: sandbox-as-workspace
    if session and _sandbox_workspace_enabled():
        shared = _get_shared_sandbox(session)
        if shared is not None and shared.alive:
            async for event in _execute_task_stream_sandbox(task, session, shared):
                yield event
            return
        else:
            yield StreamEvent(
                type="error",
                message="Sandbox unavailable. Cannot execute in sandbox-as-workspace mode.",
            )
            return

    # Legacy path
    commander = _get_commander()
    dispatcher = _get_dispatcher()
    sandbox = _get_session_sandbox(session)
    if sandbox is not None and session is not None and hasattr(sandbox, "sandbox_cwd"):
        sandbox.sandbox_cwd = session.sandbox_paper_cwd
    max_iterations = _resolve_codex_max_iterations()
    workspace = _sanitize_workspace_dir(
        (session.workspace_dir if session else None) or str(_DEFAULT_WORKSPACE_DIR)
    )

    # Dispatch
    task.status = "in_progress"
    task.assignee = f"codex-{uuid.uuid4().hex[:4]}"
    task.progress = 10
    task.updated_at = datetime.utcnow().isoformat()
    _append_task_log(
        task,
        event="task_dispatched",
        phase="dispatching",
        level="info",
        message=f"Dispatched to {task.assignee}.",
    )
    if session:
        _persist_session(session, checkpoint="task_dispatched", status="running")

    await _emit_codex_event(
        "codex_dispatched",
        task,
        session,
        {"assignee": task.assignee},
    )

    yield StreamEvent(
        type="progress",
        data={"event": "task_dispatched", "task": task.model_dump()},
    )

    task_dict = {
        "title": task.title,
        "description": task.description,
        "acceptance_criteria": [s["title"] for s in task.subtasks],
        "subtasks": [dict(subtask) for subtask in task.subtasks],
    }
    await _emit_codex_event(
        "codex_accepted",
        task,
        session,
        {"assignee": task.assignee, "model": "codex"},
    )
    prompt = await commander.build_codex_prompt(task_dict, workspace)
    result, step_events = await _dispatch_with_step_events(
        dispatcher=dispatcher,
        task=task,
        session=session,
        prompt=prompt,
        workspace=workspace,
        sandbox=sandbox,
        max_iterations=max_iterations,
        include_task_id=False,
    )
    for event in step_events:
        yield event

    result, verify_events = await _run_sandbox_verification_and_repair(
        task=task,
        session=session,
        commander=commander,
        dispatcher=dispatcher,
        task_dict=task_dict,
        current_result=result,
        workspace=workspace,
        sandbox=sandbox,
        max_iterations=max_iterations,
        include_task_id=False,
    )
    for event in verify_events:
        yield event

    failure_message, failure_details = _format_codex_failure(result)
    task.progress = 70
    task.codex_output = result.output if result.success else failure_message
    task.generated_files = list(result.files_generated)
    task.updated_at = datetime.utcnow().isoformat()
    if session:
        _persist_session(session, checkpoint="task_codex_done", status="running")

    if not result.success:
        task.status = "human_review"
        task.progress = 100
        _append_task_log(
            task,
            event="task_failed",
            phase="codex_running",
            level="error",
            message=failure_message,
            details=failure_details or None,
        )
        await _emit_codex_event(
            "codex_failed",
            task,
            session,
            {
                "assignee": task.assignee,
                "reason_code": result.diagnostics.get("reason_code", "unknown"),
                "error": str(result.error or ""),
            },
        )
        yield StreamEvent(
            type="progress",
            data={
                "event": "task_failed",
                "task": task.model_dump(),
                "error": failure_message,
                "diagnostics": result.diagnostics,
            },
        )
        if session:
            _persist_session(session, checkpoint="task_failed", status="running")
        yield StreamEvent(type="result", data={"success": False})
        return

    if task.generated_files:
        _append_task_log(
            task,
            event="files_written",
            phase="codex_running",
            level="success",
            message=f"Wrote {len(task.generated_files)} file(s) to workspace.",
            details={"files": task.generated_files},
        )

    await _emit_codex_event(
        "codex_completed",
        task,
        session,
        {
            "assignee": task.assignee,
            "files_generated": task.generated_files,
            "output_preview": (result.output or "")[:200],
        },
    )

    # Review
    task.status = "ai_review"
    task.assignee = "claude"
    task.progress = 85
    task.updated_at = datetime.utcnow().isoformat()
    if session:
        _persist_session(session, checkpoint="task_reviewed", status="running")
    _append_task_log(
        task,
        event="task_reviewing",
        phase="ai_review",
        level="info",
        message="Claude started review.",
    )

    yield StreamEvent(
        type="progress",
        data={"event": "task_reviewing", "task": task.model_dump()},
    )

    review = await commander.review(task_dict, result.output)
    task.review_feedback = review.feedback

    if review.approved:
        task.status = "done"
        task.progress = 100
        for sub in task.subtasks:
            sub["done"] = True
        commander.accumulate_wisdom(task_dict, result.output)
        _append_task_log(
            task,
            event="task_reviewed",
            phase="ai_review",
            level="success",
            message="Claude approved task.",
            details={"feedback": review.feedback},
        )
    else:
        task.status = "human_review"
        task.progress = 90
        _append_task_log(
            task,
            event="task_reviewed",
            phase="ai_review",
            level="warning",
            message="Claude requested human review.",
            details={"feedback": review.feedback},
        )

    task.updated_at = datetime.utcnow().isoformat()

    yield StreamEvent(
        type="progress",
        data={
            "event": "task_reviewed",
            "task": task.model_dump(),
            "approved": review.approved,
            "feedback": review.feedback,
        },
    )
    yield StreamEvent(type="result", data={"success": review.approved})
    if session:
        _persist_session(session, checkpoint="task_execute_complete", status="running")


async def _execute_task_stream_sandbox(
    task: AgentTask,
    session: BoardSession,
    shared: SharedSandbox,
) -> AsyncGenerator[StreamEvent, None]:
    """Phase 1B: Execute a single task in sandbox-as-workspace mode."""
    slug = session.paper_slug_name
    commander = _get_commander()
    dispatcher = _get_dispatcher()
    max_iterations = _resolve_codex_max_iterations()

    shared.ensure_paper_dir(slug)

    # Dispatch
    task.status = "in_progress"
    task.assignee = f"codex-{uuid.uuid4().hex[:4]}"
    task.progress = 10
    task.updated_at = _now_iso()
    _append_task_log(
        task, event="executor_started", phase="executing", level="info",
        message=f"Dispatched to {task.assignee} (sandbox-as-workspace).",
    )
    _persist_session(session, checkpoint="executor_started", status="running")
    yield StreamEvent(type="progress", data={"event": "executor_started", "task": task.model_dump()})

    tool_exec = SandboxToolExecutor(shared, slug, task)
    task_dict = {
        "title": task.title,
        "description": task.description,
        "acceptance_criteria": [s["title"] for s in task.subtasks],
        "subtasks": [dict(subtask) for subtask in task.subtasks],
    }

    # Build prompt
    plan_content = shared.read_file(slug, ".plan/roadmap.md") or ""
    prompt_parts = [
        f"# Task: {task.title}",
        "",
        "## Goal",
        task.description,
        "",
    ]
    if task.subtasks:
        prompt_parts.append("## Subtasks")
        for sub in task.subtasks:
            marker = "x" if sub.get("done") else " "
            prompt_parts.append(f"- [{marker}] {sub.get('id', '')}: {sub.get('title', '')}")
        prompt_parts.append("")
    if plan_content:
        prompt_parts.append("## Roadmap")
        prompt_parts.append(plan_content[:2000])
        prompt_parts.append("")
    prompt_parts.append(
        "You are working inside a VM sandbox. Write code, run it, fix errors, "
        "then call task_done with a summary."
    )
    prompt = "\n".join(prompt_parts)

    # Execute with step events
    step_queue: asyncio.Queue[StreamEvent] = asyncio.Queue()

    async def _on_step(step: int, tool_name: str, args: Dict[str, Any], obs: str) -> None:
        task.progress = min(12 + (step * 2), 65)
        task.updated_at = _now_iso()
        preview = obs if len(obs) <= 200 else f"{obs[:200]}..."
        if tool_name == "write_file":
            file_path = args.get("path", "")
            file_content = str(args.get("content", ""))
            lines_added = file_content.count("\n") + (1 if file_content and not file_content.endswith("\n") else 0)
            _append_task_log(
                task, event="file_write", phase="executing", level="info",
                message=f"[step {step}] write_file({file_path})",
                block_type="diff",
                details={"tool": tool_name, "file_path": file_path, "lines_added": lines_added, "content_preview": file_content[:3000]},
            )
        elif tool_name == "task_done":
            _append_task_log(
                task, event="task_done", phase="executing", level="success",
                message=f"[step {step}] task_done: {args.get('summary', '')}",
                block_type="result",
                details={"tool": tool_name, "summary": args.get("summary", "")},
            )
        else:
            _append_task_log(
                task, event="tool_call", phase="executing", level="info",
                message=f"[step {step}] {tool_name}({_summarize_args(args)})",
                block_type="tool" if tool_name in ("run_command", "read_file", "list_files", "search_files") else None,
                details={"tool": tool_name, "observation_preview": preview},
            )
        _persist_session(session, checkpoint="tool_step", status="running")
        await step_queue.put(
            StreamEvent(type="progress", data={
                "event": "tool_step", "task_id": task.id, "step": step,
                "tool": tool_name, "observation_preview": preview,
            })
        )

    async def _on_think(step: int, text: str) -> None:
        _append_task_log(
            task, event="thinking", phase="executing", level="info",
            message=text[:1000],
            block_type="think",
        )
        _persist_session(session, checkpoint="tool_step", status="running")
        await step_queue.put(
            StreamEvent(type="progress", data={
                "event": "agent_thinking", "task_id": task.id, "step": step,
            })
        )

    dispatch_future = asyncio.create_task(
        dispatcher.dispatch_with_sandbox_tools(
            task_id=task.id, prompt=prompt, tool_executor=tool_exec,
            on_step=_on_step, on_think=_on_think, max_iterations=max_iterations,
        )
    )

    while not dispatch_future.done() or not step_queue.empty():
        try:
            event = await asyncio.wait_for(step_queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        yield event

    try:
        result = await dispatch_future
    except Exception as exc:
        result = CodexResult(task_id=task.id, success=False, error=str(exc))

    merged = list(dict.fromkeys([*result.files_generated, *tool_exec.files_written]))
    result.files_generated = merged
    task.generated_files = merged
    failure_message, failure_details = _format_codex_failure(result)
    task.codex_output = result.output if result.success else failure_message
    task.progress = 70
    task.updated_at = _now_iso()
    _persist_session(session, checkpoint="executor_finished", status="running")

    if not result.success:
        task.status = "human_review"
        task.progress = 100
        _append_task_log(task, event="executor_failed", phase="executing", level="error",
                         message=failure_message, details=failure_details or None)
        yield StreamEvent(type="progress", data={
            "event": "executor_failed",
            "task": task.model_dump(),
            "error": failure_message,
            "diagnostics": result.diagnostics,
        })
        yield StreamEvent(type="result", data={"success": False})
        return

    # Review
    task.status = "ai_review"
    task.assignee = "claude"
    task.progress = 85
    task.updated_at = _now_iso()
    _persist_session(session, checkpoint="task_reviewing", status="running")
    yield StreamEvent(type="progress", data={"event": "task_reviewing", "task": task.model_dump()})

    review = await commander.review(task_dict, result.output)
    task.review_feedback = review.feedback

    if review.approved:
        task.status = "done"
        task.progress = 100
        for sub in task.subtasks:
            sub["done"] = True
        commander.accumulate_wisdom(task_dict, result.output)
    else:
        task.status = "human_review"
        task.progress = 90

    task.updated_at = _now_iso()
    _persist_session(session, checkpoint="task_reviewed", status="running")
    yield StreamEvent(type="progress", data={
        "event": "task_reviewed", "task": task.model_dump(),
        "approved": review.approved, "feedback": review.feedback,
    })
    yield StreamEvent(type="result", data={"success": review.approved})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auto_repair_enabled() -> bool:
    raw = os.getenv("CODEX_ENABLE_AUTO_REPAIR", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


async def _dispatch_with_step_events(
    *,
    dispatcher: Any,
    task: AgentTask,
    session: Optional["BoardSession"],
    prompt: str,
    workspace: Path,
    sandbox: Any,
    max_iterations: int,
    include_task_id: bool,
) -> tuple[CodexResult, List[StreamEvent]]:
    step_events: asyncio.Queue[StreamEvent] = asyncio.Queue()
    emitted: List[StreamEvent] = []

    async def _on_step(step: int, tool_name: str, args: Dict[str, Any], observation: str) -> None:
        task.progress = min(12 + (step * 2), 65)
        task.updated_at = _now_iso()
        obs_preview = observation if len(observation) <= 200 else f"{observation[:200]}..."
        if tool_name == "write_file":
            file_path = args.get("path", "")
            file_content = str(args.get("content", ""))
            lines_added = file_content.count("\n") + (1 if file_content and not file_content.endswith("\n") else 0)
            _append_task_log(
                task, event="file_write", phase="codex_running", level="info",
                message=f"[step {step}] write_file({file_path})",
                block_type="diff",
                details={"tool": tool_name, "file_path": file_path, "lines_added": lines_added, "content_preview": file_content[:3000]},
            )
        elif tool_name == "task_done":
            _append_task_log(
                task, event="task_done", phase="codex_running", level="success",
                message=f"[step {step}] task_done: {args.get('summary', '')}",
                block_type="result",
                details={"tool": tool_name, "summary": args.get("summary", "")},
            )
        else:
            _append_task_log(
                task, event="tool_call", phase="codex_running", level="info",
                message=f"[step {step}] {tool_name}({_summarize_args(args)})",
                block_type="tool" if tool_name in ("run_command", "read_file", "list_files", "search_files") else None,
                details={"tool": tool_name, "args_keys": sorted(args.keys()), "observation_preview": obs_preview},
            )
        if session:
            _persist_session(session, checkpoint="tool_step", status="running")
        payload: Dict[str, Any] = {
            "event": "tool_step",
            "task": task.model_dump(),
            "step": step,
            "tool": tool_name,
            "args": args,
            "observation_preview": obs_preview,
        }
        if include_task_id:
            payload["task_id"] = task.id
        await step_events.put(StreamEvent(type="progress", data=payload))

    dispatch_future = asyncio.create_task(
        dispatcher.dispatch_auto(
            task.id,
            prompt,
            workspace,
            sandbox=sandbox,
            task=task,
            on_step=_on_step,
            max_iterations=max_iterations,
        )
    )

    while not dispatch_future.done() or not step_events.empty():
        try:
            event = await asyncio.wait_for(step_events.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        emitted.append(event)

    try:
        result = await dispatch_future
    except Exception as exc:
        log.exception("Codex dispatch task failed for task %s", task.id)
        result = CodexResult(task_id=task.id, success=False, error=str(exc))

    return result, emitted


async def _run_sandbox_verification_and_repair(
    *,
    task: AgentTask,
    session: Optional["BoardSession"],
    commander: Any,
    dispatcher: Any,
    task_dict: Dict[str, Any],
    current_result: CodexResult,
    workspace: Path,
    sandbox: Any,
    max_iterations: int,
    include_task_id: bool,
) -> tuple[CodexResult, List[StreamEvent]]:
    events: List[StreamEvent] = []
    runtime = SandboxRuntime(executor=sandbox, workspace=workspace)
    policy = SandboxVerificationPolicy.from_env(workspace, sandbox_available=runtime.available())
    available = runtime.available()
    executor_type = sandbox.executor_type if sandbox is not None else "none"
    sandbox_id = str(getattr(sandbox, "sandbox_id", "") or "").strip() or None

    _append_task_log(
        task,
        event="sandbox_init",
        phase="codex_running",
        level="info" if available else "warning",
        message=f"Sandbox runtime initialized ({executor_type}, available={available}).",
        details={
            "executor_type": executor_type,
            "available": available,
            "verify_enabled": policy.enabled,
            "sandbox_id": sandbox_id,
        },
    )
    if session:
        _persist_session(session, checkpoint="sandbox_ready", status="running")
    init_payload: Dict[str, Any] = {
        "event": "sandbox_init",
        "task": task.model_dump(),
        "executor_type": executor_type,
        "available": available,
        "verify_enabled": policy.enabled,
        "sandbox_id": sandbox_id,
    }
    if include_task_id:
        init_payload["task_id"] = task.id
    events.append(StreamEvent(type="progress", data=init_payload))

    if not current_result.success:
        return current_result, events

    if not policy.enabled:
        skip_payload: Dict[str, Any] = {
            "event": "sandbox_verify_skipped",
            "task": task.model_dump(),
            "reason": "verification_disabled_or_sandbox_unavailable",
        }
        if include_task_id:
            skip_payload["task_id"] = task.id
        events.append(StreamEvent(type="progress", data=skip_payload))
        return current_result, events

    if not policy.commands:
        skip_payload = {
            "event": "sandbox_verify_skipped",
            "task": task.model_dump(),
            "reason": "no_verification_commands_resolved",
        }
        if include_task_id:
            skip_payload["task_id"] = task.id
        events.append(StreamEvent(type="progress", data=skip_payload))
        return current_result, events

    total_attempts = policy.max_retries + 1
    result = current_result

    for attempt in range(1, total_attempts + 1):
        bootstrap_results: List[SandboxRunResult] = []
        dependency_install_result: Optional[SandboxRunResult] = None
        dependency_install_packages: List[str] = []

        if policy.bootstrap_commands:
            bootstrap_start_payload: Dict[str, Any] = {
                "event": "sandbox_bootstrap_started",
                "task": task.model_dump(),
                "attempt": attempt,
                "commands": policy.bootstrap_commands,
            }
            if include_task_id:
                bootstrap_start_payload["task_id"] = task.id
            events.append(StreamEvent(type="progress", data=bootstrap_start_payload))

            bootstrap_results = await runtime.run_commands(
                policy.bootstrap_commands,
                timeout_seconds=policy.bootstrap_timeout_seconds,
            )
            bootstrap_passed = bool(bootstrap_results) and all(item.success for item in bootstrap_results)
            bootstrap_summary = summarize_verification_results(bootstrap_results)
            _append_task_log(
                task,
                event="sandbox_bootstrap_finished" if bootstrap_passed else "sandbox_bootstrap_failed",
                phase="codex_running",
                level="success" if bootstrap_passed else "warning",
                message=(
                    f"Sandbox bootstrap passed on attempt {attempt}."
                    if bootstrap_passed
                    else f"Sandbox bootstrap failed on attempt {attempt}."
                ),
                details={
                    "attempt": attempt,
                    "summary": bootstrap_summary,
                },
            )
            bootstrap_finish_payload: Dict[str, Any] = {
                "event": "sandbox_bootstrap_finished"
                if bootstrap_passed
                else "sandbox_bootstrap_failed",
                "task": task.model_dump(),
                "attempt": attempt,
                "passed": bootstrap_passed,
                "summary": bootstrap_summary,
            }
            if include_task_id:
                bootstrap_finish_payload["task_id"] = task.id
            events.append(StreamEvent(type="progress", data=bootstrap_finish_payload))

        start_payload: Dict[str, Any] = {
            "event": "sandbox_verify_started",
            "task": task.model_dump(),
            "attempt": attempt,
            "commands": policy.commands,
        }
        if include_task_id:
            start_payload["task_id"] = task.id
        events.append(StreamEvent(type="progress", data=start_payload))

        verify_results = await runtime.run_commands(
            policy.commands,
            timeout_seconds=policy.timeout_seconds,
        )
        verify_passed = bool(verify_results) and all(item.success for item in verify_results)

        if not verify_passed:
            # Derive local module names from files the agent generated so
            # we never try to ``pip install`` a project-local directory like
            # ``src`` or ``pipeline``.
            _known_local: set[str] = set()
            for fpath in task.generated_files or []:
                top = fpath.split("/")[0].lower()
                if top:
                    _known_local.add(top.removesuffix(".py"))

            dependency_install_packages = detect_missing_python_packages(
                verify_results,
                workspace=workspace,
                known_local_modules=_known_local,
            )
            if dependency_install_packages:
                install_cmd = f"pip install -q {' '.join(dependency_install_packages)}"
                dep_start_payload: Dict[str, Any] = {
                    "event": "sandbox_dependency_install_started",
                    "task": task.model_dump(),
                    "attempt": attempt,
                    "packages": dependency_install_packages,
                    "command": install_cmd,
                }
                if include_task_id:
                    dep_start_payload["task_id"] = task.id
                events.append(StreamEvent(type="progress", data=dep_start_payload))

                dependency_install_result = await runtime.run_command(
                    install_cmd,
                    timeout_seconds=policy.bootstrap_timeout_seconds,
                )
                dep_passed = dependency_install_result.success
                _append_task_log(
                    task,
                    event=(
                        "sandbox_dependency_install_finished"
                        if dep_passed
                        else "sandbox_dependency_install_failed"
                    ),
                    phase="codex_running",
                    level="success" if dep_passed else "warning",
                    message=(
                        f"Dependency install succeeded on attempt {attempt}: "
                        f"{', '.join(dependency_install_packages)}"
                        if dep_passed
                        else f"Dependency install failed on attempt {attempt}: "
                        f"{', '.join(dependency_install_packages)}"
                    ),
                    details={
                        "attempt": attempt,
                        "packages": dependency_install_packages,
                        "command": install_cmd,
                        "exit_code": dependency_install_result.exit_code,
                    },
                )
                dep_finish_payload: Dict[str, Any] = {
                    "event": (
                        "sandbox_dependency_install_finished"
                        if dep_passed
                        else "sandbox_dependency_install_failed"
                    ),
                    "task": task.model_dump(),
                    "attempt": attempt,
                    "packages": dependency_install_packages,
                    "passed": dep_passed,
                    "exit_code": dependency_install_result.exit_code,
                }
                if include_task_id:
                    dep_finish_payload["task_id"] = task.id
                events.append(StreamEvent(type="progress", data=dep_finish_payload))

                if dep_passed:
                    verify_results = await runtime.run_commands(
                        policy.commands,
                        timeout_seconds=policy.timeout_seconds,
                    )
                    verify_passed = bool(verify_results) and all(item.success for item in verify_results)

        verify_summary = summarize_verification_results(verify_results)
        verify_details = _verification_details_for_repair(verify_results)
        report_rel = _persist_verification_report(
            workspace=workspace,
            task_id=task.id,
            attempt=attempt,
            bootstrap_results=bootstrap_results,
            verify_results=verify_results,
            dependency_install_result=dependency_install_result,
            dependency_install_packages=dependency_install_packages,
            summary=verify_summary,
            passed=verify_passed,
        )
        if report_rel and report_rel not in task.generated_files:
            task.generated_files.append(report_rel)
        if report_rel and report_rel not in result.files_generated:
            result.files_generated.append(report_rel)

        _append_task_log(
            task,
            event="sandbox_verify_finished" if verify_passed else "sandbox_verify_failed",
            phase="codex_running",
            level="success" if verify_passed else "warning",
            message=(
                f"Sandbox verification passed on attempt {attempt}."
                if verify_passed
                else f"Sandbox verification failed on attempt {attempt}."
            ),
            details={
                "attempt": attempt,
                "summary": verify_summary,
                "report": report_rel,
            },
        )
        if session:
            _persist_session(
                session,
                checkpoint="sandbox_verify" if verify_passed else "sandbox_verify_failed",
                status="running",
            )

        finish_payload: Dict[str, Any] = {
            "event": "sandbox_verify_finished" if verify_passed else "sandbox_verify_failed",
            "task": task.model_dump(),
            "attempt": attempt,
            "passed": verify_passed,
            "summary": verify_summary,
            "report": report_rel,
        }
        if include_task_id:
            finish_payload["task_id"] = task.id
        events.append(StreamEvent(type="progress", data=finish_payload))

        if verify_passed:
            return result, events

        if attempt >= total_attempts or not _auto_repair_enabled():
            failure_message = (
                "Sandbox verification failed and retries were exhausted.\n\n" f"{verify_summary}"
            )
            if result.output:
                result.output = f"{result.output}\n\n{failure_message}".strip()
            result.error = failure_message
            result.success = False
            exhausted_payload: Dict[str, Any] = {
                "event": "repair_exhausted",
                "task": task.model_dump(),
                "attempt": attempt,
                "summary": verify_summary,
            }
            if include_task_id:
                exhausted_payload["task_id"] = task.id
            events.append(StreamEvent(type="progress", data=exhausted_payload))
            return result, events

        repair_prompt = await _build_repair_prompt(
            commander=commander,
            task_dict=task_dict,
            workspace=workspace,
            verify_summary=verify_summary,
            verify_details=verify_details,
            attempt=attempt,
        )
        repair_start_payload: Dict[str, Any] = {
            "event": "repair_attempt_started",
            "task": task.model_dump(),
            "attempt": attempt,
            "reason": verify_summary,
        }
        if include_task_id:
            repair_start_payload["task_id"] = task.id
        events.append(StreamEvent(type="progress", data=repair_start_payload))

        repair_result, repair_step_events = await _dispatch_with_step_events(
            dispatcher=dispatcher,
            task=task,
            session=session,
            prompt=repair_prompt,
            workspace=workspace,
            sandbox=sandbox,
            max_iterations=max_iterations,
            include_task_id=include_task_id,
        )
        events.extend(repair_step_events)
        result = repair_result
        merged_files = list(dict.fromkeys([*task.generated_files, *repair_result.files_generated]))
        task.generated_files = merged_files
        result.files_generated = merged_files
        repair_finish_payload: Dict[str, Any] = {
            "event": "repair_attempt_finished",
            "task": task.model_dump(),
            "attempt": attempt,
            "success": repair_result.success,
            "error": repair_result.error,
        }
        if include_task_id:
            repair_finish_payload["task_id"] = task.id
        events.append(StreamEvent(type="progress", data=repair_finish_payload))

        if repair_result.success:
            task.codex_output = repair_result.output
            task.generated_files = repair_result.files_generated

    return result, events


async def _build_repair_prompt(
    *,
    commander: Any,
    task_dict: Dict[str, Any],
    workspace: Path,
    verify_summary: str,
    verify_details: str,
    attempt: int,
) -> str:
    build_method = getattr(commander, "build_codex_repair_prompt", None)
    if callable(build_method):
        try:
            maybe = build_method(
                task_dict,
                workspace,
                verify_summary=verify_summary,
                verify_details=verify_details,
                attempt=attempt,
            )
        except TypeError:
            # Backward compatibility with older commander signatures.
            maybe = build_method(
                task_dict,
                workspace,
                verify_summary=verify_summary,
                attempt=attempt,
            )
        if asyncio.iscoroutine(maybe):
            return await maybe
        return str(maybe)

    base_prompt = await commander.build_codex_prompt(task_dict, workspace)
    return (
        f"{base_prompt}\n\n"
        f"## Sandbox Verification Failure (Attempt {attempt})\n"
        f"{verify_summary}\n\n"
        "## Detailed Failure Output\n"
        f"{verify_details}\n\n"
        "Fix the failing tests/errors shown above, then call task_done again."
    )


def _verification_details_for_repair(
    verify_results: List[SandboxRunResult], *, max_chars: int = 12000
) -> str:
    if not verify_results:
        return "No verification output was captured."

    lines: List[str] = []
    for item in verify_results:
        lines.append(f"$ {item.command}")
        lines.append(f"exit_code={item.exit_code} status={item.status}")
        if item.error:
            lines.append(f"error: {item.error}")
        if item.logs:
            lines.append("```text")
            lines.append(item.logs)
            lines.append("```")
        lines.append("")

    details = "\n".join(lines).strip()
    if len(details) > max_chars:
        return details[:max_chars].rstrip() + "\n...[truncated]"
    return details


def _persist_verification_report(
    *,
    workspace: Path,
    task_id: str,
    attempt: int,
    bootstrap_results: Optional[List[SandboxRunResult]] = None,
    verify_results: List[SandboxRunResult],
    dependency_install_result: Optional[SandboxRunResult] = None,
    dependency_install_packages: Optional[List[str]] = None,
    summary: str,
    passed: bool,
) -> str:
    review_rel = Path("reviews") / f"{task_id}-sandbox-verify-attempt-{attempt}.md"
    review_dest = workspace / review_rel
    review_dest.parent.mkdir(parents=True, exist_ok=True)

    lines: List[str] = [
        f"# Sandbox Verification Report: {task_id}",
        "",
        f"- Attempt: {attempt}",
        f"- Status: {'passed' if passed else 'failed'}",
        "",
        "## Summary",
        summary or "(empty)",
        "",
        "## Command Results",
    ]
    if bootstrap_results:
        lines.append("")
        lines.append("### Bootstrap")
        for item in bootstrap_results:
            lines.append("")
            lines.append(f"#### `{item.command}`")
            lines.append(f"- Exit code: {item.exit_code}")
            lines.append(f"- Status: {item.status}")
            if item.error:
                lines.append(f"- Error: {item.error}")
            if item.logs:
                lines.append("```text")
                lines.append(item.logs)
                lines.append("```")

    if dependency_install_result is not None:
        lines.append("")
        lines.append("### Dependency Auto-Install")
        if dependency_install_packages:
            lines.append(f"- Packages: {', '.join(dependency_install_packages)}")
        lines.append(f"- Exit code: {dependency_install_result.exit_code}")
        lines.append(f"- Status: {dependency_install_result.status}")
        if dependency_install_result.error:
            lines.append(f"- Error: {dependency_install_result.error}")
        if dependency_install_result.logs:
            lines.append("```text")
            lines.append(dependency_install_result.logs)
            lines.append("```")

    lines.append("")
    lines.append("### Verify")
    if verify_results:
        for item in verify_results:
            lines.append("")
            lines.append(f"#### `{item.command}`")
            lines.append(f"- Exit code: {item.exit_code}")
            lines.append(f"- Status: {item.status}")
            if item.error:
                lines.append(f"- Error: {item.error}")
            if item.logs:
                lines.append("```text")
                lines.append(item.logs)
                lines.append("```")
    else:
        lines.append("- No command results were produced.")

    review_dest.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return review_rel.as_posix()


def _resolve_codex_max_iterations() -> int:
    raw = os.getenv("CODEX_MAX_ITERATIONS", "100").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 100
    return max(1, min(value, 100))


def _format_codex_failure(result: CodexResult) -> tuple[str, Dict[str, Any]]:
    """Build a user-facing failure message and structured details from CodexResult."""
    base = (result.error or "Codex execution failed.").strip()
    diagnostics = result.diagnostics if isinstance(result.diagnostics, dict) else {}
    reason = str(diagnostics.get("reason_code", "")).strip()

    reason_map = {
        "stagnation_detected": "stagnation_detected",
        "max_iterations_exhausted": "max_iterations_exhausted",
        "repeated_tool_calls": "repeated_tool_calls",
        "too_many_tool_errors": "too_many_tool_errors",
        "terminated_finish_reason": "terminated_finish_reason",
        "unsupported_finish_reason": "unsupported_finish_reason",
        "empty_choices": "empty_choices",
        "missing_tool_calls": "missing_tool_calls",
    }
    reason_label = reason_map.get(reason, reason)

    message = base if not reason_label else f"{base} (reason={reason_label})"
    details: Dict[str, Any] = {}
    if diagnostics:
        details["codex_diagnostics"] = diagnostics
    return message, details


def _summarize_args(args: Dict[str, Any]) -> str:
    if not args:
        return ""

    parts: List[str] = []
    for key in sorted(args.keys()):
        value = args[key]
        if isinstance(value, str):
            compact = value.replace("\n", " ")
            if len(compact) > 40:
                compact = f"{compact[:40]}..."
            rendered = f"'{compact}'"
        else:
            rendered = repr(value)
        parts.append(f"{key}={rendered}")
        if len(parts) >= 3:
            break

    extra = len(args) - len(parts)
    if extra > 0:
        parts.append(f"...+{extra} more")
    return ", ".join(parts)


def _infer_block_type(phase: str, event: str) -> str:
    """Map (phase, event) to a UI block type for the task detail view."""
    if event in ("thinking", "reasoning"):
        return "think"
    if event in ("file_write", "file_edit"):
        return "diff"
    if event in (
        "verify_result", "review_result", "executor_finished", "task_done",
        "human_approved", "human_rejected", "human_requested_changes",
    ):
        return "result"
    if event in ("tool_call", "shell_exec", "run_command"):
        return "tool"
    return "info"


def _append_task_log(
    task: AgentTask,
    *,
    event: str,
    phase: str,
    message: str,
    level: str = "info",
    details: Optional[Dict[str, Any]] = None,
    block_type: Optional[str] = None,
) -> None:
    entry: Dict[str, Any] = {
        "id": f"log-{uuid.uuid4().hex[:8]}",
        "timestamp": datetime.utcnow().isoformat(),
        "event": event,
        "phase": phase,
        "level": level,
        "message": message,
        "block_type": block_type or _infer_block_type(phase, event),
    }
    if details:
        entry["details"] = details
    task.execution_log.append(entry)
    # Keep bounded to avoid unbounded memory growth in long sessions.
    if len(task.execution_log) > 500:
        task.execution_log = task.execution_log[-500:]


def _append_session_event(
    session: BoardSession,
    *,
    event: str,
    message: str,
    level: str = "info",
    details: Optional[Dict[str, Any]] = None,
) -> None:
    entry: Dict[str, Any] = {
        "id": f"se-{uuid.uuid4().hex[:8]}",
        "timestamp": _now_iso(),
        "event": event,
        "level": level,
        "message": message,
    }
    if details:
        entry["details"] = details
    session.lifecycle_events.append(entry)
    if len(session.lifecycle_events) > 200:
        session.lifecycle_events = session.lifecycle_events[-200:]


def _merge_review_feedback(existing: Optional[str], notes: str) -> str:
    if not existing:
        return f"Human feedback: {notes}"
    return f"{existing}\n\nHuman feedback: {notes}"


def _load_context_pack(context_pack_id: str) -> Optional[dict]:
    try:
        from ...infrastructure.stores.repro_context_store import (
            SqlAlchemyReproContextStore,
        )

        store = SqlAlchemyReproContextStore()
        return store.get(context_pack_id)
    except Exception:
        log.exception("Failed to load context pack %s", context_pack_id)
        return None


def _build_tags(task_data: dict) -> List[str]:
    tags = []
    difficulty = task_data.get("difficulty", task_data.get("estimated_difficulty"))
    if difficulty:
        tags.append(difficulty)
    return tags


def _build_subtasks(index: int, task_data: dict) -> List[Dict[str, Any]]:
    subtasks = []
    criteria = task_data.get("acceptance_criteria", [])
    if isinstance(criteria, list):
        for j, criterion in enumerate(criteria):
            subtasks.append(
                {
                    "id": f"sub-{index}-{j}",
                    "title": criterion if isinstance(criterion, str) else str(criterion),
                    "done": False,
                }
            )
    return subtasks


def _sandbox_mode() -> str:
    return (os.getenv("PAPERBOT_SANDBOX_MODE", "persistent") or "").strip().lower() or "persistent"


def _normalize_user_key(user_id: Optional[str]) -> str:
    return (user_id or "default").strip() or "default"


def _serialize_sandbox_lease(lease: Optional[Any]) -> Optional[Dict[str, Any]]:
    if lease is None:
        return None
    return {
        "user_key": str(getattr(lease, "user_key", "") or ""),
        "session_id": str(getattr(lease, "session_id", "") or ""),
        "executor_type": str(getattr(lease, "executor_type", "") or ""),
        "sandbox_id": str(getattr(lease, "sandbox_id", "") or "") or None,
        "updated_at": str(getattr(lease, "updated_at", "") or ""),
    }


def _release_user_sandbox(*, session: BoardSession, reason: str) -> Dict[str, Any]:
    user_key = _normalize_user_key(session.user_id)
    mode = _sandbox_mode()
    released = False
    released_sandbox_id = session.sandbox_id
    released_executor = session.sandbox_executor

    if mode not in {"ephemeral", "legacy"} and _get_sandbox is _DEFAULT_GET_SANDBOX:
        lease = _sandbox_manager.lease_for_user(user_key)
        if lease is not None:
            released = True
            released_sandbox_id = released_sandbox_id or lease.sandbox_id
            released_executor = released_executor or lease.executor_type
        _sandbox_manager.terminate(user_key=user_key)
    else:
        sandbox = _get_sandbox()
        if sandbox is not None:
            released_sandbox_id = released_sandbox_id or getattr(sandbox, "sandbox_id", None)
            released_executor = released_executor or getattr(sandbox, "executor_type", None)
            cleanup = getattr(sandbox, "cleanup", None)
            if callable(cleanup):
                cleanup()
                released = True

    cleared_sessions = _clear_sandbox_metadata_for_user(user_key=user_key, reason=reason)
    return {
        "released": released or cleared_sessions > 0,
        "released_sandbox_id": released_sandbox_id,
        "released_executor_type": released_executor,
        "cleared_sessions": cleared_sessions,
        "reason": reason,
    }


def _clear_sandbox_metadata_for_user(*, user_key: str, reason: str) -> int:
    rows = _get_board_store().list_sessions(workflow="agent_board", limit=_session_scan_limit())
    cleared = 0
    normalized = _normalize_user_key(user_key)
    for row in rows:
        session = _session_from_store_row(row)
        if not session:
            continue
        if _normalize_user_key(session.user_id) != normalized:
            continue

        had_metadata = bool(session.sandbox_id or session.sandbox_executor)
        session.sandbox_id = None
        session.sandbox_executor = None
        _append_session_event(
            session,
            event="sandbox_released",
            level="info",
            message="Sandbox metadata cleared for user.",
            details={"reason": reason},
        )
        status = str(row.get("status", "running") or "running")
        _persist_session(session, checkpoint="sandbox_released", status=status)
        if had_metadata:
            cleared += 1
    return cleared


def _session_payload(session: BoardSession) -> Dict[str, Any]:
    return {
        "paper_id": session.paper_id,
        "context_pack_id": session.context_pack_id,
        "workspace_dir": session.workspace_dir,
        "user_id": session.user_id,
        "sandbox_id": session.sandbox_id,
        "sandbox_executor": session.sandbox_executor,
    }


def _persist_session(
    session: BoardSession,
    *,
    checkpoint: str,
    status: str,
) -> None:
    store = _get_board_store()
    payload = _session_payload(session)
    snapshot = session.to_dict()

    try:
        existing = store.get_session(session.session_id)
        if existing is None:
            store.start_session(
                workflow="agent_board",
                payload=payload,
                session_id=session.session_id,
                resume=False,
            )

        store.update_status(
            session_id=session.session_id,
            status=status,
            checkpoint=checkpoint,
            state_patch={"board_session": snapshot},
            result={"board_session": snapshot},
        )
    except Exception:
        log.exception("Failed to persist agent board session %s", session.session_id)


def _load_session(session_id: str) -> Optional[BoardSession]:
    row = _get_board_store().get_session(session_id)
    if not row or row.get("workflow") != "agent_board":
        return None
    return _session_from_store_row(row)


def _session_from_store_row(row: Dict[str, Any]) -> Optional[BoardSession]:
    if not isinstance(row, dict):
        return None

    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    snapshot = state.get("board_session") if isinstance(state, dict) else None

    if isinstance(snapshot, dict):
        try:
            return BoardSession.from_dict(snapshot)
        except Exception:
            log.exception("Failed to parse board session snapshot")
            return None

    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    session_id = str(row.get("session_id", "")).strip()
    if not session_id:
        return None
    return BoardSession(
        session_id=session_id,
        paper_id=str(payload.get("paper_id", "")),
        context_pack_id=str(payload.get("context_pack_id", "")),
        workspace_dir=payload.get("workspace_dir"),
        user_id=str(payload.get("user_id", "default") or "default"),
        sandbox_id=payload.get("sandbox_id"),
        sandbox_executor=payload.get("sandbox_executor"),
    )


def _find_task_with_session(task_id: str) -> Optional[tuple[BoardSession, AgentTask]]:
    scan_limit = _session_scan_limit()
    rows = _get_board_store().list_sessions(workflow="agent_board", limit=scan_limit)
    for row in rows:
        session = _session_from_store_row(row)
        if not session:
            continue
        for task in session.tasks:
            if task.id == task_id:
                return session, task
    return None


def _session_scan_limit() -> int:
    raw = os.getenv("PAPERBOT_AGENT_BOARD_SCAN_LIMIT", "500").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 500
    return max(50, min(value, 5000))


def _sanitize_workspace_dir(raw_path: str) -> Path:
    candidate_text = (raw_path or "").strip()
    if not candidate_text:
        raise HTTPException(status_code=400, detail="workspace_dir is required")
    if "\x00" in candidate_text:
        raise HTTPException(status_code=400, detail="workspace_dir contains invalid characters")

    candidate = Path(candidate_text).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    candidate = candidate.resolve(strict=False)

    for blocked_root in _DANGEROUS_WORKSPACE_ROOTS:
        blocked_root_resolved = blocked_root.resolve(strict=False)
        if _is_within(candidate, blocked_root_resolved):
            raise HTTPException(
                status_code=400,
                detail=f"workspace_dir is not allowed: {candidate}",
            )

    allowed_roots = _workspace_allowed_roots()
    if not any(_is_within(candidate, root) for root in allowed_roots):
        raise HTTPException(
            status_code=400,
            detail=(
                "workspace_dir must be under an allowed root: "
                + ", ".join(str(root) for root in allowed_roots)
            ),
        )

    return candidate


def _workspace_allowed_roots() -> List[Path]:
    configured = os.getenv("PAPERBOT_WORKSPACE_ALLOWED_ROOTS", "").strip()
    roots: List[Path] = []

    if configured:
        raw_items = [item.strip() for item in configured.split(os.pathsep)]
        for item in raw_items:
            if item:
                roots.append(Path(item).expanduser().resolve(strict=False))
    else:
        roots.extend(
            [
                Path.home().resolve(strict=False),
                Path.cwd().resolve(strict=False),
                Path(tempfile.gettempdir()).resolve(strict=False),
            ]
        )

    roots.append(_DEFAULT_WORKSPACE_DIR.resolve(strict=False))

    unique: List[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False
