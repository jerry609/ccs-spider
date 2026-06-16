"""
P2C (Paper-to-Context) API Route

Endpoints:
  POST   /api/research/repro/context/generate          — generate context pack (SSE)
  GET    /api/research/repro/context                   — list packs for a user
  GET    /api/research/repro/context/{pack_id}         — get full pack detail
  POST   /api/research/repro/context/{pack_id}/session — create repro session from pack
  DELETE /api/research/repro/context/{pack_id}         — soft-delete pack
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict as _asdict
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from paperbot.api.streaming import StreamEvent, sse_response
from paperbot.api.auth.dependencies import get_required_user_id
from paperbot.application.services.p2c.models import (
    GenerateContextRequest as P2CRequest,
    RawPaperData,
    new_context_pack_id,
)
from paperbot.application.services.p2c.orchestrator import ExtractionOrchestrator
from paperbot.infrastructure.stores.repro_context_store import SqlAlchemyReproContextStore
from paperbot.utils.user_identity import has_user_identity
from paperbot.utils.logging_config import LogFiles, Logger, set_trace_id

_MAX_OBSERVATION_NARRATIVE = 400  # chars stored per memory item

router = APIRouter()

_store: Optional[SqlAlchemyReproContextStore] = None


def _get_store() -> SqlAlchemyReproContextStore:
    global _store
    if _store is None:
        _store = SqlAlchemyReproContextStore()
    return _store


# ------------------------------------------------------------------ #
# Request / Response schemas                                           #
# ------------------------------------------------------------------ #

class GenerateContextPackRequest(BaseModel):
    paper_id: str
    project_id: Optional[str] = None
    track_id: Optional[int] = None
    depth: Literal["fast", "standard", "deep"] = "standard"
    # Optional inline paper data — when provided, skips the input router lookup.
    title: Optional[str] = None
    abstract: Optional[str] = None


class CreateSessionRequest(BaseModel):
    executor_preference: Literal["auto", "claude_code", "codex", "local"] = "auto"
    override_env: Optional[dict] = None
    override_roadmap: Optional[list] = None


# ------------------------------------------------------------------ #
# POST /generate  (SSE)                                               #
# ------------------------------------------------------------------ #

async def _generate_stream(request: GenerateContextPackRequest, user_id: str):
    """SSE generator for context pack generation via Module 1 ExtractionOrchestrator."""
    pack_id = new_context_pack_id()
    Logger.info(
        f"[M2] start generate pack_id={pack_id} paper_id={request.paper_id} depth={request.depth}",
        file=LogFiles.API,
    )

    # Persist initial "running" record so the frontend can poll status.
    try:
        await asyncio.to_thread(
            _get_store().save,
            pack_id=pack_id,
            user_id=user_id,
            paper_id=request.paper_id,
            depth=request.depth,
            pack_data={},
            project_id=request.project_id,
            confidence_overall=0.0,
            warning_count=0,
        )
    except Exception as exc:
        Logger.warning(
            f"[M2] store_save_failed pack_id={pack_id} error={exc} (continuing without persistence)",
            file=LogFiles.ERROR,
        )

    yield StreamEvent(type="status", data={"pack_id": pack_id, "status": "running"})

    # asyncio.Queue bridges the on_stage_complete callback → SSE generator.
    queue: asyncio.Queue = asyncio.Queue()
    _DONE = object()
    _ERROR = object()

    total_stages = 2 if request.depth == "fast" else 6
    stages_done = 0
    last_stage_name: Optional[str] = None

    async def on_stage_complete(stage_name: str, observations: list, warnings: list) -> None:
        nonlocal stages_done
        nonlocal last_stage_name
        stages_done += 1
        last_stage_name = stage_name
        confidence = (
            sum(o.confidence for o in observations) / len(observations)
            if observations
            else 0.0
        )
        observation_summaries = [
            {
                "id": o.id,
                "type": o.type,
                "title": o.title,
                "confidence": o.confidence,
            }
            for o in observations
        ]
        try:
            await asyncio.to_thread(
                _get_store().save_stage_result,
                pack_id=pack_id,
                stage_name=stage_name,
                status="completed",
                result_data={"observations": [o.to_full() for o in observations]},
                confidence=confidence,
                duration_ms=0,
            )
        except Exception as exc:
            Logger.warning(
                f"[M2] store_save_stage_result_failed pack_id={pack_id} "
                f"stage={stage_name} error={exc}",
                file=LogFiles.ERROR,
            )
        Logger.info(
            f"[M2] stage_complete pack_id={pack_id} stage={stage_name} obs={len(observations)} warnings={len(warnings)}",
            file=LogFiles.API,
        )
        await queue.put(
            StreamEvent(
                type="stage_observations",
                data={
                    "stage": stage_name,
                    "observations": observation_summaries,
                },
            )
        )
        await queue.put(
            StreamEvent(
                type="progress",
                data={
                    "stage": stage_name,
                    "progress": stages_done / total_stages,
                    "message": f"Completed {stage_name}",
                },
            )
        )

    p2c_request = P2CRequest(
        paper_id=request.paper_id,
        user_id=user_id,
        project_id=request.project_id,
        track_id=request.track_id,
        depth=request.depth,
    )
    orchestrator = ExtractionOrchestrator()
    result_holder: list = []

    # When title+abstract are provided inline, build RawPaperData directly
    # so the orchestrator skips the input router (which cannot handle studio IDs).
    inline_raw: Optional[RawPaperData] = None
    if request.title is not None and request.abstract is not None:
        inline_raw = RawPaperData(
            paper_id=request.paper_id,
            title=request.title,
            abstract=request.abstract,
            source_adapter="inline",
        )

    async def _run() -> None:
        try:
            pack = await orchestrator.run(
                p2c_request,
                raw_paper=inline_raw,
                on_stage_complete=on_stage_complete,
            )
            result_holder.append(pack)
            await queue.put(_DONE)
        except ValueError as exc:
            result_holder.append({"kind": "client", "message": str(exc)})
            await queue.put(_ERROR)
        except Exception as exc:  # noqa: BLE001
            Logger.error(
                f"[M2] generation_failed pack_id={pack_id} error={exc}",
                file=LogFiles.ERROR,
            )
            result_holder.append({"kind": "server", "message": "Internal error during context pack generation."})
            await queue.put(_ERROR)

    asyncio.create_task(_run())

    # Drain queue, yielding progress events until the orchestrator finishes.
    while True:
        item = await queue.get()
        if item is _DONE:
            break
        elif item is _ERROR:
            err = result_holder[0]
            err_message = err.get("message", "Generation failed") if isinstance(err, dict) else str(err)
            try:
                await asyncio.to_thread(_get_store().update_status, pack_id, status="failed")
            except Exception as exc:
                Logger.warning(
                    f"[M2] store_update_status_failed pack_id={pack_id} "
                    f"status=failed error={exc}",
                    file=LogFiles.ERROR,
                )
            yield StreamEvent(type="error", data=err, message=err_message)
            return
        else:
            yield item  # StreamEvent from on_stage_complete

    if last_stage_name:
        yield StreamEvent(
            type="progress",
            data={
                "stage": last_stage_name,
                "progress": 1.0,
                "message": f"Completed {last_stage_name}",
            },
        )

    # Serialize and persist the final pack.
    pack = result_holder[0]
    pack_dict = _asdict(pack)
    pack_dict["context_pack_id"] = pack_id  # align with our DB record

    try:
        await asyncio.to_thread(
            _get_store().update_status,
            pack_id,
            status="completed",
            pack_data=pack_dict,
            confidence_overall=pack.confidence.overall,
            warning_count=len(pack.warnings),
            objective=pack.objective,
        )
    except Exception as exc:
        Logger.warning(
            f"[M2] store_update_failed pack_id={pack_id} error={exc}",
            file=LogFiles.ERROR,
        )

    # Write observations to paper-scope memory so future P2C runs can reuse them.
    await _write_paper_scope_memories(
        paper_id=request.paper_id,
        user_id=user_id,
        observations=pack.observations,
    )
    Logger.info(
        f"[M2] generation_completed pack_id={pack_id} observations={len(pack.observations)} warnings={len(pack.warnings)}",
        file=LogFiles.API,
    )

    yield StreamEvent(
        type="result",
        data={
            "context_pack_id": pack_id,
            "status": "completed",
            "summary": f"Context pack created for {request.paper_id}",
            "confidence": _asdict(pack.confidence),
            "warnings": pack.warnings,
            "next_action": "create_repro_session",
        },
    )


async def _write_paper_scope_memories(
    *,
    paper_id: str,
    user_id: str,
    observations: list,
) -> None:
    """Persist P2C observations as paper-scoped memory items for future reuse."""
    if not has_user_identity(user_id) or not observations:
        return
    try:
        from paperbot.infrastructure.stores.memory_store import SqlAlchemyMemoryStore
        from paperbot.memory.schema import MemoryCandidate

        candidates = []
        for obs in observations:
            narrative = (obs.narrative or "")[:_MAX_OBSERVATION_NARRATIVE]
            content = f"[{obs.stage}/{obs.type}] {obs.title}: {narrative}"
            candidates.append(
                MemoryCandidate(
                    kind="note",
                    content=content,
                    confidence=obs.confidence,
                    scope_type="paper",
                    scope_id=paper_id,
                    tags=[obs.stage, obs.type] + list(obs.concepts[:3]),
                )
            )
        store = SqlAlchemyMemoryStore()
        created, skipped, _ = await asyncio.to_thread(
            store.add_memories,
            user_id=user_id,
            memories=candidates,
            actor_id="p2c_orchestrator",
        )
        Logger.info(
            f"[M2] paper_memory_written paper_id={paper_id} created={created} skipped={skipped}",
            file=LogFiles.API,
        )
    except Exception as exc:  # noqa: BLE001 — DB/store errors, non-critical path
        Logger.warning(
            f"[M2] paper_memory_write_failed paper_id={paper_id} error={exc!r}",
            file=LogFiles.API,
        )


@router.post("/generate")
async def generate_context_pack(
    request: GenerateContextPackRequest,
    user_id: str = Depends(get_required_user_id),
):
    """Generate a P2C context pack for the given paper. Returns SSE stream."""
    trace_id = set_trace_id()

    Logger.info(
        f"[M2] generate_request trace_id={trace_id} paper_id={request.paper_id} user_id={user_id}",
        file=LogFiles.API,
    )
    return sse_response(_generate_stream(request, user_id), workflow="p2c_generate")


# ------------------------------------------------------------------ #
# GET /  (list)                                                        #
# ------------------------------------------------------------------ #

@router.get("")
async def list_context_packs(
    paper_id: Optional[str] = None,
    project_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    user_id: str = Depends(get_required_user_id),
):
    """List context packs for a user, with optional filters."""
    set_trace_id()
    Logger.info(
        f"[M2] list_packs user_id={user_id} paper_id={paper_id} project_id={project_id} limit={limit} offset={offset}",
        file=LogFiles.API,
    )
    items, total = await asyncio.to_thread(
        _get_store().list_by_user,
        user_id=user_id,
        paper_id=paper_id,
        project_id=project_id,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total}


# ------------------------------------------------------------------ #
# GET /{pack_id}                                                       #
# ------------------------------------------------------------------ #

@router.get("/{pack_id}")
async def get_context_pack(pack_id: str, user_id: str = Depends(get_required_user_id)):
    """Return the full context pack detail."""
    set_trace_id()
    Logger.info(f"[M2] get_pack pack_id={pack_id}", file=LogFiles.API)
    pack = await asyncio.to_thread(_get_store().get, pack_id)
    if pack is None:
        Logger.warning(f"[M2] pack_not_found pack_id={pack_id}", file=LogFiles.API)
        raise HTTPException(status_code=404, detail="Context pack not found.")
    if pack.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Access denied.")
    return pack


@router.get("/{pack_id}/observation/{observation_id}")
async def get_observation_detail(pack_id: str, observation_id: str, user_id: str = Depends(get_required_user_id)):
    """Return a single observation detail by ID."""
    set_trace_id()
    Logger.info(
        f"[M2] get_observation pack_id={pack_id} observation_id={observation_id}",
        file=LogFiles.API,
    )
    pack = await asyncio.to_thread(_get_store().get, pack_id)
    if pack is None or pack.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Context pack not found.")
    observation = await asyncio.to_thread(_get_store().get_observation, pack_id, observation_id)
    if observation is None:
        Logger.warning(
            f"[M2] observation_not_found pack_id={pack_id} observation_id={observation_id}",
            file=LogFiles.API,
        )
        raise HTTPException(status_code=404, detail="Observation not found.")
    return observation


# ------------------------------------------------------------------ #
# POST /{pack_id}/session                                             #
# ------------------------------------------------------------------ #

@router.post("/{pack_id}/session")
async def create_repro_session(pack_id: str, request: CreateSessionRequest, user_id: str = Depends(get_required_user_id)):
    """
    Convert a context pack into a runbook session.

    TODO: integrate with existing runbook creation once Module 1 is wired.
    """
    pack = await asyncio.to_thread(_get_store().get, pack_id)
    if pack is None:
        Logger.warning(f"[M2] session_pack_not_found pack_id={pack_id}", file=LogFiles.API)
        raise HTTPException(status_code=404, detail="Context pack not found.")
    if pack.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Access denied.")
    Logger.info(
        f"[M2] create_session pack_id={pack_id} executor={request.executor_preference}",
        file=LogFiles.API,
    )

    session_id = f"sess_{uuid.uuid4().hex[:16]}"
    runbook_id = f"rb_{uuid.uuid4().hex[:12]}"

    roadmap = pack.get("task_roadmap", [])
    initial_steps = [
        {
            "step_id": f"S{i + 1}",
            "title": cp.get("title", f"Step {i + 1}"),
            "command": "",
            "status": "pending",
        }
        for i, cp in enumerate(roadmap)
    ] or [{"step_id": "S1", "title": "Setup environment", "command": "", "status": "pending"}]

    return {
        "session_id": session_id,
        "runbook_id": runbook_id,
        "initial_steps": initial_steps,
        "initial_prompt": (
            f"Based on the reproduction context pack for paper {pack.get('paper_id', '')}, "
            "implement the code step by step following the roadmap."
        ),
    }


# ------------------------------------------------------------------ #
# DELETE /{pack_id}                                                   #
# ------------------------------------------------------------------ #

@router.delete("/{pack_id}")
async def delete_context_pack(pack_id: str, user_id: str = Depends(get_required_user_id)):
    """Soft-delete a context pack."""
    set_trace_id()
    Logger.info(f"[M2] delete_pack pack_id={pack_id}", file=LogFiles.API)
    pack = await asyncio.to_thread(_get_store().get, pack_id)
    if pack is None:
        Logger.warning(f"[M2] delete_pack_not_found pack_id={pack_id}", file=LogFiles.API)
        raise HTTPException(status_code=404, detail="Context pack not found.")
    if pack.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Access denied.")
    await asyncio.to_thread(_get_store().soft_delete, pack_id)
    return {"status": "deleted", "context_pack_id": pack_id}
