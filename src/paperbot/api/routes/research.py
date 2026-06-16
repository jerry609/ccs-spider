from __future__ import annotations

import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from paperbot.application.services.workflow_query_grounder import (
    WorkflowQueryGrounder,
    build_grounded_routing_query,
)
from paperbot.application.services.research_track_context_service import (
    ResearchTrackContextService,
    TrackContextSnapshot,
)
from paperbot.application.services.track_memory_service import (
    TrackMemoryScopeError,
    TrackMemoryService,
    TrackMemoryValidationError,
)
from paperbot.context_engine import ContextEngine, ContextEngineConfig
from paperbot.context_engine.track_router import TrackRouter
from paperbot.domain.paper_identity import normalize_arxiv_id, normalize_doi
from paperbot.infrastructure.api_clients.semantic_scholar import SemanticScholarClient
from paperbot.infrastructure.exporters.obsidian_sync import (
    export_track_snapshot,
    obsidian_auto_export_enabled,
)
from paperbot.infrastructure.stores.memory_store import SqlAlchemyMemoryStore
from paperbot.infrastructure.stores.research_store import SqlAlchemyResearchStore
from paperbot.infrastructure.stores.workflow_metric_store import WorkflowMetricStore
from paperbot.api.auth.dependencies import get_required_user_id
from paperbot.memory.eval.collector import MemoryMetricCollector
from paperbot.memory.extractor import extract_memories
from paperbot.memory.schema import MemoryCandidate, NormalizedMessage
from paperbot.infrastructure.stores.wiki_concept_store import WikiConceptStore
from paperbot.utils.logging_config import LogFiles, Logger, set_trace_id
from paperbot.application.services.wiki_concept_service import WikiConceptService

router = APIRouter()

_research_store: Optional[SqlAlchemyResearchStore] = None
_memory_store: Optional[SqlAlchemyMemoryStore] = None
_track_router: Optional[TrackRouter] = None
_metric_collector: Optional[MemoryMetricCollector] = None
_workflow_metric_store: Optional[WorkflowMetricStore] = None
_paper_store: Optional["PaperStore"] = None
_paper_search_service: Optional["PaperSearchService"] = None
_document_index_store: Optional["DocumentIndexStore"] = None
_anchor_service: Optional["AnchorService"] = None
_subscription_service: Optional["SubscriptionService"] = None
_workflow_query_grounder: Optional[WorkflowQueryGrounder] = None


def _get_research_store() -> SqlAlchemyResearchStore:
    global _research_store
    if _research_store is None:
        _research_store = SqlAlchemyResearchStore()
    return _research_store


def _get_memory_store() -> SqlAlchemyMemoryStore:
    global _memory_store
    if _memory_store is None:
        _memory_store = SqlAlchemyMemoryStore()
    return _memory_store


def _get_track_router() -> TrackRouter:
    global _track_router
    if _track_router is None:
        _track_router = TrackRouter(
            research_store=_get_research_store(),
            memory_store=_get_memory_store(),
        )
    return _track_router


def _get_workflow_query_grounder() -> WorkflowQueryGrounder:
    global _workflow_query_grounder
    if _workflow_query_grounder is None:
        _workflow_query_grounder = WorkflowQueryGrounder(WikiConceptService(WikiConceptStore()))
    return _workflow_query_grounder


def _build_track_context_service() -> ResearchTrackContextService:
    return ResearchTrackContextService(
        track_reader=_get_research_store(),
        memory_store=_get_memory_store(),
    )


def _build_track_memory_service() -> TrackMemoryService:
    return TrackMemoryService(
        track_reader=_get_research_store(),
        memory_store=_get_memory_store(),
    )


def _schedule_obsidian_export(
    background_tasks: BackgroundTasks,
    *,
    user_id: str,
    track_id: int,
    for_tracks: bool = False,
) -> None:
    if track_id <= 0:
        return
    if not obsidian_auto_export_enabled(for_tracks=for_tracks):
        return
    background_tasks.add_task(
        export_track_snapshot,
        user_id=user_id,
        track_id=track_id,
    )


def _schedule_obsidian_export_for_track(
    background_tasks: BackgroundTasks,
    *,
    track: Optional[Dict[str, Any]],
    for_tracks: bool = False,
) -> None:
    if track is None:
        return

    user_id = str(track.get("user_id") or "").strip()
    track_id = int(track.get("id") or 0)
    if not user_id or track_id <= 0:
        return

    _schedule_obsidian_export(
        background_tasks,
        user_id=user_id,
        track_id=track_id,
        for_tracks=for_tracks,
    )


def _trusted_track_user_id(track: Optional[Dict[str, Any]]) -> Optional[str]:
    if track is None:
        return None
    user_id = str(track.get("user_id") or "").strip()
    return user_id or None


ENABLE_ANCHOR_AUTHORS = os.getenv("PAPERBOT_ENABLE_ANCHOR_AUTHORS", "true").lower() == "true"

_DISCOVERY_STOPWORDS: Set[str] = {
    "about",
    "across",
    "after",
    "also",
    "analysis",
    "approach",
    "based",
    "between",
    "beyond",
    "dataset",
    "from",
    "into",
    "method",
    "methods",
    "model",
    "models",
    "paper",
    "study",
    "their",
    "these",
    "this",
    "using",
    "with",
}

_DEADLINE_RADAR_DATA: List[Dict[str, Any]] = [
    {
        "name": "KDD 2026",
        "ccf_level": "A",
        "field": "Data Mining",
        "deadline": "2026-03-05T23:59:59+00:00",
        "url": "https://kdd.org/kdd2026/",
        "keywords": ["data mining", "recommendation", "graph mining"],
    },
    {
        "name": "ACL 2026",
        "ccf_level": "A",
        "field": "NLP",
        "deadline": "2026-03-15T23:59:59+00:00",
        "url": "https://2026.aclweb.org/",
        "keywords": ["nlp", "llm", "language model", "retrieval"],
    },
    {
        "name": "CVPR 2026",
        "ccf_level": "A",
        "field": "Computer Vision",
        "deadline": "2026-03-20T23:59:59+00:00",
        "url": "https://cvpr.thecvf.com/",
        "keywords": ["computer vision", "diffusion", "multimodal"],
    },
    {
        "name": "USENIX Security 2026",
        "ccf_level": "A",
        "field": "Security",
        "deadline": "2026-03-28T23:59:59+00:00",
        "url": "https://www.usenix.org/conference/usenixsecurity26",
        "keywords": ["security", "privacy", "llm safety"],
    },
    {
        "name": "EMNLP 2026",
        "ccf_level": "B",
        "field": "NLP",
        "deadline": "2026-05-10T23:59:59+00:00",
        "url": "https://2026.emnlp.org/",
        "keywords": ["nlp", "alignment", "reasoning"],
    },
    {
        "name": "NeurIPS 2026",
        "ccf_level": "A",
        "field": "Machine Learning",
        "deadline": "2026-05-15T23:59:59+00:00",
        "url": "https://neurips.cc/",
        "keywords": ["machine learning", "llm", "optimization"],
    },
    {
        "name": "AAAI 2027",
        "ccf_level": "A",
        "field": "Artificial Intelligence",
        "deadline": "2026-08-10T23:59:59+00:00",
        "url": "https://aaai.org/conference/aaai/",
        "keywords": ["ai", "agent", "reasoning"],
    },
]


def _get_metric_collector() -> MemoryMetricCollector:
    """Lazy initialization of metric collector."""
    global _metric_collector
    if _metric_collector is None:
        _metric_collector = MemoryMetricCollector()
    return _metric_collector


def _get_workflow_metric_store() -> WorkflowMetricStore:
    global _workflow_metric_store
    if _workflow_metric_store is None:
        _workflow_metric_store = WorkflowMetricStore()
    return _workflow_metric_store


def _get_paper_store() -> "PaperStore":
    """Lazy initialization of paper store."""
    from paperbot.infrastructure.stores.paper_store import PaperStore

    global _paper_store
    if _paper_store is None:
        _paper_store = PaperStore()
    return _paper_store


def _get_paper_search_service() -> "PaperSearchService":
    """Lazy initialization of unified paper search service."""
    from paperbot.application.services.paper_search_service import PaperSearchService
    from paperbot.infrastructure.adapters import build_adapter_registry

    global _paper_search_service
    if _paper_search_service is None:
        _paper_search_service = PaperSearchService(
            adapters=build_adapter_registry(),
            registry=_get_paper_store(),
        )
    return _paper_search_service


def _get_document_index_store() -> "DocumentIndexStore":
    """Lazy initialization of document evidence retrieval store."""
    from paperbot.infrastructure.stores.document_index_store import DocumentIndexStore

    global _document_index_store
    if _document_index_store is None:
        _document_index_store = DocumentIndexStore()
    return _document_index_store


def _get_anchor_service() -> "AnchorService":
    """Lazy initialization of anchor author discovery service."""
    from paperbot.application.services.anchor_service import AnchorService

    global _anchor_service
    if _anchor_service is None:
        _anchor_service = AnchorService()
    return _anchor_service


def _get_subscription_service() -> "SubscriptionService":
    from paperbot.infrastructure.services.subscription_service import SubscriptionService

    global _subscription_service
    if _subscription_service is None:
        _subscription_service = SubscriptionService(
            config_path=os.getenv("PAPERBOT_SUBSCRIPTIONS_CONFIG_PATH") or None
        )
    return _subscription_service


def _ensure_anchor_feature_enabled() -> None:
    if ENABLE_ANCHOR_AUTHORS:
        return
    raise HTTPException(
        status_code=503,
        detail="Anchor author feature is disabled by PAPERBOT_ENABLE_ANCHOR_AUTHORS",
    )


def _schedule_embedding_precompute(
    background_tasks: Optional[BackgroundTasks],
    *,
    user_id: str,
    track_ids: List[int],
) -> None:
    if background_tasks is None:
        return

    ids = sorted({int(x) for x in track_ids if int(x) > 0})
    if not ids:
        return

    def _run() -> None:
        try:
            _get_track_router().precompute_track_embeddings(user_id=user_id, track_ids=ids)
        except Exception:
            return

    background_tasks.add_task(_run)


def _normalize_deadline_match_terms(values: List[Any]) -> Set[str]:
    terms: Set[str] = set()
    for value in values:
        normalized = str(value or "").strip().lower()
        if not normalized:
            continue
        terms.add(normalized)
        for token in re.split(r"[^a-z0-9]+", normalized):
            token = token.strip()
            if len(token) >= 2:
                terms.add(token)
    return terms


def _collect_track_deadline_terms(track: Dict[str, Any]) -> Set[str]:
    values: List[Any] = []
    for key in ("keywords", "methods", "venues"):
        values.extend(track.get(key) or [])
    return _normalize_deadline_match_terms(values)


def _collect_conference_deadline_terms(item: Dict[str, Any]) -> Set[str]:
    values: List[Any] = list(item.get("keywords") or [])
    values.append(item.get("field") or "")
    name = re.sub(r"\b20\d{2}\b", "", str(item.get("name") or ""), flags=re.IGNORECASE).strip()
    if name:
        values.append(name)
    return _normalize_deadline_match_terms(values)


class TrackCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = ""
    keywords: List[str] = []
    venues: List[str] = []
    methods: List[str] = []
    activate: bool = True


class TrackUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    description: Optional[str] = None
    keywords: Optional[List[str]] = None
    venues: Optional[List[str]] = None
    methods: Optional[List[str]] = None


class TrackResponse(BaseModel):
    track: Dict[str, Any]


class TrackContextMemorySummaryResponse(BaseModel):
    total_items: int
    approved_items: int
    pending_items: int
    top_tags: List[str]
    latest_memory_at: Optional[str] = None


class TrackContextFeedbackSummaryResponse(BaseModel):
    total_items: int
    actions: Dict[str, int]
    latest_feedback_at: Optional[str] = None
    recent_items: List[Dict[str, Any]]


class TrackContextSavedPapersResponse(BaseModel):
    total_items: int
    latest_saved_at: Optional[str] = None
    recent_items: List[Dict[str, Any]]


class TrackContextResponse(BaseModel):
    user_id: str
    track_id: int
    track: Dict[str, Any]
    tasks: List[Dict[str, Any]]
    milestones: List[Dict[str, Any]]
    memory: TrackContextMemorySummaryResponse
    feedback: TrackContextFeedbackSummaryResponse
    saved_papers: TrackContextSavedPapersResponse
    eval_summary: Dict[str, Any]


def _serialize_track_context_response(
    *,
    user_id: str,
    snapshot: TrackContextSnapshot,
) -> TrackContextResponse:
    track_id = int(snapshot.track.get("id") or 0)
    return TrackContextResponse(
        user_id=user_id,
        track_id=track_id,
        **snapshot.to_dict(),
    )


@router.post("/research/tracks", response_model=TrackResponse)
def create_track(
    req: TrackCreateRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_required_user_id),
):
    track = _get_research_store().create_track(
        user_id=user_id,
        name=req.name,
        description=req.description,
        keywords=req.keywords,
        venues=req.venues,
        methods=req.methods,
        activate=req.activate,
    )
    track_user_id = _trusted_track_user_id(track)
    if track_user_id:
        _schedule_embedding_precompute(
            background_tasks, user_id=track_user_id, track_ids=[int(track.get("id") or 0)]
        )
    _schedule_obsidian_export_for_track(background_tasks, track=track, for_tracks=True)
    return TrackResponse(track=track)


class TrackListResponse(BaseModel):
    user_id: str
    tracks: List[Dict[str, Any]]


@router.get("/research/tracks", response_model=TrackListResponse)
def list_tracks(
    user_id: str = Depends(get_required_user_id),
    include_archived: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
):
    tracks = _get_research_store().list_tracks(
        user_id=user_id, include_archived=include_archived, limit=limit
    )
    return TrackListResponse(user_id=user_id, tracks=tracks)


class DeadlineRadarResponse(BaseModel):
    user_id: str
    generated_at: str
    items: List[Dict[str, Any]]


@router.get("/research/deadlines/radar", response_model=DeadlineRadarResponse)
def get_deadline_radar(
    user_id: str = Depends(get_required_user_id),
    days: int = Query(180, ge=7, le=365),
    ccf_levels: str = Query("A,B,C"),
    field: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
):
    levels = {
        token.strip().upper()
        for token in str(ccf_levels or "").split(",")
        if token.strip().upper() in {"A", "B", "C"}
    }
    if not levels:
        levels = {"A", "B", "C"}

    field_filter = str(field or "").strip().lower()
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=int(days))

    tracks = _get_research_store().list_tracks(user_id=user_id, include_archived=False, limit=200)
    track_tokens: Dict[int, set[str]] = {}
    for track in tracks:
        track_id = int(track.get("id") or 0)
        if track_id <= 0:
            continue
        tokens = _collect_track_deadline_terms(track)
        track_tokens[track_id] = tokens

    rows: List[Dict[str, Any]] = []
    for item in _DEADLINE_RADAR_DATA:
        try:
            deadline = datetime.fromisoformat(str(item.get("deadline") or ""))
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        if deadline < now or deadline > cutoff:
            continue
        if str(item.get("ccf_level") or "").strip().upper() not in levels:
            continue
        if field_filter and field_filter not in str(item.get("field") or "").strip().lower():
            continue

        conf_keywords = {
            str(k).strip().lower() for k in (item.get("keywords") or []) if str(k).strip()
        }
        conf_terms = _collect_conference_deadline_terms(item)

        matched_tracks: List[Dict[str, Any]] = []
        for track in tracks:
            track_id = int(track.get("id") or 0)
            if track_id <= 0:
                continue
            overlap = sorted(conf_terms & track_tokens.get(track_id, set()))
            if overlap:
                matched_tracks.append(
                    {
                        "track_id": track_id,
                        "track_name": str(track.get("name") or ""),
                        "matched_keywords": overlap,
                        "matched_terms": overlap,
                    }
                )

        workflow_query = ", ".join(item.get("keywords") or [])
        days_left = max(0, int((deadline - now).total_seconds() // 86400))
        rows.append(
            {
                "name": str(item.get("name") or ""),
                "ccf_level": str(item.get("ccf_level") or ""),
                "field": str(item.get("field") or ""),
                "deadline": deadline.isoformat(),
                "days_left": days_left,
                "url": str(item.get("url") or ""),
                "keywords": sorted(conf_keywords),
                "workflow_query": workflow_query,
                "matched_tracks": matched_tracks,
            }
        )

    rows.sort(key=lambda row: (int(row.get("days_left") or 0), str(row.get("name") or "")))
    return DeadlineRadarResponse(
        user_id=user_id,
        generated_at=now.isoformat(),
        items=rows[: max(1, int(limit))],
    )


@router.get("/research/tracks/active", response_model=TrackResponse)
def get_active_track(user_id: str = Depends(get_required_user_id)):
    track = _get_research_store().get_active_track(user_id=user_id)
    if not track:
        raise HTTPException(status_code=404, detail="No active track for user")
    return TrackResponse(track=track)


@router.get("/research/tracks/{track_id}/context", response_model=TrackContextResponse)
def get_track_context(track_id: int, user_id: str = Depends(get_required_user_id)):
    snapshot = _build_track_context_service().get_track_context(
        user_id=user_id,
        track_id=track_id,
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Track not found")
    return _serialize_track_context_response(user_id=user_id, snapshot=snapshot)


@router.patch("/research/tracks/{track_id}", response_model=TrackResponse)
def update_track(
    track_id: int,
    req: TrackUpdateRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_required_user_id),
):
    update_data = req.model_dump(exclude_unset=True, exclude_none=True)

    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    try:
        track = _get_research_store().update_track(
            user_id=user_id, track_id=track_id, **update_data
        )
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Track name already exists") from None
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    track_user_id = _trusted_track_user_id(track)
    if track_user_id:
        _schedule_embedding_precompute(
            background_tasks, user_id=track_user_id, track_ids=[track_id]
        )
    _schedule_obsidian_export_for_track(background_tasks, track=track, for_tracks=True)
    return TrackResponse(track=track)


@router.post("/research/tracks/{track_id}/activate", response_model=TrackResponse)
def activate_track(
    track_id: int,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_required_user_id),
):
    track = _get_research_store().activate_track(user_id=user_id, track_id=track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    _schedule_embedding_precompute(background_tasks, user_id=user_id, track_ids=[track_id])
    return TrackResponse(track=track)


class TaskCreateRequest(BaseModel):
    title: str = Field(..., min_length=1)
    status: str = "todo"
    priority: int = 0
    paper_id: Optional[str] = None
    paper_url: Optional[str] = None
    metadata: Dict[str, Any] = {}


class TaskResponse(BaseModel):
    task: Dict[str, Any]


@router.post("/research/tracks/{track_id}/tasks", response_model=TaskResponse)
def add_task(
    track_id: int,
    req: TaskCreateRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_required_user_id),
):
    task = _get_research_store().add_task(
        user_id=user_id,
        track_id=track_id,
        title=req.title,
        status=req.status,
        priority=req.priority,
        paper_id=req.paper_id,
        paper_url=req.paper_url,
        metadata=req.metadata,
    )
    if not task:
        raise HTTPException(status_code=404, detail="Track not found")
    _schedule_embedding_precompute(background_tasks, user_id=user_id, track_ids=[track_id])
    return TaskResponse(task=task)


class TaskListResponse(BaseModel):
    user_id: str
    track_id: int
    tasks: List[Dict[str, Any]]


@router.get("/research/tracks/{track_id}/tasks", response_model=TaskListResponse)
def list_tasks(
    track_id: int,
    user_id: str = Depends(get_required_user_id),
    status: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
):
    tasks = _get_research_store().list_tasks(
        user_id=user_id, track_id=track_id, status=status, limit=limit
    )
    return TaskListResponse(user_id=user_id, track_id=track_id, tasks=tasks)


class MemoryItemCreateRequest(BaseModel):
    scope_type: str = "track"  # global/track
    scope_id: Optional[str] = None
    kind: str = Field("note", min_length=1, max_length=32)
    content: str = Field(..., min_length=1)
    tags: List[str] = []
    status: str = "approved"
    confidence: float = 0.8
    evidence: Dict[str, Any] = {}


class MemoryItemResponse(BaseModel):
    item: Dict[str, Any]


def _resolve_track_scope_id(
    user_id: str, scope_type: str, scope_id: Optional[str]
) -> Optional[str]:
    return _build_track_memory_service().resolve_scope_id(
        user_id=user_id,
        scope_type=scope_type,
        scope_id=scope_id,
    )


@router.post("/research/memory/items", response_model=MemoryItemResponse)
def create_memory_item(
    req: MemoryItemCreateRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_required_user_id),
):
    scope_type = (req.scope_type or "global").strip() or "global"
    scope_id = _resolve_track_scope_id(user_id, scope_type, req.scope_id)
    if scope_type == "track" and not scope_id:
        raise HTTPException(
            status_code=400,
            detail="track scope requires an existing track or an active track",
        )

    cand = MemoryCandidate(
        kind=req.kind,  # type: ignore[arg-type]
        content=req.content,
        confidence=float(req.confidence),
        tags=req.tags,
        evidence=req.evidence,
        scope_type=scope_type,
        scope_id=scope_id,
        status=req.status,
    )
    created, _, rows = _get_memory_store().add_memories(user_id=user_id, memories=[cand])
    if created <= 0 or not rows:
        raise HTTPException(
            status_code=409, detail="Duplicate memory item (same scope/kind/content)"
        )
    if scope_type == "track":
        _schedule_embedding_precompute(
            background_tasks, user_id=user_id, track_ids=[int(scope_id or 0)]
        )
    return MemoryItemResponse(item=SqlAlchemyMemoryStore._row_to_dict(rows[0]))


class MemoryItemListResponse(BaseModel):
    user_id: str
    items: List[Dict[str, Any]]


@router.get("/research/memory/items", response_model=MemoryItemListResponse)
def list_memory_items(
    user_id: str = Depends(get_required_user_id),
    scope_type: Optional[str] = None,
    scope_id: Optional[str] = None,
    kind: Optional[str] = None,
    status: Optional[str] = None,
    include_pending: bool = False,
    limit: int = Query(100, ge=1, le=500),
):
    items = _get_memory_store().list_memories(
        user_id=user_id,
        limit=limit,
        kind=kind,
        scope_type=scope_type,
        scope_id=scope_id,
        include_pending=include_pending,
        status=status,
    )
    return MemoryItemListResponse(user_id=user_id, items=items)


@router.get("/research/memory/inbox", response_model=MemoryItemListResponse)
def list_memory_inbox(
    user_id: str = Depends(get_required_user_id),
    track_id: Optional[int] = None,
    limit: int = Query(100, ge=1, le=500),
):
    try:
        items = _build_track_memory_service().list_inbox(
            user_id=user_id,
            track_id=track_id,
            limit=limit,
        )
    except TrackMemoryScopeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MemoryItemListResponse(user_id=user_id, items=items)


class MemorySuggestRequest(BaseModel):
    text: str = Field(..., min_length=1)
    scope_type: str = "track"
    scope_id: Optional[str] = None
    use_llm: bool = False
    redact: bool = True
    language_hint: Optional[str] = None


class MemorySuggestResponse(BaseModel):
    user_id: str
    created: int
    skipped: int
    candidates: List[Dict[str, Any]]


@router.post("/research/memory/suggest", response_model=MemorySuggestResponse)
def suggest_memories(
    req: MemorySuggestRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_required_user_id),
):
    scope_type = (req.scope_type or "global").strip() or "global"
    scope_id = _resolve_track_scope_id(user_id, scope_type, req.scope_id)
    if scope_type == "track" and not scope_id:
        raise HTTPException(
            status_code=400,
            detail="track scope requires an existing track or an active track",
        )

    msgs = [NormalizedMessage(role="user", content=req.text)]
    extracted = extract_memories(
        msgs, use_llm=req.use_llm, redact=req.redact, language_hint=req.language_hint
    )
    pending = [
        MemoryCandidate(
            kind=m.kind,
            content=m.content,
            confidence=m.confidence,
            tags=m.tags,
            evidence=m.evidence,
            scope_type=scope_type,
            scope_id=scope_id,
            status="pending",
        )
        for m in extracted
    ]
    created, skipped, rows = _get_memory_store().add_memories(user_id=user_id, memories=pending)
    if scope_type == "track":
        _schedule_embedding_precompute(
            background_tasks, user_id=user_id, track_ids=[int(scope_id or 0)]
        )
    return MemorySuggestResponse(
        user_id=user_id,
        created=created,
        skipped=skipped,
        candidates=[SqlAlchemyMemoryStore._row_to_dict(r) for r in rows],
    )


class MemoryModerateRequest(BaseModel):
    status: str = Field(..., min_length=1)  # approved/rejected/pending/superseded
    content: Optional[str] = None
    kind: Optional[str] = None
    tags: Optional[List[str]] = None
    scope_type: Optional[str] = None
    scope_id: Optional[str] = None


@router.post("/research/memory/items/{item_id}/moderate", response_model=MemoryItemResponse)
def moderate_memory_item(
    item_id: int,
    req: MemoryModerateRequest,
    user_id: str = Depends(get_required_user_id),
):
    updated = _get_memory_store().update_item(
        user_id=user_id,
        item_id=item_id,
        status=req.status,
        content=req.content,
        kind=req.kind,
        tags=req.tags,
        scope_type=req.scope_type,
        scope_id=req.scope_id,
        actor_id="user",
    )
    if not updated:
        raise HTTPException(status_code=409, detail="Memory item not found or update conflict")
    return MemoryItemResponse(item=updated)


class BulkModerateRequest(BaseModel):
    item_ids: List[int] = Field(default_factory=list)
    status: str = Field(..., min_length=1)


class BulkModerateResponse(BaseModel):
    user_id: str
    updated: List[Dict[str, Any]]


@router.post("/research/memory/bulk_moderate", response_model=BulkModerateResponse)
def bulk_moderate(
    req: BulkModerateRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_required_user_id),
):
    result = _build_track_memory_service().bulk_moderate(
        user_id=user_id,
        item_ids=req.item_ids,
        status=req.status,
    )
    items_before = result.items_before
    updated = result.updated_items
    _schedule_embedding_precompute(
        background_tasks,
        user_id=user_id,
        track_ids=result.affected_track_ids,
    )

    # P0 Hook: Record false positive rate when user rejects high-confidence items
    # A rejection of an auto-approved (confidence >= 0.60) item is a false positive
    if req.status == "rejected" and items_before:
        high_confidence_rejected = sum(
            1
            for item in items_before
            if item.get("confidence", 0) >= 0.60 and item.get("status") == "approved"
        )
        if high_confidence_rejected > 0:
            collector = _get_metric_collector()
            collector.record_false_positive_rate(
                false_positive_count=high_confidence_rejected,
                total_approved_count=len(items_before),
                evaluator_id=f"user:{user_id}",
                detail={
                    "item_ids": req.item_ids,
                    "action": "bulk_moderate_reject",
                },
            )

    return BulkModerateResponse(user_id=user_id, updated=updated)


class BulkMoveRequest(BaseModel):
    item_ids: List[int] = Field(default_factory=list)
    scope_type: str = Field(..., min_length=1)
    scope_id: Optional[str] = None


class BulkMoveResponse(BaseModel):
    user_id: str
    updated: List[Dict[str, Any]]


@router.post("/research/memory/bulk_move", response_model=BulkMoveResponse)
def bulk_move(
    req: BulkMoveRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_required_user_id),
):
    try:
        result = _build_track_memory_service().bulk_move(
            user_id=user_id,
            item_ids=req.item_ids,
            scope_type=req.scope_type,
            scope_id=req.scope_id,
        )
    except TrackMemoryValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    _schedule_embedding_precompute(
        background_tasks,
        user_id=user_id,
        track_ids=result.affected_track_ids,
    )
    return BulkMoveResponse(user_id=user_id, updated=result.updated_items)


class MemoryFeedbackRequest(BaseModel):
    """Request to record feedback on retrieved memories."""

    memory_ids: List[int] = Field(..., min_length=1, description="IDs of memories being rated")
    helpful_ids: List[int] = Field(
        default_factory=list, description="IDs of memories that were helpful"
    )
    not_helpful_ids: List[int] = Field(
        default_factory=list, description="IDs of memories that were not helpful"
    )
    context_run_id: Optional[int] = None
    query: Optional[str] = None


class MemoryFeedbackResponse(BaseModel):
    user_id: str
    total_rated: int
    helpful_count: int
    not_helpful_count: int
    hit_rate: float


@router.post("/research/memory/feedback", response_model=MemoryFeedbackResponse)
def record_memory_feedback(
    req: MemoryFeedbackRequest,
    user_id: str = Depends(get_required_user_id),
):
    """
    Record user feedback on retrieved memories.

    This endpoint allows users to indicate which memories were helpful vs not helpful
    when they were retrieved for a query. This data feeds into the P0 retrieval_hit_rate metric.

    Usage:
    - After building context, frontend shows retrieved memories
    - User marks which memories were helpful
    - Frontend calls this endpoint with the feedback
    """
    helpful_set = set(req.helpful_ids)
    not_helpful_set = set(req.not_helpful_ids)

    # Count hits (helpful) and misses (not helpful)
    hits = len(helpful_set)
    total = len(req.memory_ids)

    if total > 0:
        hit_rate = hits / total
        collector = _get_metric_collector()
        collector.record_retrieval_hit_rate(
            hits=hits,
            expected=total,
            evaluator_id=f"user:{user_id}",
            detail={
                "memory_ids": req.memory_ids,
                "helpful_ids": list(helpful_set),
                "not_helpful_ids": list(not_helpful_set),
                "context_run_id": req.context_run_id,
                "query": req.query,
                "action": "memory_feedback",
            },
        )
    else:
        hit_rate = 0.0

    return MemoryFeedbackResponse(
        user_id=user_id,
        total_rated=total,
        helpful_count=hits,
        not_helpful_count=len(not_helpful_set),
        hit_rate=hit_rate,
    )


class ClearTrackMemoryResponse(BaseModel):
    user_id: str
    track_id: int
    deleted_count: int


@router.post("/research/tracks/{track_id}/memory/clear", response_model=ClearTrackMemoryResponse)
def clear_track_memory(
    track_id: int,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_required_user_id),
    confirm: bool = Query(False),
):
    if not confirm:
        raise HTTPException(status_code=400, detail="confirm=true required")
    try:
        result = _build_track_memory_service().clear_track_memory(
            user_id=user_id,
            track_id=track_id,
        )
    except TrackMemoryScopeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    deleted = result.deleted_count
    _schedule_embedding_precompute(background_tasks, user_id=user_id, track_ids=[track_id])

    # P0 Hook: Verify deletion compliance - deleted items should not be retrievable
    if deleted > 0:
        collector = _get_metric_collector()
        collector.record_deletion_compliance(
            deleted_retrieved_count=result.retrieved_after_delete_count,
            deleted_total_count=deleted,
            evaluator_id=f"user:{user_id}",
            detail={
                "track_id": track_id,
                "deleted_count": deleted,
                "retrieved_after_delete": result.retrieved_after_delete_count,
                "action": "clear_track_memory",
            },
        )

    return ClearTrackMemoryResponse(user_id=user_id, track_id=track_id, deleted_count=deleted)


class PrecomputeEmbeddingsRequest(BaseModel):
    track_ids: Optional[List[int]] = None


class PrecomputeEmbeddingsResponse(BaseModel):
    user_id: str
    result: Dict[str, int]


@router.post("/research/embeddings/precompute", response_model=PrecomputeEmbeddingsResponse)
def precompute_embeddings(
    req: PrecomputeEmbeddingsRequest,
    user_id: str = Depends(get_required_user_id),
):
    result = _get_track_router().precompute_track_embeddings(
        user_id=user_id, track_ids=req.track_ids or None
    )
    return PrecomputeEmbeddingsResponse(user_id=user_id, result=result)


class EvalSummaryResponse(BaseModel):
    user_id: str
    track_id: Optional[int] = None
    summary: Dict[str, Any]


@router.get("/research/evals/summary", response_model=EvalSummaryResponse)
def eval_summary(
    user_id: str = Depends(get_required_user_id),
    track_id: Optional[int] = None,
    days: int = Query(30, ge=1, le=365),
):
    summary = _get_research_store().summarize_eval(user_id=user_id, track_id=track_id, days=days)
    return EvalSummaryResponse(user_id=user_id, track_id=track_id, summary=summary)


class PaperFeedbackRequest(BaseModel):
    track_id: Optional[int] = None
    paper_id: str = Field(..., min_length=1)
    action: str = Field(..., min_length=1)  # like/unlike/dislike/undislike/skip/save/unsave/cite
    weight: float = 0.0
    metadata: Dict[str, Any] = {}
    context_run_id: Optional[int] = None
    context_rank: Optional[int] = None
    # Paper metadata (optional, used when saving to library)
    paper_title: Optional[str] = None
    paper_abstract: Optional[str] = None
    paper_authors: Optional[List[str]] = None
    paper_year: Optional[int] = None
    paper_venue: Optional[str] = None
    paper_citation_count: Optional[int] = None
    paper_url: Optional[str] = None
    paper_source: Optional[str] = None  # arxiv, semantic_scholar, openalex


class PaperFeedbackResponse(BaseModel):
    feedback: Dict[str, Any]
    library_paper_id: Optional[int] = None  # ID in papers table if saved
    current_action: Optional[str] = None


@router.post("/research/papers/feedback", response_model=PaperFeedbackResponse)
def add_paper_feedback(
    req: PaperFeedbackRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_required_user_id),
):
    set_trace_id()  # Initialize trace_id for this request
    Logger.info(f"Received paper feedback request, action={req.action}", file=LogFiles.HARVEST)
    research_store = _get_research_store()

    track_id = req.track_id
    active_track: Optional[Dict[str, Any]] = None
    if track_id is None:
        Logger.info("No track specified, checking active track", file=LogFiles.HARVEST)
        active_track = research_store.get_active_track(user_id=user_id)
        if active_track:
            track_id = int(active_track["id"])
        else:
            Logger.info(
                "No active track found; recording global paper feedback",
                file=LogFiles.HARVEST,
            )

    meta: Dict[str, Any] = dict(req.metadata or {})
    if req.context_run_id is not None:
        meta["context_run_id"] = int(req.context_run_id)
    if req.context_rank is not None:
        meta["context_rank"] = int(req.context_rank)

    library_paper_id: Optional[int] = None

    # If action is "save" and we have paper metadata, insert into papers table
    if req.action == "save" and req.paper_title:
        Logger.info(
            "Save action detected, inserting paper into papers table", file=LogFiles.HARVEST
        )
        try:
            from paperbot.domain.harvest import HarvestedPaper, HarvestSource

            paper_store = _get_paper_store()

            # Determine source from request or default to semantic_scholar
            source_str = (req.paper_source or "semantic_scholar").lower()
            source_map = {
                "arxiv": HarvestSource.ARXIV,
                "semantic_scholar": HarvestSource.SEMANTIC_SCHOLAR,
                "openalex": HarvestSource.OPENALEX,
            }
            source = source_map.get(source_str, HarvestSource.SEMANTIC_SCHOLAR)

            paper = HarvestedPaper(
                title=req.paper_title,
                source=source,
                abstract=req.paper_abstract or "",
                authors=req.paper_authors or [],
                semantic_scholar_id=(
                    req.paper_id if source == HarvestSource.SEMANTIC_SCHOLAR else None
                ),
                arxiv_id=req.paper_id if source == HarvestSource.ARXIV else None,
                openalex_id=req.paper_id if source == HarvestSource.OPENALEX else None,
                year=req.paper_year,
                venue=req.paper_venue,
                citation_count=req.paper_citation_count or 0,
                url=req.paper_url,
            )
            Logger.info("Calling paper store to upsert paper", file=LogFiles.HARVEST)
            new_count, _ = paper_store.upsert_papers_batch([paper])

            # Get the paper ID from database using store method
            result = paper_store.get_paper_by_source_id(source, req.paper_id)
            if result:
                library_paper_id = result.id
                # Store library_paper_id in metadata for joins, keep paper_id as external ID
                meta["library_paper_id"] = library_paper_id
                Logger.info(
                    f"Paper saved to library with id={library_paper_id}", file=LogFiles.HARVEST
                )
        except Exception as e:
            Logger.warning(f"Failed to save paper to library: {e}", file=LogFiles.HARVEST)

    Logger.info("Recording paper feedback to research store", file=LogFiles.HARVEST)
    fb = _get_research_store().add_paper_feedback(
        user_id=user_id,
        track_id=track_id,
        paper_id=req.paper_id,  # Always use external ID for consistency
        action=req.action,
        weight=req.weight,
        metadata=meta,
    )
    if not fb:
        Logger.error("Failed to record feedback - track not found", file=LogFiles.HARVEST)
        raise HTTPException(status_code=404, detail="Track not found")
    Logger.info("Paper feedback recorded successfully", file=LogFiles.HARVEST)
    normalized_action = research_store._normalize_feedback_action(req.action)
    current_action = research_store._effective_feedback_action(normalized_action)
    if normalized_action in {"save", "unsave"} and track_id is not None:
        export_track = active_track or research_store.get_track_by_id(track_id=int(track_id))
        _schedule_obsidian_export_for_track(
            background_tasks,
            track=export_track,
            for_tracks=False,
        )
    return PaperFeedbackResponse(
        feedback=fb,
        library_paper_id=library_paper_id,
        current_action=current_action,
    )


class PaperFeedbackListResponse(BaseModel):
    user_id: str
    track_id: int
    items: List[Dict[str, Any]]


@router.get("/research/tracks/{track_id}/papers/feedback", response_model=PaperFeedbackListResponse)
def list_paper_feedback(
    track_id: int,
    user_id: str = Depends(get_required_user_id),
    action: Optional[str] = None,
    limit: int = Query(200, ge=1, le=1000),
):
    items = _get_research_store().list_paper_feedback(
        user_id=user_id, track_id=track_id, action=action, limit=limit
    )
    return PaperFeedbackListResponse(user_id=user_id, track_id=track_id, items=items)


class PaperReadingStatusRequest(BaseModel):
    status: str = Field(..., min_length=1)  # unread/reading/read/archived
    mark_saved: Optional[bool] = None
    metadata: Dict[str, Any] = {}


class PaperReadingStatusResponse(BaseModel):
    status: Dict[str, Any]


class SavedPapersResponse(BaseModel):
    user_id: str
    items: List[Dict[str, Any]]


class DiscoverySeedRequest(BaseModel):
    track_id: Optional[int] = None
    seed_type: str = Field(..., pattern="^(doi|arxiv|openalex|semantic_scholar|author)$")
    seed_id: str = Field(..., min_length=1)
    limit: int = Field(default=30, ge=1, le=200)
    include_related: bool = True
    include_cited: bool = True
    include_citing: bool = True
    include_coauthor: bool = True
    personalized: bool = True
    year_from: Optional[int] = Field(default=None, ge=1900, le=2100)
    year_to: Optional[int] = Field(default=None, ge=1900, le=2100)


class DiscoverySeedResponse(BaseModel):
    seed: Dict[str, Any]
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    items: List[Dict[str, Any]]
    stats: Dict[str, Any]


class PaperCollectionCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = ""
    track_id: Optional[int] = Field(default=None, ge=1)


class PaperCollectionUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = None
    archived: Optional[bool] = None


class PaperCollectionItemUpsertRequest(BaseModel):
    paper_id: str = Field(..., min_length=1)
    note: Optional[str] = ""
    tags: Optional[List[str]] = []


class PaperCollectionItemPatchRequest(BaseModel):
    note: Optional[str] = ""
    tags: Optional[List[str]] = []


class PaperCollectionListResponse(BaseModel):
    user_id: str
    items: List[Dict[str, Any]]


class PaperCollectionResponse(BaseModel):
    collection: Dict[str, Any]


class PaperCollectionItemsResponse(BaseModel):
    user_id: str
    collection_id: int
    items: List[Dict[str, Any]]


class TrackFeedResponse(BaseModel):
    user_id: str
    track_id: int
    total: int
    limit: int
    offset: int
    items: List[Dict[str, Any]]


class AnchorDiscoverResponse(BaseModel):
    user_id: str
    track_id: int
    limit: int
    window_days: int
    personalized: bool
    items: List[Dict[str, Any]]


class AnchorActionRequest(BaseModel):
    action: str = Field(..., pattern="^(follow|ignore)$")


class AnchorActionResponse(BaseModel):
    action: Dict[str, Any]


class AnchorActionListResponse(BaseModel):
    user_id: str
    track_id: int
    items: List[Dict[str, Any]]


class PaperDetailResponse(BaseModel):
    detail: Dict[str, Any]


class PaperRepoListResponse(BaseModel):
    paper_id: str
    repos: List[Dict[str, Any]]


@router.post("/research/papers/{paper_id}/status", response_model=PaperReadingStatusResponse)
def update_paper_status(
    paper_id: str,
    req: PaperReadingStatusRequest,
    user_id: str = Depends(get_required_user_id),
):
    status = _get_research_store().set_paper_reading_status(
        user_id=user_id,
        paper_id=paper_id,
        status=req.status,
        metadata=req.metadata,
        mark_saved=req.mark_saved,
    )
    if not status:
        raise HTTPException(status_code=404, detail="Paper not found in registry")
    return PaperReadingStatusResponse(status=status)


@router.get("/research/papers/saved", response_model=SavedPapersResponse)
def list_saved_papers(
    user_id: str = Depends(get_required_user_id),
    track_id: Optional[int] = None,
    collection_id: Optional[int] = None,
    sort_by: str = Query("saved_at"),
    limit: int = Query(200, ge=1, le=1000),
):
    items = _get_research_store().list_saved_papers(
        user_id=user_id,
        track_id=track_id,
        collection_id=collection_id,
        sort_by=sort_by,
        limit=limit,
    )
    return SavedPapersResponse(user_id=user_id, items=items)


@router.post("/research/discovery/seed", response_model=DiscoverySeedResponse)
async def discover_from_seed(
    req: DiscoverySeedRequest, user_id: str = Depends(get_required_user_id)
):
    if req.year_from and req.year_to and req.year_from > req.year_to:
        raise HTTPException(status_code=400, detail="year_from must be <= year_to")

    from paperbot.infrastructure.connectors.openalex_connector import OpenAlexConnector

    client = SemanticScholarClient(
        api_key=os.getenv("SEMANTIC_SCHOLAR_API_KEY") or os.getenv("S2_API_KEY")
    )
    openalex = OpenAlexConnector()
    seed_node_id = f"seed:{req.seed_type}:{req.seed_id}"
    candidate_map: Dict[str, Dict[str, Any]] = {}
    edge_map: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    seed_info: Dict[str, Any] = {"seed_type": req.seed_type, "seed_id": req.seed_id}

    try:
        if req.seed_type == "author":
            author = await client.get_author(
                req.seed_id,
                fields=["name", "paperCount", "citationCount", "hIndex", "affiliations"],
            )
            papers = await client.get_author_papers(
                req.seed_id,
                limit=max(20, req.limit * 2),
                fields=[
                    "title",
                    "abstract",
                    "year",
                    "citationCount",
                    "authors",
                    "venue",
                    "url",
                    "paperId",
                    "externalIds",
                ],
            )
            seed_info["name"] = (author or {}).get("name") or req.seed_id
            seed_info["paper_count"] = int((author or {}).get("paperCount") or 0)
            seed_info["citation_count"] = int((author or {}).get("citationCount") or 0)

            if req.include_coauthor:
                for row in papers:
                    paper = _normalize_discovery_candidate(row, source="semantic_scholar")
                    _add_discovery_candidate(
                        candidate_map,
                        edge_map,
                        seed_node_id=seed_node_id,
                        edge_type="coauthor",
                        paper=paper,
                    )
        else:
            seed_s2 = _build_s2_seed_id(req.seed_type, req.seed_id)
            seed_paper = await client.get_paper(
                seed_s2,
                fields=[
                    "paperId",
                    "title",
                    "abstract",
                    "year",
                    "citationCount",
                    "authors",
                    "venue",
                    "url",
                    "externalIds",
                    "references",
                    "citations",
                ],
            )
            if seed_paper:
                seed_info.update(
                    {
                        "title": seed_paper.get("title") or req.seed_id,
                        "year": seed_paper.get("year"),
                        "citation_count": int(seed_paper.get("citationCount") or 0),
                    }
                )
                if req.include_cited:
                    for row in seed_paper.get("references") or []:
                        wrapped = row.get("citedPaper") if isinstance(row, dict) else row
                        paper = _normalize_discovery_candidate(
                            wrapped,
                            source="semantic_scholar",
                        )
                        _add_discovery_candidate(
                            candidate_map,
                            edge_map,
                            seed_node_id=seed_node_id,
                            edge_type="cited",
                            paper=paper,
                        )
                if req.include_citing:
                    for row in seed_paper.get("citations") or []:
                        wrapped = row.get("citingPaper") if isinstance(row, dict) else row
                        paper = _normalize_discovery_candidate(
                            wrapped,
                            source="semantic_scholar",
                        )
                        _add_discovery_candidate(
                            candidate_map,
                            edge_map,
                            seed_node_id=seed_node_id,
                            edge_type="citing",
                            paper=paper,
                        )

            try:
                openalex_work = await openalex.resolve_work(
                    seed_type=req.seed_type,
                    seed_id=req.seed_id,
                )
            except Exception:
                openalex_work = None
            if openalex_work:
                if "title" not in seed_info and openalex_work.get("title"):
                    seed_info["title"] = openalex_work.get("title")
                if "year" not in seed_info and openalex_work.get("publication_year"):
                    seed_info["year"] = openalex_work.get("publication_year")
                if req.include_related:
                    try:
                        related_rows = await openalex.get_related_works(
                            openalex_work,
                            limit=req.limit,
                        )
                    except Exception:
                        related_rows = []
                    for row in related_rows:
                        paper = _normalize_discovery_candidate(row, source="openalex")
                        _add_discovery_candidate(
                            candidate_map,
                            edge_map,
                            seed_node_id=seed_node_id,
                            edge_type="related",
                            paper=paper,
                        )
                if req.include_cited:
                    try:
                        cited_rows = await openalex.get_referenced_works(
                            openalex_work,
                            limit=req.limit,
                        )
                    except Exception:
                        cited_rows = []
                    for row in cited_rows:
                        paper = _normalize_discovery_candidate(row, source="openalex")
                        _add_discovery_candidate(
                            candidate_map,
                            edge_map,
                            seed_node_id=seed_node_id,
                            edge_type="cited",
                            paper=paper,
                        )
                if req.include_citing:
                    try:
                        citing_rows = await openalex.get_citing_works(
                            openalex_work,
                            limit=req.limit,
                        )
                    except Exception:
                        citing_rows = []
                    for row in citing_rows:
                        paper = _normalize_discovery_candidate(row, source="openalex")
                        _add_discovery_candidate(
                            candidate_map,
                            edge_map,
                            seed_node_id=seed_node_id,
                            edge_type="citing",
                            paper=paper,
                        )
    finally:
        await client.close()
        await openalex.close()

    candidates = list(candidate_map.values())
    filtered = _filter_discovery_candidates(
        candidates,
        year_from=req.year_from,
        year_to=req.year_to,
    )
    feedback_profile = (
        _build_feedback_profile(user_id=user_id, track_id=req.track_id) if req.personalized else {}
    )
    scored = _rank_discovery_candidates(
        filtered,
        feedback_profile=feedback_profile,
        limit=req.limit,
    )

    scored_keys = {str(item.get("_candidate_key") or "") for item in scored}
    nodes: List[Dict[str, Any]] = [
        {
            "id": seed_node_id,
            "type": "seed",
            "label": seed_info.get("title") or seed_info.get("name") or req.seed_id,
            "year": seed_info.get("year"),
            "seed_type": req.seed_type,
        }
    ]
    for row in scored:
        nodes.append(
            {
                "id": str(row.get("_candidate_key") or ""),
                "type": "paper",
                "label": str(row.get("paper", {}).get("title") or "Untitled"),
                "year": row.get("paper", {}).get("year"),
                "edge_types": row.get("edge_types") or [],
                "score": row.get("score"),
            }
        )

    edges: List[Dict[str, Any]] = []
    for _, payload in edge_map.items():
        candidate_key = str(payload.get("target_key") or "")
        if candidate_key not in scored_keys:
            continue
        edges.append(payload)

    items: List[Dict[str, Any]] = []
    for row in scored:
        items.append(
            {
                "candidate_key": str(row.get("_candidate_key") or ""),
                "paper": row.get("paper"),
                "edge_types": row.get("edge_types") or [],
                "score": row.get("score"),
                "why_this_paper": row.get("why_this_paper") or [],
            }
        )

    relation_counts: Dict[str, int] = defaultdict(int)
    for row in items:
        for relation in row.get("edge_types") or []:
            relation_counts[str(relation)] += 1

    return DiscoverySeedResponse(
        seed=seed_info,
        nodes=nodes,
        edges=edges,
        items=items,
        stats={
            "candidate_count": len(candidates),
            "filtered_count": len(filtered),
            "returned_count": len(items),
            "relation_counts": dict(relation_counts),
            "personalized": bool(req.personalized),
        },
    )


@router.post("/research/collections", response_model=PaperCollectionResponse)
def create_collection(
    req: PaperCollectionCreateRequest, user_id: str = Depends(get_required_user_id)
):
    try:
        collection = _get_research_store().create_collection(
            user_id=user_id,
            name=req.name,
            description=req.description,
            track_id=req.track_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PaperCollectionResponse(collection=collection)


@router.get("/research/collections", response_model=PaperCollectionListResponse)
def list_collections(
    user_id: str = Depends(get_required_user_id),
    include_archived: bool = Query(False),
    track_id: Optional[int] = Query(default=None),
    limit: int = Query(200, ge=1, le=1000),
):
    items = _get_research_store().list_collections(
        user_id=user_id,
        include_archived=include_archived,
        track_id=track_id,
        limit=limit,
    )
    return PaperCollectionListResponse(user_id=user_id, items=items)


@router.patch("/research/collections/{collection_id}", response_model=PaperCollectionResponse)
def update_collection(
    collection_id: int,
    req: PaperCollectionUpdateRequest,
    user_id: str = Depends(get_required_user_id),
):
    try:
        collection = _get_research_store().update_collection(
            user_id=user_id,
            collection_id=collection_id,
            name=req.name,
            description=req.description,
            archived=req.archived,
        )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Collection name already exists") from exc
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    return PaperCollectionResponse(collection=collection)


@router.get(
    "/research/collections/{collection_id}/items",
    response_model=PaperCollectionItemsResponse,
)
def list_collection_items(
    collection_id: int,
    user_id: str = Depends(get_required_user_id),
    limit: int = Query(500, ge=1, le=5000),
):
    items = _get_research_store().list_collection_items(
        user_id=user_id,
        collection_id=collection_id,
        limit=limit,
    )
    return PaperCollectionItemsResponse(user_id=user_id, collection_id=collection_id, items=items)


@router.post(
    "/research/collections/{collection_id}/items",
    response_model=PaperCollectionItemsResponse,
)
def upsert_collection_item(
    collection_id: int,
    req: PaperCollectionItemUpsertRequest,
    user_id: str = Depends(get_required_user_id),
):
    item = _get_research_store().upsert_collection_item(
        user_id=user_id,
        collection_id=collection_id,
        paper_id=req.paper_id,
        note=req.note,
        tags=req.tags,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Collection or paper not found")
    items = _get_research_store().list_collection_items(
        user_id=user_id, collection_id=collection_id
    )
    return PaperCollectionItemsResponse(user_id=user_id, collection_id=collection_id, items=items)


@router.patch(
    "/research/collections/{collection_id}/items/{paper_id}",
    response_model=PaperCollectionItemsResponse,
)
def patch_collection_item(
    collection_id: int,
    paper_id: str,
    req: PaperCollectionItemPatchRequest,
    user_id: str = Depends(get_required_user_id),
):
    item = _get_research_store().upsert_collection_item(
        user_id=user_id,
        collection_id=collection_id,
        paper_id=paper_id,
        note=req.note,
        tags=req.tags,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Collection or paper not found")
    items = _get_research_store().list_collection_items(
        user_id=user_id, collection_id=collection_id
    )
    return PaperCollectionItemsResponse(user_id=user_id, collection_id=collection_id, items=items)


@router.delete("/research/collections/{collection_id}/items/{paper_id}")
def delete_collection_item(
    collection_id: int, paper_id: str, user_id: str = Depends(get_required_user_id)
):
    ok = _get_research_store().remove_collection_item(
        user_id=user_id,
        collection_id=collection_id,
        paper_id=paper_id,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Collection item not found")
    return {"ok": True}


@router.get("/research/tracks/{track_id}/feed", response_model=TrackFeedResponse)
def get_track_feed(
    track_id: int,
    user_id: str = Depends(get_required_user_id),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    track = _get_research_store().get_track(user_id=user_id, track_id=track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    payload = _get_research_store().list_track_feed(
        user_id=user_id,
        track_id=track_id,
        limit=limit,
        offset=offset,
    )
    return TrackFeedResponse(
        user_id=user_id,
        track_id=track_id,
        total=int(payload.get("total") or 0),
        limit=limit,
        offset=offset,
        items=payload.get("items") or [],
    )


@router.get("/research/papers/export")
def export_papers(
    user_id: str = Depends(get_required_user_id),
    track_id: Optional[int] = None,
    format: str = Query("bibtex", pattern="^(bibtex|ris|markdown|csl_json)$"),
):
    items = _get_research_store().list_saved_papers(user_id=user_id, track_id=track_id, limit=1000)
    papers = [item["paper"] for item in items if item.get("paper")]

    if not papers:
        raise HTTPException(status_code=404, detail="No saved papers found")

    if format == "bibtex":
        raw_keys = [_make_citation_key(p.get("authors") or [], p.get("year")) for p in papers]
        keys = _dedup_citation_keys(raw_keys)
        body = "\n\n".join(_paper_to_bibtex(p, k) for p, k in zip(papers, keys))
        return Response(
            content=body,
            media_type="application/x-bibtex",
            headers={"Content-Disposition": "attachment; filename=papers.bib"},
        )

    if format == "ris":
        body = "\n\n".join(_paper_to_ris(p) for p in papers)
        return Response(
            content=body,
            media_type="application/x-research-info-systems",
            headers={"Content-Disposition": "attachment; filename=papers.ris"},
        )

    if format == "csl_json":
        import json as _json

        csl_items = [_paper_to_csl_json(p) for p in papers]
        body = _json.dumps(csl_items, ensure_ascii=False, indent=2)
        return Response(
            content=body,
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=papers.csl.json"},
        )

    # markdown
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    header = f"---\nexported: {now_iso}\ncount: {len(papers)}\n---\n\n# Saved Papers\n"
    body = header + "\n\n".join(_paper_to_markdown(p) for p in papers)
    return Response(
        content=body,
        media_type="text/markdown",
        headers={"Content-Disposition": "attachment; filename=papers.md"},
    )


class BibtexImportRequest(BaseModel):
    content: str = Field(..., min_length=1)
    track_id: Optional[int] = Field(default=None, ge=1)
    track_name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    source_hint: str = "bibtex_import"


class BibtexImportResponse(BaseModel):
    user_id: str
    track_id: int
    track_name: str
    parsed: int
    imported: int
    created: int
    updated: int
    skipped: int
    errors: List[str] = []


@router.post("/research/papers/import/bibtex", response_model=BibtexImportResponse)
def import_bibtex(req: BibtexImportRequest, user_id: str = Depends(get_required_user_id)):
    entries = _parse_bibtex_entries(req.content)
    if not entries:
        raise HTTPException(status_code=400, detail="No valid BibTeX entries found")

    track = _resolve_or_create_import_track(
        user_id=user_id,
        track_id=req.track_id,
        track_name=req.track_name,
        default_track_name="BibTeX Imports",
    )
    track_pk = int(track["id"])

    paper_store = _get_paper_store()
    existing_saved_ids = _get_research_store().list_paper_feedback_ids(
        user_id=user_id,
        track_id=track_pk,
        action="save",
        limit=5000,
    )

    imported = 0
    created = 0
    updated = 0
    skipped = 0
    errors: List[str] = []

    for index, entry in enumerate(entries, start=1):
        normalized = _bibtex_entry_to_paper(entry)
        title = str(normalized.get("title") or "").strip()
        if not title:
            skipped += 1
            errors.append(f"entry {index}: missing title")
            continue

        try:
            upserted = paper_store.upsert_paper(paper=normalized, source_hint=req.source_hint)
            paper_ref = str(upserted.get("id") or "").strip()
            if not paper_ref:
                skipped += 1
                errors.append(f"entry {index}: failed to resolve saved paper id")
                continue

            imported += 1
            if bool(upserted.get("_created")):
                created += 1
            else:
                updated += 1

            if paper_ref not in existing_saved_ids:
                metadata: Dict[str, Any] = {
                    "import_source": "bibtex",
                    "citation_key": str(entry.get("key") or ""),
                    "entry_type": str(entry.get("entry_type") or ""),
                }
                _get_research_store().add_paper_feedback(
                    user_id=user_id,
                    track_id=track_pk,
                    paper_id=paper_ref,
                    action="save",
                    weight=1.0,
                    metadata=metadata,
                )
                existing_saved_ids.add(paper_ref)
        except Exception as exc:
            skipped += 1
            errors.append(f"entry {index}: {exc}")

    return BibtexImportResponse(
        user_id=user_id,
        track_id=track_pk,
        track_name=str(track.get("name") or ""),
        parsed=len(entries),
        imported=imported,
        created=created,
        updated=updated,
        skipped=skipped,
        errors=errors[:100],
    )


class ZoteroSyncRequest(BaseModel):
    track_id: Optional[int] = Field(default=None, ge=1)
    track_name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    library_type: str = Field(default="user", pattern="^(user|group)$")
    library_id: str = Field(..., min_length=1)
    api_key: str = Field(..., min_length=1)
    max_items: int = Field(default=100, ge=1, le=1000)


class ZoteroPullResponse(BaseModel):
    user_id: str
    track_id: int
    track_name: str
    total_remote: int
    imported: int
    created: int
    updated: int
    skipped: int
    errors: List[str] = []


@router.post("/research/integrations/zotero/pull", response_model=ZoteroPullResponse)
def pull_from_zotero(req: ZoteroSyncRequest, user_id: str = Depends(get_required_user_id)):
    from paperbot.infrastructure.connectors.zotero_connector import ZoteroConnector

    track = _resolve_or_create_import_track(
        user_id=user_id,
        track_id=req.track_id,
        track_name=req.track_name,
        default_track_name="Zotero Imports",
    )
    track_pk = int(track["id"])

    connector = ZoteroConnector()
    try:
        remote_items = connector.list_all_items(
            api_key=req.api_key,
            library_type=req.library_type,
            library_id=req.library_id,
            max_items=req.max_items,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to pull from Zotero: {exc}") from exc

    paper_store = _get_paper_store()
    existing_saved_ids = _get_research_store().list_paper_feedback_ids(
        user_id=user_id,
        track_id=track_pk,
        action="save",
        limit=5000,
    )

    imported = 0
    created = 0
    updated = 0
    skipped = 0
    errors: List[str] = []

    for index, item in enumerate(remote_items, start=1):
        paper = connector.zotero_item_to_paper(item)
        if not str(paper.get("title") or "").strip():
            skipped += 1
            errors.append(f"item {index}: missing title")
            continue

        try:
            upserted = paper_store.upsert_paper(paper=paper, source_hint="zotero")
            paper_ref = str(upserted.get("id") or "").strip()
            if not paper_ref:
                skipped += 1
                errors.append(f"item {index}: failed to resolve saved paper id")
                continue

            imported += 1
            if bool(upserted.get("_created")):
                created += 1
            else:
                updated += 1

            if paper_ref not in existing_saved_ids:
                metadata: Dict[str, Any] = {
                    "import_source": "zotero",
                    "zotero_key": str((item or {}).get("key") or ""),
                    "zotero_library_type": req.library_type,
                    "zotero_library_id": req.library_id,
                }
                _get_research_store().add_paper_feedback(
                    user_id=user_id,
                    track_id=track_pk,
                    paper_id=paper_ref,
                    action="save",
                    weight=1.0,
                    metadata=metadata,
                )
                existing_saved_ids.add(paper_ref)
        except Exception as exc:
            skipped += 1
            errors.append(f"item {index}: {exc}")

    return ZoteroPullResponse(
        user_id=user_id,
        track_id=track_pk,
        track_name=str(track.get("name") or ""),
        total_remote=len(remote_items),
        imported=imported,
        created=created,
        updated=updated,
        skipped=skipped,
        errors=errors[:100],
    )


class ZoteroPushRequest(ZoteroSyncRequest):
    dry_run: bool = False


class ZoteroPushResponse(BaseModel):
    user_id: str
    track_id: Optional[int] = None
    local_saved: int
    remote_items: int
    to_push: int
    pushed: int
    skipped: int
    dry_run: bool
    errors: List[str] = []


@router.post("/research/integrations/zotero/push", response_model=ZoteroPushResponse)
def push_to_zotero(req: ZoteroPushRequest, user_id: str = Depends(get_required_user_id)):
    from paperbot.infrastructure.connectors.zotero_connector import ZoteroConnector

    if req.track_id is not None:
        track = _get_research_store().get_track(user_id=user_id, track_id=req.track_id)
        if not track:
            raise HTTPException(status_code=404, detail="Track not found")

    connector = ZoteroConnector()
    local_items = _get_research_store().list_saved_papers(
        user_id=user_id,
        track_id=req.track_id,
        sort_by="saved_at",
        limit=req.max_items,
    )
    local_papers = [item.get("paper") for item in local_items if item.get("paper")]

    try:
        remote_items = connector.list_all_items(
            api_key=req.api_key,
            library_type=req.library_type,
            library_id=req.library_id,
            max_items=2000,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to list Zotero items: {exc}") from exc

    existing_keys = {
        key for key in (connector.item_dedupe_key(item) for item in remote_items) if key
    }
    payload: List[Dict[str, Any]] = []
    skipped = 0

    for paper in local_papers:
        if not isinstance(paper, dict):
            continue
        key = connector.paper_dedupe_key(paper)
        if key and key in existing_keys:
            skipped += 1
            continue
        payload.append(connector.paper_to_zotero_item(paper))
        if key:
            existing_keys.add(key)

    pushed = 0
    errors: List[str] = []
    if not req.dry_run:
        for start in range(0, len(payload), 50):
            batch = payload[start : start + 50]
            if not batch:
                continue
            try:
                result = connector.create_items(
                    api_key=req.api_key,
                    library_type=req.library_type,
                    library_id=req.library_id,
                    items=batch,
                )
                successful = result.get("successful")
                if isinstance(successful, dict):
                    pushed += len(successful)
                else:
                    pushed += len(batch)
            except Exception as exc:
                errors.append(f"batch {start // 50 + 1}: {exc}")

    return ZoteroPushResponse(
        user_id=user_id,
        track_id=req.track_id,
        local_saved=len(local_papers),
        remote_items=len(remote_items),
        to_push=len(payload),
        pushed=pushed,
        skipped=skipped,
        dry_run=req.dry_run,
        errors=errors[:100],
    )


@router.get("/research/tracks/{track_id}/anchors/discover", response_model=AnchorDiscoverResponse)
def discover_track_anchors(
    track_id: int,
    user_id: str = Depends(get_required_user_id),
    limit: int = Query(20, ge=1, le=100),
    window_days: int = Query(365, ge=30, le=3650),
    personalized: bool = Query(True),
):
    _ensure_anchor_feature_enabled()

    track = _get_research_store().get_track(user_id=user_id, track_id=track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    window_years = max(int(window_days) // 365, 1)
    try:
        items = _get_anchor_service().discover(
            track_id=track_id,
            user_id=user_id,
            limit=limit,
            window_years=window_years,
            personalized=personalized,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return AnchorDiscoverResponse(
        user_id=user_id,
        track_id=track_id,
        limit=limit,
        window_days=window_days,
        personalized=personalized,
        items=items,
    )


@router.post(
    "/research/tracks/{track_id}/anchors/{author_id}/action",
    response_model=AnchorActionResponse,
)
def set_anchor_action(
    track_id: int,
    author_id: int,
    req: AnchorActionRequest,
    user_id: str = Depends(get_required_user_id),
):
    _ensure_anchor_feature_enabled()

    track = _get_research_store().get_track(user_id=user_id, track_id=track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    started = time.perf_counter()
    try:
        payload = _get_anchor_service().set_user_anchor_action(
            user_id=user_id,
            track_id=track_id,
            author_id=author_id,
            action=req.action,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(status_code=404, detail="Anchor author not found") from exc

    elapsed_ms = float((time.perf_counter() - started) * 1000.0)
    _get_workflow_metric_store().record_metric(
        workflow="anchor_action",
        stage=req.action,
        status="completed",
        track_id=track_id,
        claim_count=0,
        evidence_count=0,
        elapsed_ms=elapsed_ms,
        detail={"author_id": author_id, "user_id": user_id},
    )

    return AnchorActionResponse(action=payload)


@router.get("/research/tracks/{track_id}/anchors/actions", response_model=AnchorActionListResponse)
def list_anchor_actions(track_id: int, user_id: str = Depends(get_required_user_id)):
    _ensure_anchor_feature_enabled()

    track = _get_research_store().get_track(user_id=user_id, track_id=track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    items = _get_anchor_service().get_user_anchor_actions(user_id=user_id, track_id=track_id)
    return AnchorActionListResponse(user_id=user_id, track_id=track_id, items=items)


@router.get("/research/papers/{paper_id}", response_model=PaperDetailResponse)
def get_paper_detail(paper_id: str, user_id: str = Depends(get_required_user_id)):
    detail = _get_research_store().get_paper_detail(paper_id=paper_id, user_id=user_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Paper not found in registry")
    return PaperDetailResponse(detail=detail)


@router.get("/research/papers/{paper_id}/repos", response_model=PaperRepoListResponse)
def get_paper_repos(paper_id: str):
    repos = _get_research_store().list_paper_repos(paper_id=paper_id)
    if repos is None:
        raise HTTPException(status_code=404, detail="Paper not found in registry")
    return PaperRepoListResponse(paper_id=paper_id, repos=repos)


class RouterSuggestRequest(BaseModel):
    query: str = Field(..., min_length=1)


class RouterSuggestResponse(BaseModel):
    suggestion: Optional[Dict[str, Any]]


@router.post("/research/router/suggest", response_model=RouterSuggestResponse)
def suggest_track(req: RouterSuggestRequest, user_id: str = Depends(get_required_user_id)):
    active = _get_research_store().get_active_track(user_id=user_id)
    if not active:
        return RouterSuggestResponse(suggestion=None)
    grounded_query = _get_workflow_query_grounder().ground_query(
        user_id=user_id, query=req.query
    )
    routing_query = build_grounded_routing_query(
        original_query=req.query,
        grounded_query=grounded_query,
    )
    suggestion = _get_track_router().suggest_track(
        user_id=user_id,
        query=routing_query or req.query,
        active_track_id=int(active["id"]),
    )
    if suggestion is not None and grounded_query.concepts:
        suggestion = {
            **suggestion,
            "query_grounding": grounded_query.to_dict(),
        }
    return RouterSuggestResponse(suggestion=suggestion)


class ContextRequest(BaseModel):
    query: str = Field(..., min_length=1)
    track_id: Optional[int] = None
    activate_track_id: Optional[int] = None  # confirm switch: activates then uses it
    memory_limit: int = Field(8, ge=1, le=50)
    paper_limit: int = Field(8, ge=0, le=50)
    sources: Optional[List[str]] = None
    offline: bool = False
    include_cross_track: bool = False
    stage: str = "auto"  # auto/survey/writing/rebuttal
    exploration_ratio: Optional[float] = Field(default=None, ge=0.0, le=0.5)
    diversity_strength: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    personalized: bool = True
    year_from: Optional[int] = Field(default=None, ge=1900, le=2100)
    year_to: Optional[int] = Field(default=None, ge=1900, le=2100)


class ContextResponse(BaseModel):
    context_pack: Dict[str, Any]


class WorkflowMetricsResponse(BaseModel):
    summary: Dict[str, Any]


class EvidenceCoverageResponse(BaseModel):
    coverage_rate: float
    total_claims: int
    total_with_evidence: int
    trend: List[Dict[str, Any]]


@router.get("/research/metrics/workflows", response_model=WorkflowMetricsResponse)
def get_workflow_metrics_summary(
    days: int = Query(7, ge=1, le=90),
    workflow: Optional[str] = None,
    track_id: Optional[int] = None,
):
    summary = _get_workflow_metric_store().summarize(
        days=days,
        workflow=workflow,
        track_id=track_id,
    )
    return WorkflowMetricsResponse(summary=summary)


@router.get("/research/metrics/evidence-coverage", response_model=EvidenceCoverageResponse)
def get_evidence_coverage(
    days: int = Query(7, ge=1, le=90),
    workflow: Optional[str] = None,
    track_id: Optional[int] = None,
):
    summary = _get_workflow_metric_store().summarize(
        days=days,
        workflow=workflow,
        track_id=track_id,
    )
    totals = summary.get("totals", {})
    total_claims = int(totals.get("claim_count") or 0)
    total_with_evidence = int(totals.get("evidence_count") or 0)
    coverage_rate = float(totals.get("coverage_rate") or 0.0)

    trend = []
    for day_bucket in summary.get("by_day", []):
        trend.append(
            {
                "date": day_bucket.get("date", ""),
                "rate": float(day_bucket.get("coverage_rate") or 0.0),
            }
        )

    return EvidenceCoverageResponse(
        coverage_rate=coverage_rate,
        total_claims=total_claims,
        total_with_evidence=total_with_evidence,
        trend=trend,
    )


@router.post("/research/context", response_model=ContextResponse)
async def build_context(req: ContextRequest, user_id: str = Depends(get_required_user_id)):
    set_trace_id()  # Initialize trace_id for this request
    Logger.info("Received build context request", file=LogFiles.HARVEST)

    started = time.perf_counter()
    metric_store = _get_workflow_metric_store()

    if req.year_from is not None and req.year_to is not None and req.year_from > req.year_to:
        raise HTTPException(status_code=400, detail="year_from cannot be greater than year_to")

    if req.activate_track_id is not None:
        Logger.info("Activating research track", file=LogFiles.HARVEST)
        activated = _get_research_store().activate_track(
            user_id=user_id, track_id=req.activate_track_id
        )
        if not activated:
            Logger.error("Research track not found", file=LogFiles.HARVEST)
            raise HTTPException(status_code=404, detail="Track not found")

    Logger.info("Initializing context engine", file=LogFiles.HARVEST)
    search_service = None
    if not req.offline and req.paper_limit > 0:
        try:
            search_service = _get_paper_search_service()
        except Exception as exc:
            Logger.warning(
                f"Failed to initialize PaperSearchService, fallback to legacy S2 path: {exc}",
                file=LogFiles.HARVEST,
            )

    engine = ContextEngine(
        research_store=_get_research_store(),
        memory_store=_get_memory_store(),
        paper_store=_get_paper_store(),
        search_service=search_service,
        evidence_retriever=_get_document_index_store(),
        track_router=_get_track_router(),
        query_grounder=_get_workflow_query_grounder(),
        config=ContextEngineConfig(
            memory_limit=req.memory_limit,
            paper_limit=req.paper_limit,
            search_sources=req.sources,
            offline=req.offline,
            stage=req.stage,
            exploration_ratio=(
                float(req.exploration_ratio) if req.exploration_ratio is not None else None
            ),
            diversity_strength=(
                float(req.diversity_strength) if req.diversity_strength is not None else None
            ),
            personalized=bool(req.personalized),
            year_from=req.year_from,
            year_to=req.year_to,
        ),
    )
    try:
        Logger.info("Building context pack with paper recommendations", file=LogFiles.HARVEST)
        pack = await engine.build_context_pack(
            user_id=user_id,
            query=req.query,
            track_id=req.track_id,
            include_cross_track=req.include_cross_track,
        )
        paper_count = len(pack.get("paper_recommendations", []))
        Logger.info(
            f"Context pack built successfully, found {paper_count} papers", file=LogFiles.HARVEST
        )
        evidence_count = sum(1 for p in (pack.get("paper_recommendations") or []) if p.get("url"))
        metric_store.record_metric(
            workflow="research_context",
            stage=req.stage,
            status="completed",
            track_id=req.track_id,
            claim_count=paper_count,
            evidence_count=evidence_count,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            detail={
                "paper_limit": int(req.paper_limit),
                "memory_limit": int(req.memory_limit),
                "offline": bool(req.offline),
                "year_from": req.year_from,
                "year_to": req.year_to,
                "sources": list(req.sources or []),
            },
        )
        return ContextResponse(context_pack=pack)
    except Exception as exc:
        metric_store.record_metric(
            workflow="research_context",
            stage=req.stage,
            status="failed",
            track_id=req.track_id,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            detail={
                "error": str(exc),
                "paper_limit": int(req.paper_limit),
                "memory_limit": int(req.memory_limit),
                "offline": bool(req.offline),
                "year_from": req.year_from,
                "year_to": req.year_to,
            },
        )
        raise
    finally:
        await engine.close()


class ScholarListResponse(BaseModel):
    items: List[Dict[str, Any]]
    total: int


class ScholarCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    semantic_scholar_id: str = Field(..., min_length=1, max_length=128)
    affiliations: List[str] = []
    keywords: List[str] = []
    research_fields: List[str] = []


class ScholarCreateResponse(BaseModel):
    scholar: Dict[str, Any]


class ScholarUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    semantic_scholar_id: Optional[str] = Field(None, min_length=1, max_length=128)
    affiliations: Optional[List[str]] = None
    keywords: Optional[List[str]] = None
    research_fields: Optional[List[str]] = None
    muted: Optional[bool] = None
    last_seen_at: Optional[str] = None
    last_seen_cached_papers: Optional[int] = Field(None, ge=0)
    digest_enabled: Optional[bool] = None
    digest_frequency: Optional[str] = Field(None, pattern="^(daily|weekly|monthly)$")
    alert_enabled: Optional[bool] = None
    alert_keywords: Optional[List[str]] = None


class ScholarDeleteResponse(BaseModel):
    removed: bool
    scholar: Optional[Dict[str, Any]] = None


@router.post("/research/scholars", response_model=ScholarCreateResponse)
def create_tracked_scholar(req: ScholarCreateRequest):
    service = _get_subscription_service()
    try:
        scholar = service.add_scholar(
            {
                "name": req.name,
                "semantic_scholar_id": req.semantic_scholar_id,
                "affiliations": req.affiliations,
                "keywords": req.keywords,
                "research_fields": req.research_fields,
            }
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 409 if "already exists" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"failed to persist scholar: {exc}") from exc

    return ScholarCreateResponse(scholar=scholar)


@router.patch("/research/scholars/{scholar_ref}", response_model=ScholarCreateResponse)
def update_tracked_scholar(scholar_ref: str, req: ScholarUpdateRequest):
    service = _get_subscription_service()
    payload = {
        "name": req.name,
        "semantic_scholar_id": req.semantic_scholar_id,
        "affiliations": req.affiliations,
        "keywords": req.keywords,
        "research_fields": req.research_fields,
        "muted": req.muted,
        "last_seen_at": req.last_seen_at,
        "last_seen_cached_papers": req.last_seen_cached_papers,
        "digest_enabled": req.digest_enabled,
        "digest_frequency": req.digest_frequency,
        "alert_enabled": req.alert_enabled,
        "alert_keywords": req.alert_keywords,
    }
    try:
        scholar = service.update_scholar(scholar_ref, payload)
    except ValueError as exc:
        detail = str(exc)
        status_code = 409 if "already exists" in detail else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"failed to persist scholar update: {exc}"
        ) from exc

    if scholar is None:
        raise HTTPException(status_code=404, detail="Scholar not found")

    return ScholarCreateResponse(scholar=scholar)


@router.delete("/research/scholars/{scholar_ref}", response_model=ScholarDeleteResponse)
def delete_tracked_scholar(scholar_ref: str):
    service = _get_subscription_service()
    try:
        removed = service.remove_scholar(scholar_ref)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"failed to persist scholar removal: {exc}"
        ) from exc

    if removed is None:
        raise HTTPException(status_code=404, detail="Scholar not found")

    return ScholarDeleteResponse(removed=True, scholar=removed)


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


class ScholarSearchResponse(BaseModel):
    query: str
    items: List[Dict[str, Any]]
    total: int


@router.get("/research/scholars/search", response_model=ScholarSearchResponse)
async def search_scholar_candidates(
    query: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=50),
):
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY") or os.getenv("S2_API_KEY")
    client = SemanticScholarClient(api_key=api_key)
    try:
        rows = await client.search_authors(
            query=query,
            limit=max(1, int(limit)),
            fields=["name", "affiliations", "paperCount", "citationCount", "hIndex"],
        )
    finally:
        await client.close()

    items: List[Dict[str, Any]] = []
    for row in rows:
        name = str(row.get("name") or "").strip()
        author_id = str(row.get("authorId") or row.get("author_id") or "").strip()
        if not name or not author_id:
            continue

        affiliations_raw = row.get("affiliations") or []
        affiliations = []
        if isinstance(affiliations_raw, list):
            affiliations = [str(v).strip() for v in affiliations_raw if str(v).strip()]

        items.append(
            {
                "author_id": author_id,
                "name": name,
                "affiliations": affiliations,
                "affiliation": affiliations[0] if affiliations else "Unknown affiliation",
                "paper_count": _safe_int(row.get("paperCount"), 0),
                "citation_count": _safe_int(row.get("citationCount"), 0),
                "h_index": _safe_int(row.get("hIndex"), 0),
            }
        )

    return ScholarSearchResponse(query=query, items=items, total=len(items))


@router.get("/research/scholars", response_model=ScholarListResponse)
def list_tracked_scholars(limit: int = Query(100, ge=1, le=500)):
    from paperbot.agents.scholar_tracking.scholar_profile_agent import ScholarProfileAgent

    try:
        profile = ScholarProfileAgent()
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"failed to load scholar profile: {exc}"
        ) from exc

    now = datetime.now(timezone.utc)
    items: List[Dict[str, Any]] = []

    metadata_by_ref: Dict[str, Dict[str, Any]] = {}
    try:
        service = _get_subscription_service()
        for row in service.get_scholar_configs():
            semantic = str(row.get("semantic_scholar_id") or "").strip().lower()
            row_id = str(row.get("scholar_id") or row.get("id") or "").strip().lower()
            name = str(row.get("name") or "").strip().lower()
            for key in {semantic, row_id, name}:
                if key:
                    metadata_by_ref[key] = row
    except Exception:
        metadata_by_ref = {}

    for scholar in profile.list_tracked_scholars():
        semantic_id = str(scholar.semantic_scholar_id or "").strip()
        scholar_ref = semantic_id or str(scholar.scholar_id or "").strip()
        cache_stats = profile.get_cache_stats(scholar_ref) if scholar_ref else {}

        last_updated = cache_stats.get("last_updated")
        last_updated_dt = _parse_iso_datetime(last_updated)
        age_days: Optional[int] = None
        if last_updated_dt is not None:
            age_days = max(0, int((now - last_updated_dt).total_seconds() // 86400))

        if age_days is None:
            recent_activity = "No tracking runs yet"
            status = "idle"
        elif age_days == 0:
            recent_activity = "Updated today"
            status = "active"
        elif age_days == 1:
            recent_activity = "Updated 1 day ago"
            status = "active"
        else:
            recent_activity = f"Updated {age_days} days ago"
            status = "active" if age_days <= 30 else "idle"

        row_meta = (
            metadata_by_ref.get(semantic_id.lower())
            or metadata_by_ref.get(str(scholar.scholar_id or "").strip().lower())
            or metadata_by_ref.get(str(scholar.name or "").strip().lower())
        )

        items.append(
            {
                "id": scholar_ref or scholar.name,
                "scholar_id": str(scholar.scholar_id or scholar_ref),
                "semantic_scholar_id": semantic_id or None,
                "name": scholar.name,
                "affiliation": (
                    scholar.affiliations[0] if scholar.affiliations else "Unknown affiliation"
                ),
                "affiliations": list(scholar.affiliations or []),
                "keywords": list(scholar.keywords or []),
                "research_fields": list(scholar.research_fields or []),
                "h_index": _safe_int(scholar.h_index, 0),
                "citation_count": _safe_int(scholar.citation_count, 0),
                "paper_count": _safe_int(scholar.paper_count, 0),
                "cached_papers": _safe_int(cache_stats.get("paper_count"), 0),
                "cache_history_length": _safe_int(cache_stats.get("history_length"), 0),
                "last_updated": last_updated,
                "recent_activity": recent_activity,
                "status": status,
                "muted": bool(row_meta.get("muted")) if isinstance(row_meta, dict) else False,
                "last_seen_at": (
                    str(row_meta.get("last_seen_at") or "").strip()
                    if isinstance(row_meta, dict)
                    else None
                )
                or None,
                "last_seen_cached_papers": (
                    _safe_int(row_meta.get("last_seen_cached_papers"), 0)
                    if isinstance(row_meta, dict)
                    else 0
                ),
                "digest_enabled": (
                    bool(row_meta.get("digest_enabled")) if isinstance(row_meta, dict) else False
                ),
                "digest_frequency": (
                    str(row_meta.get("digest_frequency") or "weekly").strip()
                    if isinstance(row_meta, dict)
                    else "weekly"
                )
                or "weekly",
                "alert_enabled": (
                    bool(row_meta.get("alert_enabled")) if isinstance(row_meta, dict) else False
                ),
                "alert_keywords": (
                    [
                        str(keyword).strip()
                        for keyword in (row_meta.get("alert_keywords") or [])
                        if str(keyword).strip()
                    ]
                    if isinstance(row_meta, dict)
                    else []
                ),
            }
        )

    items.sort(
        key=lambda row: (
            _parse_iso_datetime(row.get("last_updated"))
            or datetime(1970, 1, 1, tzinfo=timezone.utc),
            str(row.get("name") or "").lower(),
        ),
        reverse=True,
    )

    return ScholarListResponse(items=items[: max(1, int(limit))], total=len(items))


class ScholarNetworkRequest(BaseModel):
    scholar_id: Optional[str] = None
    scholar_name: Optional[str] = None
    max_papers: int = Field(100, ge=1, le=500)
    recent_years: int = Field(5, ge=0, le=30)
    max_nodes: int = Field(40, ge=5, le=200)


class ScholarNetworkResponse(BaseModel):
    scholar: Dict[str, Any]
    stats: Dict[str, Any]
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]


class ScholarTrendsRequest(BaseModel):
    scholar_id: Optional[str] = None
    scholar_name: Optional[str] = None
    max_papers: int = Field(200, ge=1, le=1000)
    year_window: int = Field(10, ge=3, le=30)


class ScholarTrendsResponse(BaseModel):
    scholar: Dict[str, Any]
    stats: Dict[str, Any]
    publication_velocity: List[Dict[str, Any]]
    topic_distribution: List[Dict[str, Any]]
    venue_distribution: List[Dict[str, Any]]
    recent_papers: List[Dict[str, Any]]
    trend_summary: Dict[str, Any]


def _resolve_scholar_identity(
    *, scholar_id: Optional[str], scholar_name: Optional[str]
) -> Tuple[str, Optional[str]]:
    if scholar_id and scholar_id.strip():
        return scholar_id.strip(), None

    if not scholar_name or not scholar_name.strip():
        raise HTTPException(status_code=400, detail="scholar_id or scholar_name is required")

    from paperbot.agents.scholar_tracking.scholar_profile_agent import ScholarProfileAgent

    name_key = scholar_name.strip().lower()
    try:
        profile = ScholarProfileAgent()
        for scholar in profile.list_tracked_scholars():
            if (scholar.name or "").strip().lower() != name_key:
                continue
            if not scholar.semantic_scholar_id:
                break
            return scholar.semantic_scholar_id, scholar.name
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"failed to load scholar profile: {exc}"
        ) from exc

    raise HTTPException(
        status_code=404,
        detail="Scholar not found in subscriptions. Provide scholar_id directly or add scholar to subscriptions.",
    )


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _extract_year_from_paper(paper: Dict[str, Any]) -> Optional[int]:
    year = _safe_int(paper.get("year"), 0)
    if year > 0:
        return year

    date_value = str(paper.get("publicationDate") or paper.get("publication_date") or "")
    match = re.search(r"(20\d{2}|19\d{2})", date_value)
    if match:
        return _safe_int(match.group(1), 0) or None
    return None


def _unwrap_author_paper_row(row: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(row.get("paper"), dict):
        return row["paper"]
    return row


def _trend_direction(values: List[float]) -> str:
    if len(values) < 2:
        return "flat"
    pivot = max(1, len(values) // 2)
    older = sum(values[:pivot]) / max(1, len(values[:pivot]))
    recent = sum(values[pivot:]) / max(1, len(values[pivot:]))
    if recent > older * 1.15:
        return "up"
    if recent < older * 0.85:
        return "down"
    return "flat"


@router.post("/research/scholar/network", response_model=ScholarNetworkResponse)
async def scholar_network(req: ScholarNetworkRequest):
    scholar_id, resolved_name = _resolve_scholar_identity(
        scholar_id=req.scholar_id,
        scholar_name=req.scholar_name,
    )

    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY") or os.getenv("S2_API_KEY")
    client = SemanticScholarClient(api_key=api_key)
    try:
        author = await client.get_author(
            scholar_id,
            fields=["name", "affiliations", "paperCount", "citationCount", "hIndex"],
        )
        paper_rows = await client.get_author_papers(
            scholar_id,
            limit=max(1, int(req.max_papers)),
            fields=["title", "year", "citationCount", "authors", "url", "publicationDate"],
        )
    finally:
        await client.close()

    target_name = (author or {}).get("name") or resolved_name or req.scholar_name or scholar_id
    target_key = str(scholar_id)
    min_year: Optional[int] = None
    if req.recent_years > 0:
        min_year = datetime.now(timezone.utc).year - int(req.recent_years) + 1

    collaborators: Dict[str, Dict[str, Any]] = {}
    papers_used = 0

    for raw_row in paper_rows:
        paper = _unwrap_author_paper_row(raw_row)
        year = _extract_year_from_paper(paper)
        if min_year and year and year < min_year:
            continue

        parsed_authors: List[Tuple[str, str]] = []
        has_target_author = False
        for author_row in paper.get("authors") or []:
            if isinstance(author_row, dict):
                author_id = str(author_row.get("authorId") or "")
                author_name = str(author_row.get("name") or "").strip()
            else:
                author_id = ""
                author_name = str(author_row or "").strip()
            if not author_name:
                continue
            parsed_authors.append((author_id, author_name))
            if author_id and author_id == target_key:
                has_target_author = True
            elif not author_id and author_name.lower() == str(target_name).lower():
                has_target_author = True

        if not parsed_authors or not has_target_author:
            continue

        papers_used += 1
        paper_title = str(paper.get("title") or "Untitled")
        citation_count = _safe_int(paper.get("citationCount"), 0)

        for author_id, author_name in parsed_authors:
            if (author_id and author_id == target_key) or (
                not author_id and author_name.lower() == str(target_name).lower()
            ):
                continue

            node_id = f"author:{author_id}" if author_id else f"name:{author_name.lower()}"
            item = collaborators.setdefault(
                node_id,
                {
                    "id": node_id,
                    "author_id": author_id or None,
                    "name": author_name,
                    "collab_papers": 0,
                    "citation_sum": 0,
                    "recent_year": year,
                    "sample_titles": [],
                },
            )
            item["collab_papers"] += 1
            item["citation_sum"] += citation_count
            if year and (not item.get("recent_year") or year > int(item.get("recent_year") or 0)):
                item["recent_year"] = year
            if len(item["sample_titles"]) < 3:
                item["sample_titles"].append(paper_title)

    ranked = sorted(
        collaborators.values(),
        key=lambda row: (int(row["collab_papers"]), int(row["citation_sum"])),
        reverse=True,
    )
    ranked = ranked[: max(0, int(req.max_nodes) - 1)]

    nodes = [
        {
            "id": f"author:{target_key}",
            "author_id": target_key,
            "name": target_name,
            "type": "target",
            "collab_papers": papers_used,
            "citation_sum": _safe_int((author or {}).get("citationCount"), 0),
        }
    ]
    nodes.extend(
        [
            {
                "id": row["id"],
                "author_id": row["author_id"],
                "name": row["name"],
                "type": "coauthor",
                "collab_papers": row["collab_papers"],
                "citation_sum": row["citation_sum"],
                "recent_year": row.get("recent_year"),
            }
            for row in ranked
        ]
    )

    edges = [
        {
            "source": f"author:{target_key}",
            "target": row["id"],
            "weight": row["collab_papers"],
            "citation_sum": row["citation_sum"],
            "sample_titles": row["sample_titles"],
        }
        for row in ranked
    ]

    scholar_payload = {
        "scholar_id": target_key,
        "name": target_name,
        "affiliations": (author or {}).get("affiliations") or [],
        "paper_count": _safe_int((author or {}).get("paperCount"), 0),
        "citation_count": _safe_int((author or {}).get("citationCount"), 0),
        "h_index": _safe_int((author or {}).get("hIndex"), 0),
    }

    return ScholarNetworkResponse(
        scholar=scholar_payload,
        stats={
            "papers_fetched": len(paper_rows),
            "papers_used": papers_used,
            "coauthor_count": len(ranked),
            "recent_years": int(req.recent_years),
        },
        nodes=nodes,
        edges=edges,
    )


@router.post("/research/scholar/trends", response_model=ScholarTrendsResponse)
async def scholar_trends(req: ScholarTrendsRequest):
    scholar_id, resolved_name = _resolve_scholar_identity(
        scholar_id=req.scholar_id,
        scholar_name=req.scholar_name,
    )

    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY") or os.getenv("S2_API_KEY")
    client = SemanticScholarClient(api_key=api_key)
    try:
        author = await client.get_author(
            scholar_id,
            fields=["name", "affiliations", "paperCount", "citationCount", "hIndex"],
        )
        paper_rows = await client.get_author_papers(
            scholar_id,
            limit=max(1, int(req.max_papers)),
            fields=[
                "title",
                "year",
                "citationCount",
                "venue",
                "fieldsOfStudy",
                "publicationDate",
                "url",
            ],
        )
    finally:
        await client.close()

    current_year = datetime.now(timezone.utc).year
    min_year = current_year - int(req.year_window) + 1

    year_buckets: Dict[int, Dict[str, int]] = {}
    topic_counter: Counter[str] = Counter()
    venue_counter: Counter[str] = Counter()
    recent_papers: List[Dict[str, Any]] = []

    for raw_row in paper_rows:
        paper = _unwrap_author_paper_row(raw_row)
        year = _extract_year_from_paper(paper)
        if year is None or year < min_year:
            continue

        citation_count = _safe_int(paper.get("citationCount"), 0)
        bucket = year_buckets.setdefault(year, {"papers": 0, "citations": 0})
        bucket["papers"] += 1
        bucket["citations"] += citation_count

        for topic in paper.get("fieldsOfStudy") or paper.get("fields_of_study") or []:
            topic_name = str(topic).strip()
            if topic_name:
                topic_counter[topic_name] += 1

        venue = str(
            paper.get("venue")
            or (
                (paper.get("publicationVenue") or {}).get("name")
                if isinstance(paper.get("publicationVenue"), dict)
                else ""
            )
            or ""
        ).strip()
        if venue:
            venue_counter[venue] += 1

        recent_papers.append(
            {
                "title": paper.get("title") or "Untitled",
                "year": year,
                "citation_count": citation_count,
                "venue": venue,
                "url": paper.get("url") or "",
            }
        )

    yearly = [
        {
            "year": year,
            "papers": stats["papers"],
            "citations": stats["citations"],
        }
        for year, stats in sorted(year_buckets.items())
    ]

    recent_papers.sort(
        key=lambda row: (int(row.get("year") or 0), int(row.get("citation_count") or 0)),
        reverse=True,
    )

    paper_series = [float(row["papers"]) for row in yearly]
    citation_series = [float(row["citations"]) for row in yearly]

    trend_summary = {
        "publication_trend": _trend_direction(paper_series),
        "citation_trend": _trend_direction(citation_series),
        "active_years": len(yearly),
        "window": int(req.year_window),
    }

    scholar_payload = {
        "scholar_id": str(scholar_id),
        "name": (author or {}).get("name") or resolved_name or req.scholar_name or str(scholar_id),
        "affiliations": (author or {}).get("affiliations") or [],
        "paper_count": _safe_int((author or {}).get("paperCount"), 0),
        "citation_count": _safe_int((author or {}).get("citationCount"), 0),
        "h_index": _safe_int((author or {}).get("hIndex"), 0),
    }

    return ScholarTrendsResponse(
        scholar=scholar_payload,
        stats={
            "papers_fetched": len(paper_rows),
            "papers_in_window": sum(item["papers"] for item in yearly),
            "year_window": int(req.year_window),
        },
        publication_velocity=yearly,
        topic_distribution=[
            {"topic": topic, "count": count} for topic, count in topic_counter.most_common(15)
        ],
        venue_distribution=[
            {"venue": venue, "count": count} for venue, count in venue_counter.most_common(15)
        ],
        recent_papers=recent_papers[:10],
        trend_summary=trend_summary,
    )


# ---------------------------------------------------------------------------
# Paper export (BibTeX / RIS / Markdown)
# ---------------------------------------------------------------------------


def _build_s2_seed_id(seed_type: str, seed_id: str) -> str:
    value = str(seed_id or "").strip()
    kind = str(seed_type or "").strip().lower()
    if kind == "doi":
        doi = normalize_doi(value) or value
        return f"DOI:{doi}"
    if kind == "arxiv":
        arxiv_id = normalize_arxiv_id(value) or value
        return f"ARXIV:{arxiv_id}"
    if kind == "semantic_scholar":
        return value
    if kind == "openalex":
        return value
    return value


def _normalize_discovery_candidate(raw: Any, *, source: str) -> Dict[str, Any]:
    row = raw if isinstance(raw, dict) else {}
    external_ids = row.get("externalIds") if isinstance(row.get("externalIds"), dict) else {}
    ids_raw = row.get("ids") if isinstance(row.get("ids"), dict) else {}
    primary_location = (
        row.get("primary_location") if isinstance(row.get("primary_location"), dict) else {}
    )
    publication_venue = (
        row.get("publicationVenue") if isinstance(row.get("publicationVenue"), dict) else {}
    )
    host_venue = row.get("host_venue") if isinstance(row.get("host_venue"), dict) else {}
    doi = normalize_doi(row.get("doi") or external_ids.get("DOI") or ids_raw.get("doi"))
    arxiv_id = normalize_arxiv_id(
        row.get("arxiv_id")
        or external_ids.get("ArXiv")
        or external_ids.get("ARXIV")
        or ids_raw.get("arxiv")
        or (
            (row.get("locations") or [{}])[0].get("landing_page_url")
            if isinstance(row.get("locations"), list) and row.get("locations")
            else None
        )
        or row.get("url")
    )
    openalex_id = (
        str(row.get("id") or "").split("/")[-1]
        if str(row.get("id") or "").startswith("https://openalex.org/")
        else str(row.get("openalex_id") or "").strip()
    )
    semantic_scholar_id = str(
        row.get("paperId") or row.get("paper_id") or external_ids.get("CorpusId") or ""
    ).strip()

    title = str(row.get("title") or "").strip()
    year_raw = row.get("year") or row.get("publication_year")
    try:
        year = int(year_raw) if year_raw is not None else None
    except Exception:
        year = None
    citation_raw = row.get("citationCount") or row.get("cited_by_count") or 0
    try:
        citation_count = int(citation_raw or 0)
    except Exception:
        citation_count = 0

    authors: List[str] = []
    for author in row.get("authors") or row.get("authorships") or []:
        if isinstance(author, dict):
            name = str(
                author.get("name")
                or (author.get("author") or {}).get("display_name")
                or author.get("display_name")
                or ""
            ).strip()
            if name:
                authors.append(name)
        elif isinstance(author, str):
            if author.strip():
                authors.append(author.strip())

    venue = str(
        row.get("venue")
        or publication_venue.get("name")
        or host_venue.get("display_name")
        or (
            (primary_location.get("source") or {}).get("display_name")
            if isinstance(primary_location.get("source"), dict)
            else ""
        )
        or ""
    ).strip()
    url = str(
        row.get("url")
        or row.get("landing_page_url")
        or primary_location.get("landing_page_url")
        or ""
    ).strip()

    abstract = str(row.get("abstract") or row.get("abstract_inverted_index") or "").strip()
    candidate: Dict[str, Any] = {
        "title": title,
        "authors": authors,
        "year": year,
        "venue": venue,
        "citation_count": citation_count,
        "url": url,
        "abstract": abstract,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "openalex_id": openalex_id or None,
        "semantic_scholar_id": semantic_scholar_id or None,
        "source": source,
    }
    candidate["_candidate_key"] = _discovery_candidate_key(candidate)
    return candidate


def _discovery_candidate_key(candidate: Dict[str, Any]) -> str:
    doi = normalize_doi(candidate.get("doi"))
    if doi:
        return f"doi:{doi}"
    arxiv_id = normalize_arxiv_id(candidate.get("arxiv_id"))
    if arxiv_id:
        return f"arxiv:{arxiv_id.lower()}"
    s2 = str(candidate.get("semantic_scholar_id") or "").strip()
    if s2:
        return f"s2:{s2}"
    openalex_id = str(candidate.get("openalex_id") or "").strip()
    if openalex_id:
        return f"openalex:{openalex_id}"
    title = re.sub(r"\s+", " ", str(candidate.get("title") or "").strip().lower())
    year = str(candidate.get("year") or "")
    if title:
        return f"title:{title}|{year}"
    return ""


def _add_discovery_candidate(
    candidate_map: Dict[str, Dict[str, Any]],
    edge_map: Dict[Tuple[str, str, str], Dict[str, Any]],
    *,
    seed_node_id: str,
    edge_type: str,
    paper: Dict[str, Any],
) -> None:
    key = str(paper.get("_candidate_key") or "")
    if not key:
        return
    if not str(paper.get("title") or "").strip():
        return

    row = candidate_map.get(key)
    if row is None:
        row = {
            "_candidate_key": key,
            "paper": paper,
            "edge_types": set(),
            "_source_set": set(),
        }
        candidate_map[key] = row
    row["edge_types"].add(edge_type)
    row["_source_set"].add(str(paper.get("source") or ""))

    edge_key = (seed_node_id, key, edge_type)
    edge_map[edge_key] = {
        "source": seed_node_id,
        "target": key,
        "target_key": key,
        "type": edge_type,
        "weight": _edge_weight(edge_type),
    }


def _filter_discovery_candidates(
    rows: List[Dict[str, Any]],
    *,
    year_from: Optional[int],
    year_to: Optional[int],
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for row in rows:
        year = row.get("paper", {}).get("year")
        if year_from is not None and isinstance(year, int) and year < year_from:
            continue
        if year_to is not None and isinstance(year, int) and year > year_to:
            continue
        row["edge_types"] = sorted(list(row.get("edge_types") or []))
        result.append(row)
    return result


def _edge_weight(edge_type: str) -> float:
    return {
        "related": 1.8,
        "cited": 1.3,
        "citing": 1.1,
        "coauthor": 1.0,
    }.get(str(edge_type or "").strip().lower(), 1.0)


def _extract_profile_terms(text: str) -> List[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9\\-]{2,}", str(text or "").lower())
    terms = []
    for token in words:
        if token in _DISCOVERY_STOPWORDS:
            continue
        terms.append(token)
    return terms


def _build_feedback_profile(user_id: str, track_id: Optional[int]) -> Dict[str, float]:
    profile: Dict[str, float] = {}
    action_weight = {
        "save": 1.3,
        "like": 1.0,
        "cite": 1.1,
        "dislike": -1.2,
        "skip": -0.6,
    }
    limit = 400
    feedback_rows: List[Dict[str, Any]]
    if track_id is not None:
        feedback_rows = _get_research_store().list_effective_paper_feedback(
            user_id=user_id,
            track_id=int(track_id),
            limit=limit,
        )
    else:
        feedback_rows = []
        for track in _get_research_store().list_tracks(
            user_id=user_id, include_archived=False, limit=20
        ):
            feedback_rows.extend(
                _get_research_store().list_effective_paper_feedback(
                    user_id=user_id,
                    track_id=int(track.get("id") or 0),
                    limit=100,
                )
            )
            if len(feedback_rows) >= limit:
                break
        feedback_rows = feedback_rows[:limit]

    paper_store = _get_paper_store()
    for row in feedback_rows:
        action = str(row.get("action") or "").strip().lower()
        coeff = float(action_weight.get(action, 0.0))
        if abs(coeff) < 1e-6:
            continue
        paper_ref_id = row.get("paper_ref_id")
        if paper_ref_id is None:
            continue
        paper_model = paper_store.get_paper_by_source_id_any(str(paper_ref_id))
        if paper_model is None:
            continue
        paper_text = " ".join(
            [
                str(paper_model.title or ""),
                str(paper_model.venue or ""),
                " ".join(paper_model.get_keywords() or []),
                " ".join(paper_model.get_fields_of_study() or []),
            ]
        )
        for term in _extract_profile_terms(paper_text):
            profile[term] = profile.get(term, 0.0) + coeff

    saved_rows = _get_research_store().list_saved_papers(
        user_id=user_id, track_id=track_id, limit=200
    )
    for item in saved_rows:
        paper = item.get("paper") or {}
        paper_text = " ".join(
            [
                str(paper.get("title") or ""),
                str(paper.get("venue") or ""),
                " ".join(paper.get("keywords") or []),
            ]
        )
        for term in _extract_profile_terms(paper_text):
            profile[term] = profile.get(term, 0.0) + 0.5
    return profile


def _rank_discovery_candidates(
    rows: List[Dict[str, Any]],
    *,
    feedback_profile: Dict[str, float],
    limit: int,
) -> List[Dict[str, Any]]:
    scored: List[Dict[str, Any]] = []
    for row in rows:
        paper = row.get("paper") or {}
        edge_types = list(row.get("edge_types") or [])
        base_score = sum(_edge_weight(edge) for edge in edge_types)
        citation_score = min(float(paper.get("citation_count") or 0) / 500.0, 2.0)
        text_blob = " ".join(
            [
                str(paper.get("title") or ""),
                str(paper.get("abstract") or ""),
                str(paper.get("venue") or ""),
            ]
        )
        overlap_terms = []
        personalization_score = 0.0
        if feedback_profile:
            for term in _extract_profile_terms(text_blob):
                weight = float(feedback_profile.get(term, 0.0))
                if abs(weight) > 1e-6:
                    personalization_score += weight
                    if weight > 0:
                        overlap_terms.append(term)

        why = [f"linked via {', '.join(edge_types)}"] if edge_types else []
        if overlap_terms:
            why.append("matches your profile: " + ", ".join(sorted(set(overlap_terms))[:3]))
        score = round(base_score + citation_score + personalization_score * 0.2, 4)
        scored.append(
            {
                **row,
                "score": score,
                "why_this_paper": why,
            }
        )

    scored.sort(
        key=lambda item: (
            float(item.get("score") or 0.0),
            int((item.get("paper") or {}).get("citation_count") or 0),
            int((item.get("paper") or {}).get("year") or 0),
        ),
        reverse=True,
    )
    return scored[: max(1, int(limit))]


def _find_track_by_name(*, user_id: str, track_name: str) -> Optional[Dict[str, Any]]:
    target = (track_name or "").strip().casefold()
    if not target:
        return None
    tracks = _get_research_store().list_tracks(user_id=user_id, include_archived=True, limit=500)
    for track in tracks:
        if str(track.get("name") or "").strip().casefold() == target:
            return track
    return None


def _resolve_or_create_import_track(
    *,
    user_id: str,
    track_id: Optional[int],
    track_name: Optional[str],
    default_track_name: str,
) -> Dict[str, Any]:
    if track_id is not None:
        track = _get_research_store().get_track(user_id=user_id, track_id=int(track_id))
        if not track:
            raise HTTPException(status_code=404, detail="Track not found")
        return track

    if track_name:
        found = _find_track_by_name(user_id=user_id, track_name=track_name)
        if found:
            return found
        return _get_research_store().create_track(
            user_id=user_id,
            name=(track_name or "").strip(),
            description=f"Imported from {default_track_name.lower()}",
            activate=True,
        )

    active = _get_research_store().get_active_track(user_id=user_id)
    if active:
        return active

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _get_research_store().create_track(
        user_id=user_id,
        name=f"{default_track_name} {today}",
        description=f"Auto-created for {default_track_name.lower()}",
        activate=True,
    )


def _parse_bibtex_entries(content: str) -> List[Dict[str, Any]]:
    text = str(content or "")
    entries: List[Dict[str, Any]] = []
    cursor = 0
    size = len(text)
    while cursor < size:
        at_index = text.find("@", cursor)
        if at_index < 0:
            break

        entry, next_cursor = _parse_single_bibtex_entry(text, at_index)
        cursor = max(next_cursor, at_index + 1)
        if entry:
            entries.append(entry)
    return entries


def _parse_single_bibtex_entry(text: str, start: int) -> Tuple[Optional[Dict[str, Any]], int]:
    size = len(text)
    cursor = start + 1
    while cursor < size and text[cursor].isspace():
        cursor += 1

    entry_type_start = cursor
    while cursor < size and (text[cursor].isalnum() or text[cursor] in {"_", "-"}):
        cursor += 1
    entry_type = text[entry_type_start:cursor].strip().lower()
    if not entry_type:
        return None, cursor

    while cursor < size and text[cursor].isspace():
        cursor += 1
    if cursor >= size or text[cursor] not in {"{", "("}:
        return None, cursor

    opener = text[cursor]
    closer = "}" if opener == "{" else ")"
    cursor += 1
    body_start = cursor
    depth = 1
    in_quote = False
    escaped = False

    while cursor < size and depth > 0:
        ch = text[cursor]
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == '"':
            in_quote = not in_quote
        elif not in_quote:
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
        cursor += 1

    if depth != 0:
        return None, cursor

    body = text[body_start : cursor - 1].strip()
    parsed = _parse_bibtex_entry_body(body)
    if not parsed:
        return None, cursor
    parsed["entry_type"] = entry_type
    return parsed, cursor


def _parse_bibtex_entry_body(body: str) -> Optional[Dict[str, Any]]:
    if not body:
        return None

    split_idx = _find_top_level_char(body, ",")
    if split_idx < 0:
        return None

    citation_key = body[:split_idx].strip()
    fields_text = body[split_idx + 1 :]
    fields: Dict[str, str] = {}
    cursor = 0
    size = len(fields_text)

    while cursor < size:
        while cursor < size and fields_text[cursor] in {" ", "\t", "\n", "\r", ","}:
            cursor += 1
        if cursor >= size:
            break

        key_start = cursor
        while cursor < size and fields_text[cursor] not in {"=", ",", "\n", "\r"}:
            cursor += 1
        field_name = fields_text[key_start:cursor].strip().lower()
        while cursor < size and fields_text[cursor].isspace():
            cursor += 1
        if cursor >= size or fields_text[cursor] != "=":
            cursor += 1
            continue
        cursor += 1
        while cursor < size and fields_text[cursor].isspace():
            cursor += 1

        value, cursor = _read_bibtex_value(fields_text, cursor)
        if field_name:
            fields[field_name] = _clean_bibtex_text(value)

    return {"key": citation_key, "fields": fields}


def _read_bibtex_value(text: str, start: int) -> Tuple[str, int]:
    size = len(text)
    if start >= size:
        return "", size

    ch = text[start]
    if ch == "{":
        cursor = start + 1
        depth = 1
        while cursor < size and depth > 0:
            token = text[cursor]
            if token == "{":
                depth += 1
            elif token == "}":
                depth -= 1
            cursor += 1
        value = text[start + 1 : max(start + 1, cursor - 1)]
        return value, _consume_bibtex_value_tail(text, cursor)

    if ch == '"':
        cursor = start + 1
        escaped = False
        while cursor < size:
            token = text[cursor]
            if escaped:
                escaped = False
            elif token == "\\":
                escaped = True
            elif token == '"':
                cursor += 1
                break
            cursor += 1
        value = text[start + 1 : max(start + 1, cursor - 1)]
        return value, _consume_bibtex_value_tail(text, cursor)

    cursor = start
    while cursor < size and text[cursor] not in {",", "\n", "\r"}:
        cursor += 1
    value = text[start:cursor]
    return value, _consume_bibtex_value_tail(text, cursor)


def _consume_bibtex_value_tail(text: str, start: int) -> int:
    cursor = start
    size = len(text)
    while cursor < size and text[cursor].isspace():
        cursor += 1
    if cursor < size and text[cursor] == ",":
        cursor += 1
    return cursor


def _find_top_level_char(text: str, token: str) -> int:
    depth = 0
    in_quote = False
    escaped = False
    for idx, ch in enumerate(text):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_quote = not in_quote
            continue
        if in_quote:
            continue
        if ch == "{":
            depth += 1
            continue
        if ch == "}":
            depth = max(0, depth - 1)
            continue
        if depth == 0 and ch == token:
            return idx
    return -1


def _clean_bibtex_text(value: str) -> str:
    text = str(value or "").strip()
    text = " ".join(text.split())
    text = text.replace("\\{", "{").replace("\\}", "}")
    text = text.replace("\\&", "&").replace("\\_", "_").replace("\\%", "%")
    text = text.replace("{", "").replace("}", "")
    return text.strip()


def _parse_bibtex_authors(raw: str) -> List[str]:
    normalized = str(raw or "").strip()
    if not normalized:
        return []

    chunks: List[str] = []
    current_tokens: List[str] = []
    for token in normalized.split():
        if token.lower() == "and":
            chunk = " ".join(current_tokens).strip()
            if chunk:
                chunks.append(chunk)
            current_tokens = []
            continue
        current_tokens.append(token)

    tail = " ".join(current_tokens).strip()
    if tail:
        chunks.append(tail)

    authors: List[str] = []
    for chunk in chunks:
        if not chunk:
            continue
        if "," in chunk:
            parts = [item.strip() for item in chunk.split(",") if item.strip()]
            if len(parts) >= 2:
                authors.append(f"{parts[1]} {parts[0]}".strip())
                continue
        authors.append(chunk)
    return authors


def _extract_year(value: str) -> Optional[int]:
    match = re.search(r"(19|20)\d{2}", str(value or ""))
    if not match:
        return None
    try:
        return int(match.group(0))
    except Exception:
        return None


def _bibtex_entry_to_paper(entry: Dict[str, Any]) -> Dict[str, Any]:
    fields = dict(entry.get("fields") or {})
    title = str(fields.get("title") or "").strip()
    authors = _parse_bibtex_authors(str(fields.get("author") or ""))
    year = _extract_year(str(fields.get("year") or fields.get("date") or ""))
    venue = (
        str(fields.get("journal") or "")
        or str(fields.get("booktitle") or "")
        or str(fields.get("publisher") or "")
    ).strip()
    doi = normalize_doi(fields.get("doi"))
    url = str(fields.get("url") or "").strip()
    arxiv_id = normalize_arxiv_id(fields.get("eprint"))
    if arxiv_id is None:
        arxiv_id = normalize_arxiv_id(fields.get("arxiv"))
    if arxiv_id is None:
        arxiv_id = normalize_arxiv_id(fields.get("note"))
    if arxiv_id is None:
        arxiv_id = normalize_arxiv_id(url)

    identities: List[Dict[str, str]] = []
    if doi:
        identities.append({"source": "doi", "external_id": doi})
    if arxiv_id:
        identities.append({"source": "arxiv", "external_id": arxiv_id})

    paper: Dict[str, Any] = {
        "title": title,
        "authors": authors,
        "year": year,
        "venue": venue,
        "abstract": str(fields.get("abstract") or "").strip(),
        "url": url,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "source": "bibtex",
        "primary_source": "bibtex",
        "identities": identities,
    }

    citation_key = str(entry.get("key") or "").strip()
    if citation_key:
        paper["metadata"] = {"citation_key": citation_key}
    return paper


def _make_citation_key(authors: List[str], year: Optional[int]) -> str:
    """first_author_lastname + year, e.g. 'smith2025'."""
    lastname = "unknown"
    if authors:
        parts = authors[0].strip().split()
        if parts:
            lastname = re.sub(r"[^a-zA-Z]", "", parts[-1]).lower() or "unknown"
    return f"{lastname}{year or 'nd'}"


def _dedup_citation_keys(keys: List[str]) -> List[str]:
    """Append a/b/c suffixes when keys collide."""
    seen: Dict[str, int] = {}
    result: List[str] = []
    for k in keys:
        count = seen.get(k, 0)
        seen[k] = count + 1
        result.append(k if count == 0 else f"{k}{chr(ord('a') + count)}")
    return result


def _escape_bibtex(value: str) -> str:
    return value.replace("{", "\\{").replace("}", "\\}")


def _paper_to_bibtex(paper: Dict[str, Any], key: str) -> str:
    entry_type = "article" if paper.get("doi") else "misc"
    lines = [f"@{entry_type}{{{key},"]
    lines.append(f"  title = {{{_escape_bibtex(paper.get('title') or '')}}},")
    authors = paper.get("authors") or []
    if authors:
        lines.append(f"  author = {{{_escape_bibtex(' and '.join(authors))}}},")
    if paper.get("year"):
        lines.append(f"  year = {{{paper['year']}}},")
    if paper.get("venue"):
        field = "journal" if paper.get("doi") else "booktitle"
        lines.append(f"  {field} = {{{_escape_bibtex(paper['venue'])}}},")
    if paper.get("doi"):
        lines.append(f"  doi = {{{paper['doi']}}},")
    if paper.get("url"):
        lines.append(f"  url = {{{paper['url']}}},")
    if paper.get("arxiv_id"):
        lines.append(f"  eprint = {{{paper['arxiv_id']}}},")
        lines.append("  archiveprefix = {arXiv},")
    lines.append("}")
    return "\n".join(lines)


def _paper_to_ris(paper: Dict[str, Any]) -> str:
    venue = (paper.get("venue") or "").lower()
    is_conf = any(kw in venue for kw in ("conf", "proc", "sympos", "workshop"))
    lines = [f"TY  - {'CONF' if is_conf else 'JOUR'}"]
    lines.append(f"TI  - {paper.get('title') or ''}")
    for author in paper.get("authors") or []:
        lines.append(f"AU  - {author}")
    if paper.get("year"):
        lines.append(f"PY  - {paper['year']}")
    if paper.get("venue"):
        lines.append(f"JO  - {paper['venue']}")
    if paper.get("doi"):
        lines.append(f"DO  - {paper['doi']}")
    if paper.get("url"):
        lines.append(f"UR  - {paper['url']}")
    if paper.get("arxiv_id"):
        lines.append(f"AN  - {paper['arxiv_id']}")
    lines.append("ER  - ")
    return "\n".join(lines)


def _paper_to_markdown(paper: Dict[str, Any]) -> str:
    authors = ", ".join(paper.get("authors") or ["Unknown"])
    title = paper.get("title") or "Untitled"
    year = paper.get("year") or "n.d."
    venue = paper.get("venue") or ""
    parts = [f"- **{title}**", f"  {authors} ({year})"]
    if venue:
        parts.append(f"  *{venue}*")
    links: List[str] = []
    if paper.get("url"):
        links.append(f"[URL]({paper['url']})")
    if paper.get("doi"):
        links.append(f"[DOI](https://doi.org/{paper['doi']})")
    if paper.get("arxiv_id"):
        links.append(f"[arXiv](https://arxiv.org/abs/{paper['arxiv_id']})")
    if links:
        parts.append(f"  {' | '.join(links)}")
    return "\n".join(parts)


def _paper_to_csl_json(paper: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a paper dict to CSL-JSON format (Zotero native import)."""
    authors_raw = paper.get("authors") or []
    csl_authors = []
    for name in authors_raw:
        parts = name.strip().split()
        if len(parts) >= 2:
            csl_authors.append({"family": parts[-1], "given": " ".join(parts[:-1])})
        elif parts:
            csl_authors.append({"family": parts[0], "given": ""})

    item: Dict[str, Any] = {
        "type": "article-journal",
        "title": paper.get("title") or "",
        "author": csl_authors,
    }
    if paper.get("year"):
        item["issued"] = {"date-parts": [[paper["year"]]]}
    if paper.get("venue"):
        item["container-title"] = paper["venue"]
    if paper.get("doi"):
        item["DOI"] = paper["doi"]
    if paper.get("url"):
        item["URL"] = paper["url"]
    if paper.get("abstract"):
        item["abstract"] = paper["abstract"]
    return item


# ---------------------------------------------------------------------------
# Structured Card (LLM-extracted method/dataset/conclusion/limitations)
# ---------------------------------------------------------------------------

_llm_service: Optional["LLMService"] = None


def _get_llm_service() -> "LLMService":
    from paperbot.application.services.llm_service import get_llm_service

    global _llm_service
    if _llm_service is None:
        _llm_service = get_llm_service()
    return _llm_service


class StructuredCardResponse(BaseModel):
    paper_id: str
    structured_card: Dict[str, Any]


@router.get("/research/papers/{paper_id}/card", response_model=StructuredCardResponse)
def get_structured_card(paper_id: str, user_id: str = Depends(get_required_user_id)):
    detail = _get_research_store().get_paper_detail(paper_id=paper_id, user_id=user_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Paper not found")

    # Check if already cached in DB
    paper_store = _get_paper_store()
    db_paper = paper_store.get_paper_by_source_id_any(paper_id)
    if db_paper and db_paper.structured_card_json:
        import json as _json

        try:
            card = _json.loads(db_paper.structured_card_json)
            return StructuredCardResponse(paper_id=paper_id, structured_card=card)
        except Exception:
            pass

    # Extract via LLM
    title = str(detail.get("title") or "")
    abstract = str(detail.get("abstract") or "")
    if not abstract:
        return StructuredCardResponse(
            paper_id=paper_id,
            structured_card={
                "method": "",
                "dataset": "N/A",
                "conclusion": "",
                "limitations": "Not stated",
            },
        )

    llm = _get_llm_service()
    card = llm.extract_structured_card(title=title, abstract=abstract)

    # Cache in DB if we have a paper record
    if db_paper:
        try:
            import json as _json

            paper_store.update_structured_card(db_paper.id, _json.dumps(card, ensure_ascii=False))
        except Exception:
            pass

    return StructuredCardResponse(paper_id=paper_id, structured_card=card)


# ---------------------------------------------------------------------------
# Related Work draft generation
# ---------------------------------------------------------------------------


class RelatedWorkRequest(BaseModel):
    track_id: Optional[int] = None
    topic: str = Field(..., min_length=1)
    paper_ids: Optional[List[str]] = None
    limit: int = Field(20, ge=1, le=50)


class RelatedWorkResponse(BaseModel):
    markdown: str
    citations: List[Dict[str, Any]]


@router.post("/research/papers/related-work", response_model=RelatedWorkResponse)
def generate_related_work(req: RelatedWorkRequest, user_id: str = Depends(get_required_user_id)):
    items = _get_research_store().list_saved_papers(
        user_id=user_id, track_id=req.track_id, limit=req.limit
    )
    papers = [item["paper"] for item in items if item.get("paper")]

    if req.paper_ids:
        id_set = set(req.paper_ids)
        papers = [
            p
            for p in papers
            if str(p.get("id") or "") in id_set or str(p.get("paper_id") or "") in id_set
        ]

    if not papers:
        raise HTTPException(status_code=404, detail="No saved papers found")

    llm = _get_llm_service()
    markdown = llm.generate_related_work(papers=papers, topic=req.topic)

    # Extract citation keys from the generated text
    citations: List[Dict[str, Any]] = []
    for p in papers:
        authors = p.get("authors") or []
        year = p.get("year")
        lastname = "Unknown"
        if authors:
            parts = authors[0].strip().split()
            if parts:
                lastname = parts[-1]
        key = f"{lastname}{year or 'nd'}"
        citations.append(
            {
                "key": key,
                "title": p.get("title") or "",
                "authors": authors,
                "year": year,
            }
        )

    return RelatedWorkResponse(markdown=markdown, citations=citations)
