from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel, Field

from paperbot.infrastructure.services.intelligence_radar_service import IntelligenceRadarService
from paperbot.infrastructure.stores.research_store import SqlAlchemyResearchStore
from paperbot.api.auth.dependencies import get_required_user_id

router = APIRouter()
_service: Optional[IntelligenceRadarService] = None
_research_store = SqlAlchemyResearchStore()
_SIGNAL_STOPWORDS = {
    "about",
    "across",
    "after",
    "agent",
    "agents",
    "comments",
    "community",
    "detected",
    "discussion",
    "github",
    "latest",
    "paper",
    "papers",
    "post",
    "recent",
    "reddit",
    "release",
    "repo",
    "research",
    "signal",
    "spike",
    "thread",
    "top",
    "watch",
    "watched",
    "with",
    "workflow",
    "x",
}


def _get_service() -> IntelligenceRadarService:
    global _service
    if _service is None:
        _service = IntelligenceRadarService()
    return _service


class IntelligenceMetricResponse(BaseModel):
    name: str = ""
    value: int = 0
    delta: int = 0


class IntelligenceMatchedTrackResponse(BaseModel):
    track_id: int
    track_name: str
    matched_keywords: List[str] = Field(default_factory=list)


class IntelligenceFeedItemResponse(BaseModel):
    id: str
    source: str
    source_label: str
    kind: str
    title: str
    summary: str
    url: str = ""
    repo_full_name: str = ""
    author_name: str = ""
    keyword_hits: List[str] = Field(default_factory=list)
    author_matches: List[str] = Field(default_factory=list)
    repo_matches: List[str] = Field(default_factory=list)
    match_reasons: List[str] = Field(default_factory=list)
    score: float = 0.0
    metric: IntelligenceMetricResponse = Field(default_factory=IntelligenceMetricResponse)
    published_at: Optional[str] = None
    detected_at: Optional[str] = None
    matched_tracks: List[IntelligenceMatchedTrackResponse] = Field(default_factory=list)
    research_query: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)


class IntelligenceFeedResponse(BaseModel):
    items: List[IntelligenceFeedItemResponse] = Field(default_factory=list)
    refreshed_at: Optional[str] = None
    refresh_scheduled: bool = False
    keywords: List[str] = Field(default_factory=list)
    watch_repos: List[str] = Field(default_factory=list)
    subreddits: List[str] = Field(default_factory=list)



@router.get("/intelligence/feed", response_model=IntelligenceFeedResponse)
def get_intelligence_feed(
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_required_user_id),
    limit: int = Query(6, ge=1, le=20),
    refresh: bool = Query(False),
    source: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    repo: Optional[str] = Query(None),
    sort_by: str = Query(
        "delta",
        pattern="^(delta|score|source|keyword|repo|published_at|detected_at|freshness)$",
    ),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    track_id: Optional[int] = Query(None, ge=1),
):
    service = _get_service()
    refresh_scheduled = False

    if refresh:
        service.refresh(user_id=user_id)
    elif service.needs_refresh(user_id=user_id):
        cached = service.list_feed(user_id=user_id, limit=1)
        if cached:
            background_tasks.add_task(service.refresh, user_id=user_id)
            refresh_scheduled = True
        else:
            service.refresh(user_id=user_id)

    rows = service.list_feed(
        user_id=user_id,
        limit=max(int(limit) * 10, 50),
        source=source,
        keyword=keyword,
        repo=repo,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    annotated_rows = [_annotate_intelligence_row(user_id=user_id, row=row) for row in rows]
    if track_id:
        annotated_rows = [
            row
            for row in annotated_rows
            if any(int(track.get("track_id") or 0) == int(track_id) for track in row.get("matched_tracks") or [])
        ]

    profile = service.build_profile(user_id=user_id)

    return IntelligenceFeedResponse(
        items=[_to_response_item(row) for row in annotated_rows[: max(1, int(limit))]],
        refreshed_at=service.latest_refresh(user_id=user_id),
        refresh_scheduled=refresh_scheduled,
        keywords=profile.keywords,
        watch_repos=profile.watch_repos,
        subreddits=profile.subreddits,
    )


def _to_response_item(row: Dict[str, Any]) -> IntelligenceFeedItemResponse:
    return IntelligenceFeedItemResponse(
        id=str(row.get("external_id") or row.get("id") or ""),
        source=str(row.get("source") or "unknown"),
        source_label=str(row.get("source_label") or ""),
        kind=str(row.get("kind") or "signal"),
        title=str(row.get("title") or "Untitled signal"),
        summary=str(row.get("summary") or ""),
        url=str(row.get("url") or ""),
        repo_full_name=str(row.get("repo_full_name") or ""),
        author_name=str(row.get("author_name") or ""),
        keyword_hits=list(row.get("keyword_hits") or []),
        author_matches=list(row.get("author_matches") or []),
        repo_matches=list(row.get("repo_matches") or []),
        match_reasons=list(row.get("match_reasons") or []),
        score=float(row.get("score") or 0.0),
        metric=IntelligenceMetricResponse(
            name=str(row.get("metric_name") or ""),
            value=int(row.get("metric_value") or 0),
            delta=int(row.get("metric_delta") or 0),
        ),
        published_at=row.get("published_at"),
        detected_at=row.get("detected_at"),
        matched_tracks=list(row.get("matched_tracks") or []),
        research_query=str(row.get("research_query") or ""),
        payload=dict(row.get("payload") or {}),
    )


def _annotate_intelligence_row(*, user_id: str, row: Dict[str, Any]) -> Dict[str, Any]:
    annotated = dict(row)
    matched_tracks = _match_tracks_to_signal(user_id=user_id, row=annotated)
    annotated["matched_tracks"] = matched_tracks
    annotated["research_query"] = _build_research_query(annotated, matched_tracks)
    return annotated


def _match_tracks_to_signal(*, user_id: str, row: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        tracks = _research_store.list_tracks(user_id=user_id, include_archived=False, limit=200)
    except Exception:
        tracks = []

    signal_tokens = _expand_signal_tokens(row)
    matches: List[Dict[str, Any]] = []
    for track in tracks:
        track_id = int(track.get("id") or 0)
        if track_id <= 0:
            continue
        overlap = sorted(signal_tokens & _expand_track_tokens(track))
        if overlap:
            matches.append(
                {
                    "track_id": track_id,
                    "track_name": str(track.get("name") or ""),
                    "matched_keywords": overlap[:6],
                }
            )
    return matches


def _build_research_query(row: Dict[str, Any], matched_tracks: List[Dict[str, Any]]) -> str:
    terms: List[str] = []
    for track in matched_tracks:
        terms.extend(track.get("matched_keywords") or [])
    terms.extend(row.get("keyword_hits") or [])
    terms.extend(row.get("repo_matches") or [])
    author_name = str(row.get("author_name") or "").strip()
    if author_name:
        terms.append(author_name)
    terms.extend(_extract_terms(str(row.get("title") or ""), limit=4))
    terms.extend(_extract_terms(str(row.get("summary") or ""), limit=6))
    return ", ".join(_dedupe_preserve_order(terms)[:6])


def _expand_track_tokens(track: Dict[str, Any]) -> set[str]:
    values: List[str] = []
    values.extend(track.get("keywords") or [])
    values.extend(track.get("methods") or [])
    return set(_dedupe_preserve_order(_expand_terms(values)))


def _expand_signal_tokens(row: Dict[str, Any]) -> set[str]:
    values: List[str] = []
    values.extend(row.get("keyword_hits") or [])
    values.extend(row.get("repo_matches") or [])
    values.extend(_extract_terms(str(row.get("title") or ""), limit=8))
    values.extend(_extract_terms(str(row.get("summary") or ""), limit=12))
    return set(_dedupe_preserve_order(_expand_terms(values)))


def _expand_terms(values: List[str]) -> List[str]:
    expanded: List[str] = []
    for value in values:
        cleaned = str(value or "").strip().lower()
        if not cleaned:
            continue
        expanded.append(cleaned)
        expanded.extend(_extract_terms(cleaned, limit=8))
    return expanded


def _extract_terms(text: str, *, limit: int) -> List[str]:
    terms: List[str] = []
    for token in re.findall(r"[a-zA-Z0-9_./+-]+", str(text or "").lower()):
        normalized = token.strip("./+-_")
        if len(normalized) < 3:
            continue
        if normalized in _SIGNAL_STOPWORDS:
            continue
        terms.append(normalized)
        if len(terms) >= limit:
            break
    return terms


def _dedupe_preserve_order(values: List[str]) -> List[str]:
    deduped: List[str] = []
    seen = set()
    for value in values:
        cleaned = str(value or "").strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
    return deduped
