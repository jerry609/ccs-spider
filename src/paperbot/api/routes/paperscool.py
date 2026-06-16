from __future__ import annotations

import copy
import os
import re
import time
from threading import Thread
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from paperbot.api.streaming import StreamEvent, sse_response
from paperbot.application.services.candidate_curation import (
    curate_search_result,
    ingest_curated_report,
)
from paperbot.application.services.candidate_search import resolve_existing_canonical_paper_id
from paperbot.application.services.daily_push_service import DailyPushService
from paperbot.application.services.llm_service import get_llm_service
from paperbot.application.services.paper_search_service import PaperSearchService
from paperbot.application.services.wiki_concept_service import WikiConceptService
from paperbot.application.services.workflow_query_grounder import WorkflowQueryGrounder
from paperbot.application.workflows.analysis.paper_judge import PaperJudge
from paperbot.application.workflows.dailypaper import (
    DailyPaperReporter,
    render_daily_paper_markdown,
    normalize_output_formats,
    persist_judge_scores_to_registry,
    select_judge_candidates,
)
from paperbot.application.workflows.unified_topic_search import (
    make_default_search_service,
    run_unified_topic_search,
)
from paperbot.infrastructure.services.document_indexing_service import DocumentIndexingService
from paperbot.infrastructure.stores.paper_store import PaperStore
from paperbot.infrastructure.stores.pipeline_session_store import PipelineSessionStore
from paperbot.infrastructure.stores.research_store import SqlAlchemyResearchStore
from paperbot.infrastructure.stores.wiki_concept_store import WikiConceptStore
from paperbot.infrastructure.stores.workflow_metric_store import WorkflowMetricStore
from paperbot.utils.user_identity import optional_user_identity
from paperbot.utils.text_processing import extract_github_url

router = APIRouter()
_paper_search_service: Optional[PaperSearchService] = None
_pipeline_session_store: Optional[PipelineSessionStore] = None
_workflow_metric_store: Optional[WorkflowMetricStore] = None
_workflow_query_grounder: Optional[WorkflowQueryGrounder] = None


def _get_pipeline_session_store() -> PipelineSessionStore:
    global _pipeline_session_store
    if _pipeline_session_store is None:
        _pipeline_session_store = PipelineSessionStore()
    return _pipeline_session_store


# Test compatibility hook: unit tests can monkeypatch this to inject a fake workflow.
PapersCoolTopicSearchWorkflow = None

_ALLOWED_REPORT_BASE = os.path.abspath("./reports")


def _sanitize_output_dir(raw: str) -> str:
    """Prevent path traversal — resolve and ensure output stays under ./reports/."""
    resolved = os.path.abspath(raw)
    if not resolved.startswith(_ALLOWED_REPORT_BASE):
        return os.path.join(_ALLOWED_REPORT_BASE, "dailypaper")
    return resolved


_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def _validate_email_list(emails: List[str]) -> List[str]:
    """Validate and sanitize email list — reject header injection attempts."""
    cleaned: List[str] = []
    for e in emails:
        addr = (e or "").strip()
        if not addr:
            continue
        if "\n" in addr or "\r" in addr:
            continue
        if _EMAIL_RE.match(addr):
            cleaned.append(addr)
    return cleaned


def _get_paper_search_service() -> PaperSearchService:
    global _paper_search_service
    if _paper_search_service is None:
        _paper_search_service = make_default_search_service(registry=PaperStore())
    return _paper_search_service


def _get_workflow_metric_store() -> WorkflowMetricStore:
    global _workflow_metric_store
    if _workflow_metric_store is None:
        _workflow_metric_store = WorkflowMetricStore()
    return _workflow_metric_store


def _get_workflow_query_grounder() -> WorkflowQueryGrounder:
    global _workflow_query_grounder
    if _workflow_query_grounder is None:
        _workflow_query_grounder = WorkflowQueryGrounder(WikiConceptService(WikiConceptStore()))
    return _workflow_query_grounder


def _count_report_claims_and_evidence(report: Dict[str, Any]) -> tuple[int, int]:
    claims = 0
    evidences = 0
    for query in report.get("queries") or []:
        for item in query.get("top_items") or []:
            claims += 1
            if item.get("url") or item.get("pdf_url") or item.get("external_url"):
                evidences += 1
            judge = item.get("judge")
            if isinstance(judge, dict):
                eq = judge.get("evidence_quotes")
                if isinstance(eq, list) and eq:
                    evidences += len(eq)
    return claims, evidences


def _iter_report_item_refs(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for query in report.get("queries") or []:
        for item in query.get("top_items") or []:
            if not isinstance(item, dict):
                continue
            key = (str(item.get("url") or ""), str(item.get("title") or ""))
            if key in seen:
                continue
            seen.add(key)
            rows.append(item)
    for item in report.get("global_top") or []:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("url") or ""), str(item.get("title") or ""))
        if key in seen:
            continue
        seen.add(key)
        rows.append(item)
    return rows


def _build_document_indexing_service() -> DocumentIndexingService:
    paper_store = PaperStore()
    return DocumentIndexingService(paper_store=paper_store)


def _annotate_report_with_canonical_ids(
    report: Dict[str, Any], *, paper_store: PaperStore
) -> List[int]:
    canonical_ids: List[int] = []
    seen: set[int] = set()
    for item in _iter_report_item_refs(report):
        canonical_id = resolve_existing_canonical_paper_id(item, registry=paper_store)
        if canonical_id is None or canonical_id <= 0:
            continue
        item["canonical_id"] = canonical_id
        item["canonical_paper_id"] = canonical_id
        if canonical_id in seen:
            continue
        seen.add(canonical_id)
        canonical_ids.append(canonical_id)
    return canonical_ids


def _process_document_index_jobs(limit: int) -> Dict[str, int]:
    service = _build_document_indexing_service()
    try:
        return service.process_pending_jobs(limit=max(1, int(limit)))
    finally:
        service.close()


def _process_document_index_jobs_async(limit: int) -> None:
    try:
        _process_document_index_jobs(limit)
    except Exception:
        return


def _schedule_document_indexing_for_report(
    report: Dict[str, Any], *, trigger_source: str
) -> Dict[str, Any]:
    service = _build_document_indexing_service()
    try:
        paper_ids = _annotate_report_with_canonical_ids(report, paper_store=service.paper_store)
        summary: Dict[str, Any] = {
            "trigger_source": str(trigger_source or "manual"),
            "total": len(paper_ids),
            "queued": 0,
            "skipped": 0,
            "paper_ids": paper_ids,
            "async_processing": False,
        }
        if not paper_ids:
            report["document_index_ingest"] = summary
            return summary

        enqueue_summary = service.enqueue_papers(
            paper_ids=paper_ids,
            trigger_source=str(trigger_source or "manual"),
        )
        summary.update(enqueue_summary)
        summary["async_processing"] = bool(enqueue_summary.get("queued"))
        report["document_index_ingest"] = summary
    finally:
        service.close()

    if summary.get("queued") and _env_flag("PAPERBOT_DOCUMENT_INDEX_ASYNC", default=True):
        Thread(
            target=_process_document_index_jobs_async,
            args=(int(summary["queued"]),),
            daemon=True,
        ).start()
    return summary


async def _run_topic_search(
    *,
    user_id: Optional[str],
    queries: List[str],
    sources: List[str],
    branches: List[str],
    top_k_per_query: int,
    show_per_branch: int,
    min_score: float,
) -> Dict[str, Any]:
    return await run_unified_topic_search(
        queries=queries,
        user_id=optional_user_identity(user_id),
        sources=sources,
        branches=branches,
        top_k_per_query=top_k_per_query,
        show_per_branch=show_per_branch,
        min_score=min_score,
        search_service=_get_paper_search_service(),
        query_grounder=_get_workflow_query_grounder(),
        persist=False,
    )


class PapersCoolSearchRequest(BaseModel):
    user_id: Optional[str] = None
    queries: List[str] = Field(default_factory=list)
    sources: List[str] = Field(default_factory=lambda: ["papers_cool"])
    branches: List[str] = Field(default_factory=lambda: ["arxiv", "venue"])
    top_k_per_query: int = Field(5, ge=1, le=50)
    show_per_branch: int = Field(25, ge=1, le=200)
    min_score: float = Field(0.0, ge=0.0, description="Drop papers scoring below this threshold")


class PapersCoolSearchResponse(BaseModel):
    source: str
    fetched_at: str
    sources: List[str]
    queries: List[Dict[str, Any]]
    items: List[Dict[str, Any]]
    summary: Dict[str, Any]


class PapersCoolCurateRequest(BaseModel):
    search_result: Dict[str, Any]
    title: str = "DailyPaper Digest"
    top_n: int = Field(10, ge=1, le=200)
    enable_llm_analysis: bool = False
    llm_features: List[str] = Field(default_factory=lambda: ["summary"])
    enable_judge: bool = False
    judge_runs: int = Field(1, ge=1, le=5)
    judge_max_items_per_query: int = Field(5, ge=1, le=200)
    judge_token_budget: int = Field(0, ge=0, le=2_000_000)


class PapersCoolCurateResponse(BaseModel):
    report: Dict[str, Any]
    markdown: str


class DailyPaperRequest(BaseModel):
    user_id: Optional[str] = None
    queries: List[str] = Field(default_factory=list)
    sources: List[str] = Field(default_factory=lambda: ["papers_cool"])
    branches: List[str] = Field(default_factory=lambda: ["arxiv", "venue"])
    top_k_per_query: int = Field(5, ge=1, le=50)
    show_per_branch: int = Field(25, ge=1, le=200)
    min_score: float = Field(0.0, ge=0.0, description="Drop papers scoring below this threshold")
    title: str = "DailyPaper Digest"
    top_n: int = Field(10, ge=1, le=200)
    formats: List[str] = Field(default_factory=lambda: ["both"])
    save: bool = False
    output_dir: str = Field(
        "./reports/dailypaper", description="Relative path under project root for saving reports"
    )
    enable_llm_analysis: bool = False
    llm_features: List[str] = Field(default_factory=lambda: ["summary"])
    enable_judge: bool = False
    judge_runs: int = Field(1, ge=1, le=5)
    judge_max_items_per_query: int = Field(5, ge=1, le=200)
    judge_token_budget: int = Field(0, ge=0, le=2_000_000)
    notify: bool = False
    notify_channels: List[str] = Field(default_factory=list)
    notify_email_to: List[str] = Field(default_factory=list)
    session_id: Optional[str] = Field(
        default=None, description="Resume token for long-running pipeline"
    )
    resume: bool = Field(False, description="Resume from latest persisted checkpoint")
    require_approval: bool = Field(
        False,
        description="Pause before registry ingest and require manual approve/reject",
    )


class DailyPaperResponse(BaseModel):
    report: Dict[str, Any]
    markdown: str
    markdown_path: Optional[str] = None
    json_path: Optional[str] = None
    notify_result: Optional[Dict[str, Any]] = None


class PipelineSessionResponse(BaseModel):
    session: Dict[str, Any]


class ApprovalQueueResponse(BaseModel):
    items: List[Dict[str, Any]]


class ApprovalDecisionRequest(BaseModel):
    reason: str = ""


class PapersCoolAnalyzeRequest(BaseModel):
    report: Dict[str, Any]
    run_judge: bool = False
    run_trends: bool = False
    run_insight: bool = False
    judge_runs: int = Field(1, ge=1, le=5)
    judge_max_items_per_query: int = Field(5, ge=1, le=200)
    judge_token_budget: int = Field(0, ge=0, le=2_000_000)
    trend_max_items_per_query: int = Field(3, ge=1, le=20)


class PapersCoolReposRequest(BaseModel):
    report: Optional[Dict[str, Any]] = None
    papers: List[Dict[str, Any]] = Field(default_factory=list)
    max_items: int = Field(100, ge=1, le=1000)
    include_github_api: bool = True
    persist: bool = False


class PapersCoolReposResponse(BaseModel):
    total_candidates: int
    matched_repos: int
    github_api_used: bool
    repos: List[Dict[str, Any]]
    persist_summary: Optional[Dict[str, int]] = None


class PapersCoolIngestRequest(BaseModel):
    report: Dict[str, Any]
    persist_judge_scores: bool = False


class PapersCoolIngestResponse(BaseModel):
    report: Dict[str, Any]
    markdown: str
    registry_ingest: Dict[str, Any]
    judge_registry_ingest: Optional[Dict[str, Any]] = None
    document_index_ingest: Optional[Dict[str, Any]] = None


@router.post("/research/paperscool/search", response_model=PapersCoolSearchResponse)
async def topic_search(req: PapersCoolSearchRequest):
    cleaned_queries = [q.strip() for q in req.queries if (q or "").strip()]
    if not cleaned_queries:
        raise HTTPException(status_code=400, detail="queries is required")

    try:
        result = await _run_topic_search(
            user_id=req.user_id,
            queries=cleaned_queries,
            sources=req.sources,
            branches=req.branches,
            top_k_per_query=req.top_k_per_query,
            show_per_branch=req.show_per_branch,
            min_score=req.min_score,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"topic search failed: {exc}") from exc
    return PapersCoolSearchResponse(**result)


@router.post("/research/paperscool/curate", response_model=PapersCoolCurateResponse)
async def curate_topic_search(req: PapersCoolCurateRequest):
    try:
        curated = await curate_search_result(
            search_result=req.search_result,
            title=req.title,
            top_n=req.top_n,
            enable_llm_analysis=req.enable_llm_analysis,
            llm_features=req.llm_features,
            enable_judge=req.enable_judge,
            judge_runs=req.judge_runs,
            judge_max_items_per_query=req.judge_max_items_per_query,
            judge_token_budget=req.judge_token_budget,
            llm_service_factory=get_llm_service,
            judge_factory=PaperJudge,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"curate failed: {exc}") from exc

    return PapersCoolCurateResponse(
        report=curated.report,
        markdown=render_daily_paper_markdown(curated.report),
    )


@router.post("/research/paperscool/ingest", response_model=PapersCoolIngestResponse)
async def ingest_curated_topic_report(req: PapersCoolIngestRequest):
    if not isinstance(req.report, dict) or not req.report:
        raise HTTPException(status_code=400, detail="report is required")

    ingested = ingest_curated_report(
        report=req.report,
        persist_judge_scores=bool(req.persist_judge_scores),
    )
    report = ingested.report
    document_index_ingest = _schedule_document_indexing_for_report(
        report,
        trigger_source="paperscool_ingest_api",
    )
    _enqueue_repo_enrichment_async(report)

    return PapersCoolIngestResponse(
        report=report,
        markdown=render_daily_paper_markdown(report),
        registry_ingest=(
            dict(report.get("registry_ingest") or {})
            if isinstance(report.get("registry_ingest"), dict)
            else {}
        ),
        judge_registry_ingest=(
            dict(report.get("judge_registry_ingest") or {})
            if isinstance(report.get("judge_registry_ingest"), dict)
            else None
        ),
        document_index_ingest=document_index_ingest,
    )


async def _dailypaper_stream(req: DailyPaperRequest):
    """SSE generator for the full DailyPaper pipeline."""
    cleaned_queries = [q.strip() for q in req.queries if (q or "").strip()]
    started = time.perf_counter()
    phase_ms: Dict[str, float] = {}
    phase_start = started
    metric_store = _get_workflow_metric_store()

    session = _get_pipeline_session_store().start_session(
        workflow="paperscool_daily",
        payload=req.model_dump(),
        session_id=req.session_id,
        resume=req.resume,
    )
    session_id = str(session.get("session_id") or "")
    session_state: Dict[str, Any] = session.get("state") if req.resume else {}

    yield StreamEvent(
        type="status",
        data={
            "phase": "session",
            "session_id": session_id,
            "resume": bool(req.resume),
            "checkpoint": session.get("checkpoint") or "init",
        },
    )

    if (
        req.resume
        and session.get("status") == "completed"
        and isinstance(session.get("result"), dict)
    ):
        cached_result = dict(session.get("result") or {})
        payload = {
            "report": cached_result.get("report") or {},
            "markdown": cached_result.get("markdown") or "",
            "markdown_path": cached_result.get("markdown_path"),
            "json_path": cached_result.get("json_path"),
            "notify_result": cached_result.get("notify_result"),
            "session_id": session_id,
            "resumed": True,
        }
        yield StreamEvent(type="result", data=payload)
        return

    if (
        req.resume
        and session.get("status") == "pending_approval"
        and isinstance(session.get("result"), dict)
    ):
        cached_result = dict(session.get("result") or {})
        payload = {
            "report": cached_result.get("report") or {},
            "markdown": cached_result.get("markdown") or "",
            "markdown_path": cached_result.get("markdown_path"),
            "json_path": cached_result.get("json_path"),
            "notify_result": cached_result.get("notify_result"),
            "session_id": session_id,
            "resumed": True,
            "approval_status": "pending_approval",
        }
        yield StreamEvent(
            type="approval_required",
            data={"phase": "approval", "session_id": session_id, "status": "pending_approval"},
        )
        yield StreamEvent(type="result", data=payload)
        return

    # Phase 1 — Search
    effective_top_k = max(int(req.top_k_per_query), int(req.top_n), 1)
    if req.resume and isinstance(session_state.get("search_result"), dict):
        search_result = dict(session_state.get("search_result") or {})
        yield StreamEvent(
            type="progress",
            data={"phase": "search", "message": "Resumed search result from checkpoint"},
        )
    else:
        yield StreamEvent(
            type="progress", data={"phase": "search", "message": "Searching papers..."}
        )
        search_result = await _run_topic_search(
            user_id=req.user_id,
            queries=cleaned_queries,
            sources=req.sources,
            branches=req.branches,
            top_k_per_query=effective_top_k,
            show_per_branch=req.show_per_branch,
            min_score=req.min_score,
        )
        _get_pipeline_session_store().save_checkpoint(
            session_id=session_id,
            checkpoint="search_done",
            state={"search_result": search_result},
        )

    summary = search_result.get("summary") or {}
    yield StreamEvent(
        type="search_done",
        data={
            "items_count": len(search_result.get("items") or []),
            "queries_count": len(search_result.get("queries") or []),
            "unique_items": int(summary.get("unique_items") or 0),
            "session_id": session_id,
        },
    )
    phase_ms["search"] = round((time.perf_counter() - phase_start) * 1000.0, 2)
    phase_start = time.perf_counter()

    # Phase 2 — Curate Report
    if req.resume and isinstance(session_state.get("report"), dict):
        report = dict(session_state.get("report") or {})
        yield StreamEvent(
            type="progress",
            data={"phase": "curate", "message": "Resumed curated report from checkpoint"},
        )
        phase_ms["build"] = 0.0
        phase_ms["enrich"] = 0.0
    else:
        yield StreamEvent(
            type="progress",
            data={"phase": "curate", "message": "Curating report..."},
        )
        curated = await curate_search_result(
            search_result=search_result,
            title=req.title,
            top_n=req.top_n,
            enable_llm_analysis=req.enable_llm_analysis,
            llm_features=req.llm_features,
            enable_judge=req.enable_judge,
            judge_runs=req.judge_runs,
            judge_max_items_per_query=req.judge_max_items_per_query,
            judge_token_budget=req.judge_token_budget,
            llm_service_factory=get_llm_service,
            judge_factory=PaperJudge,
        )
        report = curated.report
        _get_pipeline_session_store().save_checkpoint(
            session_id=session_id,
            checkpoint="enriched",
            state={"search_result": search_result, "report": report},
        )
        phase_ms["build"] = curated.build_ms
        phase_ms["enrich"] = curated.enrich_ms

        for event in curated.events:
            payload = dict(event.data)
            if event.type == "report_built":
                payload["session_id"] = session_id
            yield StreamEvent(type=event.type, data=payload)

    phase_start = time.perf_counter()

    if req.require_approval:
        preview_markdown = render_daily_paper_markdown(report)
        claims, evidences = _count_report_claims_and_evidence(report)
        pending_payload = {
            "report": report,
            "markdown": preview_markdown,
            "markdown_path": None,
            "json_path": None,
            "notify_result": None,
            "session_id": session_id,
            "resumed": False,
            "approval_status": "pending_approval",
        }
        _get_pipeline_session_store().update_status(
            session_id=session_id,
            status="pending_approval",
            checkpoint="approval_pending",
            state_patch={"search_result": search_result, "report": report},
            result=pending_payload,
        )
        yield StreamEvent(
            type="approval_required",
            data={
                "phase": "approval",
                "session_id": session_id,
                "status": "pending_approval",
            },
        )
        yield StreamEvent(type="result", data=pending_payload)
        metric_store.record_metric(
            workflow="paperscool_daily",
            stage="approval_pending",
            status="pending_approval",
            claim_count=claims,
            evidence_count=evidences,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            detail={
                "session_id": session_id,
                "phase_ms": phase_ms,
                "resume": bool(req.resume),
            },
        )
        return

    # Phase 5 — Persist + Notify
    yield StreamEvent(type="progress", data={"phase": "save", "message": "Saving to registry..."})
    report = ingest_curated_report(
        report=report,
        persist_judge_scores=bool(req.enable_judge),
    ).report
    _schedule_document_indexing_for_report(
        report,
        trigger_source="paperscool_daily_stream",
    )
    _enqueue_repo_enrichment_async(report)

    markdown = render_daily_paper_markdown(report)

    markdown_path = None
    json_path = None
    notify_result: Optional[Dict[str, Any]] = None
    if req.save:
        reporter = DailyPaperReporter(output_dir=_sanitize_output_dir(req.output_dir))
        artifacts = reporter.write(
            report=report,
            markdown=markdown,
            formats=normalize_output_formats(req.formats),
            slug=req.title,
        )
        markdown_path = artifacts.markdown_path
        json_path = artifacts.json_path

    if req.notify:
        yield StreamEvent(
            type="progress", data={"phase": "notify", "message": "Sending notifications..."}
        )
        notify_service = DailyPushService.from_env()
        notify_result = notify_service.push_dailypaper(
            report=report,
            markdown=markdown,
            markdown_path=markdown_path,
            json_path=json_path,
            channels_override=req.notify_channels or None,
            email_to_override=_validate_email_list(req.notify_email_to) or None,
        )

    phase_ms["persist"] = round((time.perf_counter() - phase_start) * 1000.0, 2)

    result_payload = {
        "report": report,
        "markdown": markdown,
        "markdown_path": markdown_path,
        "json_path": json_path,
        "notify_result": notify_result,
        "session_id": session_id,
        "resumed": False,
        "approval_status": "approved",
    }
    _get_pipeline_session_store().save_result(
        session_id=session_id, result=result_payload, status="completed"
    )

    claims, evidences = _count_report_claims_and_evidence(report)
    metric_store.record_metric(
        workflow="paperscool_daily",
        stage="result",
        status="completed",
        claim_count=claims,
        evidence_count=evidences,
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
        detail={
            "session_id": session_id,
            "phase_ms": phase_ms,
            "enable_judge": bool(req.enable_judge),
            "enable_llm_analysis": bool(req.enable_llm_analysis),
        },
    )

    yield StreamEvent(type="result", data=result_payload)


@router.post("/research/paperscool/daily")
async def generate_daily_report(req: DailyPaperRequest):
    cleaned_queries = [q.strip() for q in req.queries if (q or "").strip()]
    if not cleaned_queries:
        raise HTTPException(status_code=400, detail="queries is required")

    started = time.perf_counter()
    metric_store = _get_workflow_metric_store()

    # Fast sync path when no long-running step is requested — avoids SSE overhead
    if (
        not req.enable_llm_analysis
        and not req.enable_judge
        and not req.require_approval
        and not req.resume
        and not req.session_id
    ):
        try:
            payload = await _sync_daily_report(req, cleaned_queries)
            report = payload.report if isinstance(payload, DailyPaperResponse) else {}
            claims, evidences = _count_report_claims_and_evidence(report)
            metric_store.record_metric(
                workflow="paperscool_daily",
                stage="sync_result",
                status="completed",
                claim_count=claims,
                evidence_count=evidences,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                detail={"mode": "sync"},
            )
            return payload
        except Exception as exc:
            metric_store.record_metric(
                workflow="paperscool_daily",
                stage="sync_result",
                status="failed",
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                detail={"mode": "sync", "error": str(exc)},
            )
            raise

    # SSE streaming path for long-running operations
    return sse_response(_dailypaper_stream(req), workflow="paperscool_daily")


@router.get("/research/paperscool/sessions/{session_id}", response_model=PipelineSessionResponse)
async def get_daily_session(session_id: str):
    session = _get_pipeline_session_store().get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return PipelineSessionResponse(session=session)


@router.get("/research/paperscool/approvals", response_model=ApprovalQueueResponse)
async def list_pending_approvals(limit: int = 20):
    rows = _get_pipeline_session_store().list_sessions(
        workflow="paperscool_daily",
        status="pending_approval",
        limit=max(1, min(int(limit), 200)),
    )
    items: List[Dict[str, Any]] = []
    for row in rows:
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        report = result.get("report") if isinstance(result.get("report"), dict) else {}
        stats = report.get("stats") if isinstance(report.get("stats"), dict) else {}
        items.append(
            {
                "session_id": row.get("session_id"),
                "status": row.get("status"),
                "checkpoint": row.get("checkpoint"),
                "updated_at": row.get("updated_at"),
                "title": report.get("title") or "DailyPaper Digest",
                "query_count": int(stats.get("query_count") or 0),
                "unique_items": int(stats.get("unique_items") or 0),
            }
        )
    return ApprovalQueueResponse(items=items)


def _finalize_approved_session(session: Dict[str, Any]) -> Dict[str, Any]:
    payload = session.get("payload") if isinstance(session.get("payload"), dict) else {}
    state = session.get("state") if isinstance(session.get("state"), dict) else {}
    result = session.get("result") if isinstance(session.get("result"), dict) else {}

    report = state.get("report") if isinstance(state.get("report"), dict) else {}
    if not report:
        report = result.get("report") if isinstance(result.get("report"), dict) else {}
    if not report:
        raise HTTPException(status_code=400, detail="session has no report to approve")

    report = ingest_curated_report(
        report=report,
        persist_judge_scores=bool(payload.get("enable_judge")),
    ).report
    _schedule_document_indexing_for_report(
        report,
        trigger_source="paperscool_daily_approval",
    )
    _enqueue_repo_enrichment_async(report)

    markdown = render_daily_paper_markdown(report)
    markdown_path = None
    json_path = None
    notify_result: Optional[Dict[str, Any]] = None

    if bool(payload.get("save")):
        reporter = DailyPaperReporter(
            output_dir=_sanitize_output_dir(
                str(payload.get("output_dir") or "./reports/dailypaper")
            )
        )
        artifacts = reporter.write(
            report=report,
            markdown=markdown,
            formats=normalize_output_formats(payload.get("formats") or ["both"]),
            slug=payload.get("title") or report.get("title") or "DailyPaper Digest",
        )
        markdown_path = artifacts.markdown_path
        json_path = artifacts.json_path

    if bool(payload.get("notify")):
        notify_service = DailyPushService.from_env()
        notify_result = notify_service.push_dailypaper(
            report=report,
            markdown=markdown,
            markdown_path=markdown_path,
            json_path=json_path,
            channels_override=payload.get("notify_channels") or None,
            email_to_override=_validate_email_list(payload.get("notify_email_to") or []) or None,
        )

    return {
        "report": report,
        "markdown": markdown,
        "markdown_path": markdown_path,
        "json_path": json_path,
        "notify_result": notify_result,
        "session_id": session.get("session_id"),
        "resumed": False,
        "approval_status": "approved",
    }


@router.post(
    "/research/paperscool/sessions/{session_id}/approve", response_model=PipelineSessionResponse
)
async def approve_daily_session(session_id: str):
    session = _get_pipeline_session_store().get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    if session.get("status") != "pending_approval":
        raise HTTPException(status_code=409, detail="session is not pending approval")

    final_payload = _finalize_approved_session(session)
    claims, evidences = _count_report_claims_and_evidence(final_payload.get("report") or {})
    _get_pipeline_session_store().update_status(
        session_id=session_id,
        status="completed",
        checkpoint="result",
        state_patch={"approved_at": True},
        result=final_payload,
    )
    _get_workflow_metric_store().record_metric(
        workflow="paperscool_daily",
        stage="approval_finalize",
        status="completed",
        claim_count=claims,
        evidence_count=evidences,
        detail={"session_id": session_id, "mode": "approval"},
    )
    updated = _get_pipeline_session_store().get_session(session_id)
    return PipelineSessionResponse(session=updated or {})


@router.post(
    "/research/paperscool/sessions/{session_id}/reject", response_model=PipelineSessionResponse
)
async def reject_daily_session(session_id: str, req: ApprovalDecisionRequest):
    session = _get_pipeline_session_store().get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    if session.get("status") != "pending_approval":
        raise HTTPException(status_code=409, detail="session is not pending approval")

    current_result = session.get("result") if isinstance(session.get("result"), dict) else {}
    rejected_result = {
        **current_result,
        "session_id": session_id,
        "approval_status": "rejected",
        "rejected_reason": req.reason or "",
    }

    _get_pipeline_session_store().update_status(
        session_id=session_id,
        status="rejected",
        checkpoint="approval_rejected",
        state_patch={"reject_reason": req.reason or ""},
        result=rejected_result,
    )
    _get_workflow_metric_store().record_metric(
        workflow="paperscool_daily",
        stage="approval_finalize",
        status="rejected",
        detail={"session_id": session_id, "reason": req.reason or ""},
    )
    updated = _get_pipeline_session_store().get_session(session_id)
    return PipelineSessionResponse(session=updated or {})


async def _sync_daily_report(req: DailyPaperRequest, cleaned_queries: List[str]):
    """Original synchronous path for fast requests (no LLM/Judge)."""
    effective_top_k = max(int(req.top_k_per_query), int(req.top_n), 1)
    try:
        search_result = await _run_topic_search(
            user_id=req.user_id,
            queries=cleaned_queries,
            sources=req.sources,
            branches=req.branches,
            top_k_per_query=effective_top_k,
            show_per_branch=req.show_per_branch,
            min_score=req.min_score,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"daily search failed: {exc}") from exc
    curated = await curate_search_result(
        search_result=search_result,
        title=req.title,
        top_n=req.top_n,
        llm_service_factory=get_llm_service,
        judge_factory=PaperJudge,
    )
    report = ingest_curated_report(report=curated.report).report
    _schedule_document_indexing_for_report(
        report,
        trigger_source="paperscool_daily_sync",
    )
    _enqueue_repo_enrichment_async(report)

    markdown = render_daily_paper_markdown(report)

    markdown_path = None
    json_path = None
    notify_result: Optional[Dict[str, Any]] = None
    if req.save:
        reporter = DailyPaperReporter(output_dir=_sanitize_output_dir(req.output_dir))
        artifacts = reporter.write(
            report=report,
            markdown=markdown,
            formats=normalize_output_formats(req.formats),
            slug=req.title,
        )
        markdown_path = artifacts.markdown_path
        json_path = artifacts.json_path

    if req.notify:
        notify_service = DailyPushService.from_env()
        notify_result = notify_service.push_dailypaper(
            report=report,
            markdown=markdown,
            markdown_path=markdown_path,
            json_path=json_path,
            channels_override=req.notify_channels or None,
            email_to_override=_validate_email_list(req.notify_email_to) or None,
        )

    return DailyPaperResponse(
        report=report,
        markdown=markdown,
        markdown_path=markdown_path,
        json_path=json_path,
        notify_result=notify_result,
    )


_GITHUB_REPO_RE = re.compile(r"https?://github\.com/([\w.-]+)/([\w.-]+)", re.IGNORECASE)


def _normalize_github_repo_url(raw_url: str | None) -> Optional[str]:
    if not raw_url:
        return None
    candidate = (raw_url or "").strip()
    if not candidate:
        return None
    if "github.com" not in candidate.lower():
        return None
    if not candidate.startswith("http"):
        candidate = f"https://{candidate}"

    parsed = urlparse(candidate)
    if "github.com" not in (parsed.netloc or "").lower():
        return None

    match = _GITHUB_REPO_RE.search(f"{parsed.scheme}://{parsed.netloc}{parsed.path}")
    if not match:
        return None

    owner, repo = match.group(1), match.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    return f"https://github.com/{owner}/{repo}"


def _extract_repo_url_from_paper(paper: Dict[str, Any]) -> Optional[str]:
    candidates: List[str] = []
    for key in ("github_url", "external_url", "url", "pdf_url"):
        value = paper.get(key)
        if isinstance(value, str) and value:
            candidates.append(value)

    for alt in paper.get("alternative_urls") or []:
        if isinstance(alt, str) and alt:
            candidates.append(alt)

    for candidate in candidates:
        normalized = _normalize_github_repo_url(candidate)
        if normalized:
            return normalized

    text_blob_parts = [
        str(paper.get("title") or ""),
        str(paper.get("snippet") or paper.get("abstract") or ""),
        " ".join(str(k) for k in (paper.get("keywords") or [])),
    ]
    extracted = extract_github_url("\n".join(text_blob_parts))
    return _normalize_github_repo_url(extracted)


def _flatten_report_papers(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for query in report.get("queries") or []:
        query_name = query.get("normalized_query") or query.get("raw_query") or ""
        for item in query.get("top_items") or []:
            row = dict(item)
            row.setdefault("_query", query_name)
            rows.append(row)

    for item in report.get("global_top") or []:
        row = dict(item)
        if "_query" not in row:
            matched = row.get("matched_queries") or []
            row["_query"] = matched[0] if matched else ""
        rows.append(row)

    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in rows:
        key = f"{item.get('url') or ''}|{item.get('title') or ''}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _fetch_github_repo_metadata(repo_url: str, token: Optional[str]) -> Dict[str, Any]:
    normalized = _normalize_github_repo_url(repo_url)
    if not normalized:
        return {"ok": False, "error": "invalid_repo_url"}

    match = _GITHUB_REPO_RE.search(normalized)
    if not match:
        return {"ok": False, "error": "invalid_repo_url"}

    owner, repo = match.group(1), match.group(2)
    api_url = f"https://api.github.com/repos/{owner}/{repo}"

    headers = {"Accept": "application/vnd.github+json", "User-Agent": "PaperBot/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.get(api_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return {
                "ok": False,
                "status": resp.status_code,
                "error": "github_api_error",
                "repo_url": normalized,
            }

        payload = resp.json()
        return {
            "ok": True,
            "status": resp.status_code,
            "repo_url": normalized,
            "full_name": payload.get("full_name") or f"{owner}/{repo}",
            "description": payload.get("description") or "",
            "stars": int(payload.get("stargazers_count") or 0),
            "forks": int(payload.get("forks_count") or 0),
            "open_issues": int(payload.get("open_issues_count") or 0),
            "watchers": int(payload.get("subscribers_count") or payload.get("watchers_count") or 0),
            "language": payload.get("language") or "",
            "license": (payload.get("license") or {}).get("spdx_id") or "",
            "updated_at": payload.get("updated_at"),
            "pushed_at": payload.get("pushed_at"),
            "archived": bool(payload.get("archived")),
            "topics": payload.get("topics") or [],
            "html_url": payload.get("html_url") or normalized,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"github_api_exception: {exc}",
            "repo_url": normalized,
        }


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in {"", "0", "false", "off", "no"}


def _collect_repo_enrichment_rows(
    *,
    papers: List[Dict[str, Any]],
    max_items: int,
    include_github_api: bool,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in papers:
        key = f"{item.get('url') or ''}|{item.get('title') or ''}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    selected = deduped[: max(1, int(max_items))]
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")

    # TODO: GitHub API calls are sequential — switch to concurrent.futures or
    #  async httpx with bounded concurrency to avoid multi-minute requests and
    #  rate-limit exhaustion (60 req/hr unauthenticated, 5000 authenticated).
    repos: List[Dict[str, Any]] = []
    for item in selected:
        repo_url = _extract_repo_url_from_paper(item)
        if not repo_url:
            continue

        row: Dict[str, Any] = {
            "title": item.get("title") or "Untitled",
            "query": item.get("_query") or ", ".join(item.get("matched_queries") or []),
            "paper_url": item.get("url") or item.get("external_url") or "",
            "repo_url": repo_url,
        }
        if include_github_api:
            row["github"] = _fetch_github_repo_metadata(repo_url=repo_url, token=token)
        repos.append(row)

    if include_github_api:
        repos.sort(
            key=lambda row: int(((row.get("github") or {}).get("stars") or -1)),
            reverse=True,
        )

    return selected, repos


def _persist_repo_enrichment_async(report: Dict[str, Any]) -> None:
    try:
        max_items_raw = os.getenv("PAPERBOT_REPO_ENRICH_MAX_ITEMS", "100")
        max_items = max(1, int(max_items_raw))
    except Exception:
        max_items = 100

    include_github_api = _env_flag("PAPERBOT_REPO_ENRICH_INCLUDE_GITHUB_API", default=True)

    try:
        papers = _flatten_report_papers(report)
        if not papers:
            return
        _, repos = _collect_repo_enrichment_rows(
            papers=papers,
            max_items=max_items,
            include_github_api=include_github_api,
        )
        if not repos:
            return
        store = SqlAlchemyResearchStore()
        store.ingest_repo_enrichment_rows(rows=repos, source="paperscool_daily_async")
    except Exception:
        # Async best-effort hook: ignore failures to avoid affecting daily report flow.
        return


def _enqueue_repo_enrichment_async(report: Dict[str, Any]) -> None:
    if not _env_flag("PAPERBOT_REPO_ENRICH_ASYNC", default=True):
        return
    Thread(
        target=_persist_repo_enrichment_async, args=(copy.deepcopy(report),), daemon=True
    ).start()


@router.post("/research/paperscool/repos", response_model=PapersCoolReposResponse)
def enrich_papers_with_repo_data(req: PapersCoolReposRequest):
    papers: List[Dict[str, Any]] = []
    if isinstance(req.report, dict):
        papers.extend(_flatten_report_papers(req.report))
    papers.extend(list(req.papers or []))

    if not papers:
        raise HTTPException(status_code=400, detail="report or papers is required")

    selected, repos = _collect_repo_enrichment_rows(
        papers=papers,
        max_items=req.max_items,
        include_github_api=bool(req.include_github_api),
    )

    persist_summary: Optional[Dict[str, int]] = None
    if req.persist:
        store = SqlAlchemyResearchStore()
        persist_summary = store.ingest_repo_enrichment_rows(
            rows=repos, source="paperscool_repos_api"
        )

    return PapersCoolReposResponse(
        total_candidates=len(selected),
        matched_repos=len(repos),
        github_api_used=bool(req.include_github_api),
        repos=repos,
        persist_summary=persist_summary,
    )


async def _paperscool_analyze_stream(req: PapersCoolAnalyzeRequest):
    started = time.perf_counter()
    metric_store = _get_workflow_metric_store()
    report = copy.deepcopy(req.report)
    llm_service = get_llm_service()

    llm_block: Optional[Dict[str, Any]] = None
    if req.run_trends or req.run_insight:
        llm_block = report.get("llm_analysis")
        if not isinstance(llm_block, dict):
            llm_block = {}

        features = list(llm_block.get("features") or [])
        if req.run_trends and "trends" not in features:
            features.append("trends")
        if req.run_insight and "insight" not in features:
            features.append("insight")

        llm_block["enabled"] = True
        llm_block["features"] = features
        llm_block.setdefault("daily_insight", "")
        if req.run_trends:
            llm_block["query_trends"] = []

    if req.run_trends and llm_block is not None:

        queries = list(report.get("queries") or [])
        trend_total = sum(1 for query in queries if query.get("top_items"))
        trend_done = 0
        yield StreamEvent(
            type="progress",
            data={"phase": "trends", "message": "Starting trend analysis", "total": trend_total},
        )
        for query in queries:
            query_name = query.get("normalized_query") or query.get("raw_query") or ""
            top_items = list(query.get("top_items") or [])[
                : max(1, int(req.trend_max_items_per_query))
            ]
            if not top_items:
                continue

            trend_done += 1
            analysis = llm_service.analyze_trends(topic=query_name, papers=top_items)
            llm_block["query_trends"].append({"query": query_name, "analysis": analysis})

            yield StreamEvent(
                type="trend",
                data={
                    "query": query_name,
                    "analysis": analysis,
                    "done": trend_done,
                    "total": trend_total,
                },
            )

        report["llm_analysis"] = llm_block
        yield StreamEvent(type="trend_done", data={"count": trend_done})

    if req.run_insight and llm_block is not None:
        yield StreamEvent(
            type="progress",
            data={"phase": "insight", "message": "Generating daily insight"},
        )
        daily_insight = llm_service.generate_daily_insight(report)
        llm_block["daily_insight"] = daily_insight
        report["llm_analysis"] = llm_block
        yield StreamEvent(type="insight", data={"analysis": daily_insight})

    if req.run_judge:
        judge = PaperJudge(llm_service=llm_service)
        selection = select_judge_candidates(
            report,
            max_items_per_query=req.judge_max_items_per_query,
            n_runs=req.judge_runs,
            token_budget=req.judge_token_budget,
        )
        selected = list(selection.get("selected") or [])
        recommendation_count = {
            "must_read": 0,
            "worth_reading": 0,
            "skim": 0,
            "skip": 0,
        }

        yield StreamEvent(
            type="progress",
            data={
                "phase": "judge",
                "message": "Starting judge scoring",
                "total": len(selected),
                "budget": selection.get("budget") or {},
            },
        )

        queries = list(report.get("queries") or [])
        for idx, row in enumerate(selected, start=1):
            query_index = int(row.get("query_index") or 0)
            item_index = int(row.get("item_index") or 0)

            if query_index >= len(queries):
                continue

            query = queries[query_index]
            query_name = query.get("normalized_query") or query.get("raw_query") or ""
            top_items = list(query.get("top_items") or [])
            if item_index >= len(top_items):
                continue

            item = top_items[item_index]
            if req.judge_runs > 1:
                judgment = judge.judge_with_calibration(
                    paper=item,
                    query=query_name,
                    n_runs=max(1, int(req.judge_runs)),
                )
            else:
                judgment = judge.judge_single(paper=item, query=query_name)

            j_payload = judgment.to_dict()
            item["judge"] = j_payload
            rec = j_payload.get("recommendation")
            if rec in recommendation_count:
                recommendation_count[rec] += 1

            yield StreamEvent(
                type="judge",
                data={
                    "query": query_name,
                    "title": item.get("title") or "Untitled",
                    "judge": j_payload,
                    "done": idx,
                    "total": len(selected),
                },
            )

        for query in report.get("queries") or []:
            top_items = list(query.get("top_items") or [])
            if not top_items:
                continue
            capped_count = min(len(top_items), max(1, int(req.judge_max_items_per_query)))
            capped = top_items[:capped_count]
            capped.sort(
                key=lambda it: float((it.get("judge") or {}).get("overall") or -1), reverse=True
            )
            query["top_items"] = capped + top_items[capped_count:]

        report["judge"] = {
            "enabled": True,
            "max_items_per_query": int(req.judge_max_items_per_query),
            "n_runs": int(max(1, int(req.judge_runs))),
            "recommendation_count": recommendation_count,
            "budget": selection.get("budget") or {},
        }
        try:
            report["judge_registry_ingest"] = persist_judge_scores_to_registry(report)
        except Exception as exc:
            report["judge_registry_ingest"] = {"error": str(exc)}
        yield StreamEvent(type="judge_done", data=report["judge"])

    markdown = render_daily_paper_markdown(report)
    claims, evidences = _count_report_claims_and_evidence(report)
    metric_store.record_metric(
        workflow="paperscool_analyze",
        stage="result",
        status="completed",
        claim_count=claims,
        evidence_count=evidences,
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
        detail={
            "run_judge": bool(req.run_judge),
            "run_trends": bool(req.run_trends),
            "run_insight": bool(req.run_insight),
        },
    )
    yield StreamEvent(type="result", data={"report": report, "markdown": markdown})


@router.post("/research/paperscool/analyze")
async def analyze_daily_report(req: PapersCoolAnalyzeRequest):
    if not req.run_judge and not req.run_trends and not req.run_insight:
        raise HTTPException(
            status_code=400,
            detail="run_judge or run_trends or run_insight must be true",
        )
    if not isinstance(req.report, dict) or not req.report.get("queries"):
        raise HTTPException(status_code=400, detail="report with queries is required")

    return sse_response(_paperscool_analyze_stream(req), workflow="paperscool_analyze")
