from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, Generator, List, Optional, Sequence

from paperbot.application.prompts import PromptRegistry
from paperbot.application.services.provider_resolver import (
    ProviderResolver,
    RouterBackedProviderResolver,
)
from paperbot.infrastructure.llm.router import ModelRouter
from paperbot.infrastructure.stores.llm_usage_store import LLMUsageStore

logger = logging.getLogger(__name__)


class LLMService:
    """Project-level LLM facade with task routing, prompt templates, and light caching."""

    def __init__(
        self,
        router: Optional[ModelRouter] = None,
        provider_resolver: Optional[ProviderResolver] = None,
        prompt_registry: Optional[PromptRegistry] = None,
        usage_store: Optional[LLMUsageStore] = None,
        *,
        enable_cache: bool = True,
        raise_errors: bool = False,
    ) -> None:
        resolved_router = router or ModelRouter.from_env()
        self._provider_resolver = provider_resolver or RouterBackedProviderResolver(resolved_router)
        self._prompts = prompt_registry or PromptRegistry()
        self._enable_cache = enable_cache
        self._raise_errors = raise_errors
        self._cache: Dict[str, str] = {}
        self._usage_store = usage_store
        if self._usage_store is None:
            try:
                self._usage_store = LLMUsageStore()
            except Exception:
                self._usage_store = None

    def complete(
        self,
        *,
        task_type: str = "default",
        system: str,
        user: str,
        use_cache: bool = True,
        **kwargs,
    ) -> str:
        cache_key = self._cache_key(task_type=task_type, system=system, user=user, kwargs=kwargs)
        if self._enable_cache and use_cache and cache_key in self._cache:
            return self._cache[cache_key]

        try:
            provider = self._provider_resolver.get_provider(task_type)
            result = (provider.invoke_simple(system, user, **kwargs) or "").strip()
            self._record_usage(
                task_type=task_type,
                provider=provider,
                system=system,
                user=user,
                completion=result,
            )
        except Exception as exc:  # pragma: no cover - exercised via fallback tests
            logger.warning("LLM complete failed task_type=%s error=%s", task_type, exc)
            if self._raise_errors:
                raise
            result = ""

        if self._enable_cache and use_cache:
            self._cache[cache_key] = result
        return result

    def stream(
        self,
        *,
        task_type: str = "default",
        system: str,
        user: str,
        **kwargs,
    ) -> Generator[str, None, None]:
        try:
            provider = self._provider_resolver.get_provider(task_type)
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
            chunks: List[str] = []
            for chunk in provider.stream_invoke(messages, **kwargs):
                text = str(chunk or "")
                if text:
                    chunks.append(text)
                    yield text
            self._record_usage(
                task_type=task_type,
                provider=provider,
                system=system,
                user=user,
                completion="".join(chunks),
            )
        except Exception as exc:  # pragma: no cover - stream failures are runtime/network specific
            logger.warning("LLM stream failed task_type=%s error=%s", task_type, exc)
            if self._raise_errors:
                raise
            return

    def summarize_paper(self, title: str, abstract: str) -> str:
        prompt = self._prompts.get("paper_summary")
        return self.complete(
            task_type="summary",
            system=prompt.system,
            user=prompt.user.format(title=title or "", abstract=abstract or ""),
        )

    def analyze_trends(self, *, topic: str, papers: Sequence[Dict[str, Any]]) -> str:
        prompt = self._prompts.get("trend_analysis")
        serialized = _format_papers_for_prompt(papers)
        return self.complete(
            task_type="reasoning",
            system=prompt.system,
            user=prompt.user.format(topic=topic or "", papers=serialized),
        )

    def assess_relevance(self, *, paper: Dict[str, Any], query: str) -> Dict[str, Any]:
        prompt = self._prompts.get("relevance_assess")
        raw = self.complete(
            task_type="extraction",
            system=prompt.system,
            user=prompt.user.format(
                query=query or "",
                title=paper.get("title") or "",
                abstract=paper.get("snippet") or paper.get("abstract") or "",
                keywords=", ".join(paper.get("keywords") or []),
            ),
        )
        parsed = _safe_parse_json(raw)
        if parsed is None:
            return {
                "score": _overlap_relevance_score(query=query, paper=paper),
                "fallback": True,
                "reason": "Fallback score from token overlap (LLM output unavailable).",
            }

        score = int(parsed.get("score") or 0)
        score = max(0, min(score, 100))
        reason = str(parsed.get("reason") or "")
        return {"score": score, "reason": reason}

    def generate_daily_insight(self, report: Dict[str, Any]) -> str:
        prompt = self._prompts.get("daily_insight")
        stats = report.get("stats") or {}
        highlights = []
        for query in report.get("queries") or []:
            q_name = query.get("normalized_query") or query.get("raw_query") or ""
            top_items = query.get("top_items") or []
            top_title = top_items[0].get("title") if top_items else ""
            hit_count = query.get("total_hits", 0)
            highlights.append(f"- {q_name}: hits={hit_count}, top={top_title}")

        return self.complete(
            task_type="reasoning",
            system=prompt.system,
            user=prompt.user.format(
                title=report.get("title") or "DailyPaper Digest",
                date=report.get("date") or "",
                stats=json.dumps(stats, ensure_ascii=False),
                highlights="\n".join(highlights),
            ),
        )

    def extract_structured_card(self, title: str, abstract: str) -> Dict[str, Any]:
        prompt = self._prompts.get("structured_card")
        raw = self.complete(
            task_type="extraction",
            system=prompt.system,
            user=prompt.user.format(title=title or "", abstract=abstract or ""),
        )
        parsed = _safe_parse_json(raw)
        if parsed is None:
            return {"method": "", "dataset": "N/A", "conclusion": "", "limitations": "Not stated"}
        return {
            "method": str(parsed.get("method") or ""),
            "dataset": str(parsed.get("dataset") or "N/A"),
            "conclusion": str(parsed.get("conclusion") or ""),
            "limitations": str(parsed.get("limitations") or "Not stated"),
        }

    def extract_daily_digest_card(self, title: str, abstract: str) -> Dict[str, Any]:
        prompt = self._prompts.get("daily_digest_card")
        raw = self.complete(
            task_type="extraction",
            system=prompt.system,
            user=prompt.user.format(title=title or "", abstract=abstract or ""),
        )
        parsed = _safe_parse_json(raw)
        if parsed is None:
            return {"highlight": "", "method": "", "finding": "", "tags": []}
        tags = parsed.get("tags") or []
        if not isinstance(tags, list):
            tags = [str(tags)]
        return {
            "highlight": str(parsed.get("highlight") or ""),
            "method": str(parsed.get("method") or ""),
            "finding": str(parsed.get("finding") or ""),
            "tags": [str(t) for t in tags[:6]],
        }

    def generate_related_work(self, papers: List[Dict[str, Any]], topic: str) -> str:
        prompt = self._prompts.get("related_work")
        formatted = _format_papers_for_related_work(papers)
        return self.complete(
            task_type="default",
            system=prompt.system,
            user=prompt.user.format(topic=topic or "", papers_formatted=formatted),
            use_cache=False,
        )

    def _cache_key(self, *, task_type: str, system: str, user: str, kwargs: Dict[str, Any]) -> str:
        payload = json.dumps(
            {
                "task_type": task_type,
                "system": system,
                "user": user,
                "kwargs": kwargs,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _record_usage(
        self,
        *,
        task_type: str,
        provider: Any,
        system: str,
        user: str,
        completion: str,
    ) -> None:
        if self._usage_store is None:
            return

        prompt_tokens = _estimate_tokens(system) + _estimate_tokens(user)
        completion_tokens = _estimate_tokens(completion)

        try:
            info = provider.info
            provider_name = str(getattr(info, "provider_name", "unknown") or "unknown")
            model_name = str(getattr(info, "model_name", "") or "")
        except Exception:
            provider_name = "unknown"
            model_name = ""

        cost_usd = _estimate_cost_usd(
            provider_name=provider_name,
            model_name=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

        try:
            self._usage_store.record_usage(
                task_type=task_type,
                provider_name=provider_name,
                model_name=model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                estimated_cost_usd=cost_usd,
                metadata={"estimated": True},
            )
        except Exception:
            return

    def describe_task_provider(self, task_type: str = "default") -> Dict[str, Any]:
        """Expose selected provider metadata for auditing/judge reports."""
        try:
            provider = self._provider_resolver.get_provider(task_type)
            info = provider.info
            return {
                "provider_name": info.provider_name,
                "model_name": info.model_name,
                "cost_tier": info.cost_tier,
            }
        except Exception:
            return {
                "provider_name": "",
                "model_name": "",
                "cost_tier": 0,
            }


_default_llm_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    global _default_llm_service
    if _default_llm_service is None:
        _default_llm_service = LLMService()
    return _default_llm_service


def _format_papers_for_prompt(papers: Sequence[Dict[str, Any]], limit: int = 12) -> str:
    rows: List[str] = []
    for idx, paper in enumerate(list(papers)[: max(1, int(limit))], start=1):
        rows.append(
            "{idx}. {title} | keywords={keywords} | snippet={snippet}".format(
                idx=idx,
                title=paper.get("title") or "Untitled",
                keywords=", ".join(paper.get("keywords") or []),
                snippet=(paper.get("snippet") or paper.get("abstract") or "")[:220],
            )
        )
    return "\n".join(rows)


def _format_papers_for_related_work(papers: Sequence[Dict[str, Any]], limit: int = 20) -> str:
    rows: List[str] = []
    for idx, paper in enumerate(list(papers)[: max(1, int(limit))], start=1):
        authors = paper.get("authors") or []
        author_str = authors[0].split()[-1] if authors else "Unknown"
        year = paper.get("year") or "n.d."
        cite_key = f"[{author_str}{year}]"
        rows.append(
            "{idx}. {cite_key} {title}\n   Authors: {authors}\n   Abstract: {abstract}".format(
                idx=idx,
                cite_key=cite_key,
                title=paper.get("title") or "Untitled",
                authors=", ".join(authors[:5]) or "Unknown",
                abstract=(paper.get("snippet") or paper.get("abstract") or "")[:300],
            )
        )
    return "\n\n".join(rows)


def _safe_parse_json(raw: str) -> Optional[Dict[str, Any]]:
    # 委托给统一的鲁棒解析器（markdown 围栏 / json_repair / 多候选策略），
    # 同时保留原契约：成功返回 dict，失败或非 dict 返回 None。
    from paperbot.utils.json_parser import JSONParseError, parse_json

    text = (raw or "").strip()
    if not text:
        return None
    try:
        obj = parse_json(text)
    except JSONParseError:
        return None
    return obj if isinstance(obj, dict) else None


def _overlap_relevance_score(*, query: str, paper: Dict[str, Any]) -> int:
    query_tokens = _tokenize(query)
    haystack = " ".join(
        [
            paper.get("title") or "",
            paper.get("snippet") or paper.get("abstract") or "",
            " ".join(paper.get("keywords") or []),
        ]
    ).lower()
    if not query_tokens:
        return 0

    hit = 0
    for token in query_tokens:
        if token in haystack:
            hit += 1
    return int((hit / len(query_tokens)) * 100)


def _tokenize(text: str) -> List[str]:
    lowered = (text or "").strip().lower()
    tokens = [token for token in lowered.replace("_", " ").split() if token]
    dedup: List[str] = []
    seen = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        dedup.append(token)
    return dedup


def _estimate_tokens(text: str) -> int:
    raw = str(text or "")
    if not raw:
        return 0
    # Lightweight heuristic: ~4 chars/token for mixed English+code prompts.
    return max(1, int(len(raw) / 4))


def _estimate_cost_usd(
    *, provider_name: str, model_name: str, prompt_tokens: int, completion_tokens: int
) -> float:
    provider = (provider_name or "").lower()
    model = (model_name or "").lower()
    model_alias = model.split("/", 1)[-1] if "/" in model else model

    in_price = 0.0
    out_price = 0.0

    if "gpt-4o-mini" in model_alias:
        in_price, out_price = 0.15, 0.60
    elif "gpt-4o" in model_alias:
        in_price, out_price = 2.50, 10.00
    elif "claude-3-5-sonnet" in model_alias:
        in_price, out_price = 3.00, 15.00
    elif "deepseek" in model_alias or provider == "deepseek":
        in_price, out_price = 0.55, 2.19
    elif provider == "ollama":
        in_price, out_price = 0.0, 0.0

    return ((prompt_tokens * in_price) + (completion_tokens * out_price)) / 1_000_000
