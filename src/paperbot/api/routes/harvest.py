# src/paperbot/api/routes/harvest.py
"""
Paper Harvest API Routes.

Provides endpoints for:
- Paper harvesting from multiple sources
- Paper search and retrieval
- User's paper library management
- Harvest run history
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from paperbot.api.streaming import StreamEvent, sse_response
from paperbot.application.workflows.harvest_pipeline import (
    HarvestConfig,
    HarvestFinalResult,
    HarvestPipeline,
    HarvestProgress,
)
from paperbot.api.auth.dependencies import get_required_user_id
from paperbot.infrastructure.stores.paper_store import PaperStore, paper_to_dict
from paperbot.utils.logging_config import LogFiles, Logger, set_trace_id

router = APIRouter()

# Lazy-initialized stores
_paper_store: Optional[PaperStore] = None
_research_store: Optional["SqlAlchemyResearchStore"] = None


def _get_paper_store() -> PaperStore:
    """Lazy initialization of paper store."""
    global _paper_store
    if _paper_store is None:
        _paper_store = PaperStore()
    return _paper_store


def _get_research_store() -> "SqlAlchemyResearchStore":
    """Lazy initialization of research store."""
    from paperbot.infrastructure.stores.research_store import SqlAlchemyResearchStore

    global _research_store
    if _research_store is None:
        _research_store = SqlAlchemyResearchStore()
    return _research_store


# ============================================================================
# Harvest Endpoints
# ============================================================================


class HarvestRequest(BaseModel):
    """Request body for harvest endpoint."""

    keywords: List[str] = Field(..., min_length=1, description="Search keywords")
    venues: Optional[List[str]] = Field(None, description="Filter to specific venues")
    year_from: Optional[int] = Field(None, ge=1900, le=2100, description="Start year")
    year_to: Optional[int] = Field(None, ge=1900, le=2100, description="End year")
    max_results_per_source: int = Field(50, ge=1, le=200, description="Max papers per source")
    sources: Optional[List[str]] = Field(
        None, description="Sources to harvest (arxiv, semantic_scholar, openalex)"
    )
    expand_keywords: bool = Field(True, description="Expand abbreviations")
    recommend_venues: bool = Field(True, description="Auto-recommend venues if not specified")


async def harvest_stream(request: HarvestRequest):
    """Stream harvest progress via SSE."""
    config = HarvestConfig(
        keywords=request.keywords,
        venues=request.venues,
        year_from=request.year_from,
        year_to=request.year_to,
        sources=request.sources,
        max_results_per_source=request.max_results_per_source,
        expand_keywords=request.expand_keywords,
        recommend_venues=request.recommend_venues,
    )

    pipeline = HarvestPipeline()
    try:
        async for item in pipeline.run(config):
            if isinstance(item, HarvestProgress):
                yield StreamEvent(
                    type="progress",
                    data={
                        "phase": item.phase,
                        "message": item.message,
                        "details": item.details,
                    },
                )
            elif isinstance(item, HarvestFinalResult):
                yield StreamEvent(
                    type="result",
                    data={
                        "run_id": item.run_id,
                        "status": item.status,
                        "papers_found": item.papers_found,
                        "papers_new": item.papers_new,
                        "papers_deduplicated": item.papers_deduplicated,
                        "sources": item.source_results,
                        "errors": item.errors,
                        "duration_seconds": item.duration_seconds,
                    },
                )
    except Exception as e:
        yield StreamEvent(type="error", message=str(e))
    finally:
        await pipeline.close()


@router.post("/harvest")
async def harvest_papers(request: HarvestRequest):
    """
    Harvest papers from multiple sources.

    Returns Server-Sent Events with progress updates.
    """
    set_trace_id()
    Logger.info(f"Starting harvest request: keywords={request.keywords}", file=LogFiles.HARVEST)
    return sse_response(harvest_stream(request), workflow="harvest")


class HarvestRunResponse(BaseModel):
    """Response for harvest run details."""

    run_id: str
    keywords: List[str]
    venues: List[str]
    sources: List[str]
    max_results_per_source: int
    status: str
    papers_found: int
    papers_new: int
    papers_deduplicated: int
    errors: Dict[str, Any]
    started_at: Optional[str]
    ended_at: Optional[str]


class HarvestRunListResponse(BaseModel):
    """Response for list of harvest runs."""

    runs: List[HarvestRunResponse]


# TODO(auth): This endpoint lists all harvest runs without user-based filtering.
# Intentional for MVP single-user setup. For multi-user production, add user_id
# filtering so users only see their own harvest runs.
@router.get("/harvest/runs", response_model=HarvestRunListResponse)
def list_harvest_runs(
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List harvest runs with optional filtering."""
    store = _get_paper_store()
    runs = store.list_harvest_runs(status=status, limit=limit, offset=offset)

    return HarvestRunListResponse(
        runs=[
            HarvestRunResponse(
                run_id=run.run_id,
                keywords=run.get_keywords(),
                venues=run.get_venues(),
                sources=run.get_sources(),
                max_results_per_source=run.max_results_per_source or 50,
                status=run.status or "unknown",
                papers_found=run.papers_found or 0,
                papers_new=run.papers_new or 0,
                papers_deduplicated=run.papers_deduplicated or 0,
                errors=run.get_errors(),
                started_at=run.started_at.isoformat() if run.started_at else None,
                ended_at=run.ended_at.isoformat() if run.ended_at else None,
            )
            for run in runs
        ]
    )


@router.get("/harvest/runs/{run_id}", response_model=HarvestRunResponse)
def get_harvest_run(run_id: str):
    """Get details of a specific harvest run."""
    store = _get_paper_store()
    run = store.get_harvest_run(run_id)

    if not run:
        raise HTTPException(status_code=404, detail="Harvest run not found")

    return HarvestRunResponse(
        run_id=run.run_id,
        keywords=run.get_keywords(),
        venues=run.get_venues(),
        sources=run.get_sources(),
        max_results_per_source=run.max_results_per_source or 50,
        status=run.status or "unknown",
        papers_found=run.papers_found or 0,
        papers_new=run.papers_new or 0,
        papers_deduplicated=run.papers_deduplicated or 0,
        errors=run.get_errors(),
        started_at=run.started_at.isoformat() if run.started_at else None,
        ended_at=run.ended_at.isoformat() if run.ended_at else None,
    )


# ============================================================================
# Paper Search Endpoints
# ============================================================================


class PaperSearchRequest(BaseModel):
    """Request body for paper search."""

    query: Optional[str] = Field(None, description="Full-text search query")
    keywords: Optional[List[str]] = Field(None, description="Keyword filters")
    venues: Optional[List[str]] = Field(None, description="Venue filters")
    year_from: Optional[int] = Field(None, ge=1900, le=2100)
    year_to: Optional[int] = Field(None, ge=1900, le=2100)
    min_citations: Optional[int] = Field(None, ge=0)
    sources: Optional[List[str]] = Field(None, description="Source filters")
    sort_by: str = Field("citation_count", description="Sort field")
    sort_order: str = Field("desc", description="Sort order (asc/desc)")
    limit: int = Field(50, ge=1, le=500)
    offset: int = Field(0, ge=0)


class PaperResponse(BaseModel):
    """Single paper response."""

    id: int
    doi: Optional[str]
    arxiv_id: Optional[str]
    semantic_scholar_id: Optional[str]
    openalex_id: Optional[str]
    title: str
    abstract: str
    authors: List[str]
    year: Optional[int]
    venue: Optional[str]
    publication_date: Optional[str]
    citation_count: int
    url: Optional[str]
    pdf_url: Optional[str]
    keywords: List[str]
    fields_of_study: List[str]
    primary_source: str
    sources: List[str]
    created_at: Optional[str]
    updated_at: Optional[str]


class PaperSearchResponse(BaseModel):
    """Response for paper search."""

    papers: List[Dict[str, Any]]
    total: int
    limit: int
    offset: int


@router.post("/papers/search", response_model=PaperSearchResponse)
def search_papers(request: PaperSearchRequest):
    """Search papers with filters and pagination."""
    set_trace_id()  # Initialize trace_id for this request
    Logger.info(f"Searching papers: query={request.query}", file=LogFiles.HARVEST)
    store = _get_paper_store()

    papers, total = store.search_papers(
        query=request.query,
        keywords=request.keywords,
        venues=request.venues,
        year_from=request.year_from,
        year_to=request.year_to,
        min_citations=request.min_citations,
        sources=request.sources,
        sort_by=request.sort_by,
        sort_order=request.sort_order,
        limit=request.limit,
        offset=request.offset,
    )

    return PaperSearchResponse(
        papers=[paper_to_dict(p) for p in papers],
        total=total,
        limit=request.limit,
        offset=request.offset,
    )


@router.get("/papers/stats")
def get_paper_stats():
    """Get paper collection statistics."""
    store = _get_paper_store()
    return {"total_papers": store.get_paper_count()}


# ============================================================================
# User Library Endpoints
# ============================================================================


class LibraryPaperResponse(BaseModel):
    """Paper in user's library."""

    paper: Dict[str, Any]
    saved_at: str
    track_id: Optional[int]
    action: str


class LibraryResponse(BaseModel):
    """Response for user library."""

    papers: List[LibraryPaperResponse]
    total: int
    limit: int
    offset: int


@router.get("/papers/library", response_model=LibraryResponse)
def get_user_library(
    user_id: str = Depends(get_required_user_id),
    track_id: Optional[int] = Query(None, description="Filter by track"),
    actions: Optional[str] = Query(None, description="Filter by actions (comma-separated)"),
    sort_by: str = Query("saved_at", description="Sort field"),
    sort_order: str = Query("desc", description="Sort order"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Get user's paper library (saved papers)."""
    set_trace_id()  # Initialize trace_id for this request
    Logger.info("Received request to get user library", file=LogFiles.HARVEST)
    store = _get_paper_store()

    action_list = None
    if actions:
        action_list = [a.strip() for a in actions.split(",") if a.strip()]

    Logger.info("Fetching papers from library store", file=LogFiles.HARVEST)
    library_papers, total = store.get_user_library(
        user_id=user_id,
        track_id=track_id,
        actions=action_list,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
    )

    Logger.info(
        f"Retrieved {len(library_papers)} papers from library, total={total}", file=LogFiles.HARVEST
    )
    return LibraryResponse(
        papers=[
            LibraryPaperResponse(
                paper=paper_to_dict(lp.paper),
                saved_at=lp.saved_at.isoformat() if lp.saved_at else "",
                track_id=lp.track_id,
                action=lp.action,
            )
            for lp in library_papers
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


# NOTE: Parameterized routes must come AFTER specific routes like /papers/stats and /papers/library
@router.get("/papers/{paper_id}")
def get_paper(paper_id: int):
    """Get a paper by ID."""
    store = _get_paper_store()
    paper = store.get_paper_by_id(paper_id)

    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")

    return {"paper": paper_to_dict(paper)}


class SavePaperRequest(BaseModel):
    """Request to save paper to library."""

    track_id: Optional[int] = Field(None, description="Associated track ID")
    metadata: Dict[str, Any] = Field(default_factory=dict)


@router.post("/papers/{paper_id}/save")
def save_paper_to_library(
    paper_id: int,
    request: SavePaperRequest,
    user_id: str = Depends(get_required_user_id),
):
    """
    Save a paper to user's library.

    Uses paper_feedback table with action='save'.
    """
    # Verify paper exists
    store = _get_paper_store()
    paper = store.get_paper_by_id(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")

    # Use research store to record feedback
    research_store = _get_research_store()
    feedback = research_store.add_paper_feedback(
        user_id=user_id,
        paper_id=str(paper_id),
        action="save",
        track_id=request.track_id,
        metadata=dict(request.metadata or {}),
        weight=0.0,
    )

    return {"success": True, "feedback": feedback}


@router.delete("/papers/{paper_id}/save")
def remove_paper_from_library(
    paper_id: int,
    user_id: str = Depends(get_required_user_id),
):
    """Remove a paper from user's library."""
    store = _get_paper_store()
    removed = store.remove_from_library(user_id, paper_id)

    if not removed:
        raise HTTPException(status_code=404, detail="Paper not in library")

    return {"success": True}
