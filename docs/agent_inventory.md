# Agent Inventory and Boundary Map

> Scope: current state on harvest-aligned baseline (`origin/master` + branch work)
> Date: 2026-02-11

## 1) Inventory Summary

- API entrypoints that directly invoke agents: **5**
  - `analyze`, `review`, `track`, `research` (scholar identity resolve), `gen_code`
- Agent classes:
  - `src/paperbot/agents`: **13** business agents (excluding `BaseAgent`)
  - `src/paperbot/repro/agents`: **4** business agents (excluding `BaseAgent`)
- Primary orchestration layers:
  - `ScholarPipeline` -> `ScholarWorkflowCoordinator` (scholar analysis chain)
  - `ReproAgent` -> `Orchestrator` (Paper2Code chain)

## 2) Entry Boundary Matrix

| Entrypoint | Owner | Agent Path | Input | Output | SLA (target) | Current Fallback | Boundary Decision |
|---|---|---|---|---|---|---|---|
| `POST /api/analyze` (`src/paperbot/api/routes/analyze.py`) | Research API | `ResearchAgent.analyze_paper` | title, abstract | SSE progress + paper analysis result | P50 < 15s, P95 < 45s | Emits SSE `error` event | **Must use agent** (unstructured paper reasoning) |
| `POST /api/review` (`src/paperbot/api/routes/review.py`) | Research API | `ReviewerAgent.review` | title, abstract | SSE progress + review decision payload | P50 < 20s, P95 < 60s | Emits SSE `error` event | **Must use agent** (deep critique + recommendation) |
| `GET /api/track` (`src/paperbot/api/routes/track.py`) | Scholar Tracking | `PaperTrackerAgent.track_scholar` + `ScholarPipeline.analyze_paper` | scholar id/name, flags | SSE run status + per-paper analysis | P50 < 60s for <=5 papers | Partial result or SSE `error`; closes S2 client in `finally` | **Must use agent** (multi-stage pipeline + external APIs) |
| `POST /api/research/scholar/network|trends` identity resolve (`src/paperbot/api/routes/research.py`) | Research API | `ScholarProfileAgent` (lookup only) | scholar_id or scholar_name | resolved scholar id then service stats | P50 < 2s lookup + downstream client time | HTTP 4xx/5xx | **Service preferred** (simple profile lookup; agent optional) |
| `POST /api/gen-code` (`src/paperbot/api/routes/gen_code.py`) | Paper2Code | `ReproAgent.reproduce_from_paper` -> `Orchestrator` (`Planning/Coding/Verification/Debugging` agents) | title, abstract, method section, flags | SSE progress + generated files/verification status | P50 < 180s (model/executor dependent) | Emits SSE `error`; returns partial generation when possible | **Must use agent** (planning + coding + repair loop) |

## 3) Workflow-Level Agent Entry Points

| Workflow Entry | Owner | Agent Chain | Notes |
|---|---|---|---|
| `ScholarPipeline.analyze_paper` (`src/paperbot/application/workflows/scholar_pipeline.py`) | Scholar Tracking | delegates to `ScholarWorkflowCoordinator` | Stable app-layer boundary; currently thin wrapper |
| `ScholarWorkflowCoordinator.run_paper_pipeline` (`src/paperbot/core/workflow_coordinator.py`) | Scholar Tracking | `ResearchAgent` -> `CodeAnalysisAgent` -> `QualityAgent` -> influence/report | Has Fail-Fast + ScoreShareBus integration |
| `ReproAgent.reproduce_from_paper` (`src/paperbot/repro/repro_agent.py`) | Paper2Code | node pipeline or orchestrator mode | Supports Docker/E2B executor choices |
| `Orchestrator.run` (`src/paperbot/repro/orchestrator.py`) | Paper2Code | `PlanningAgent` -> `CodingAgent` -> `VerificationAgent` -> `DebuggingAgent` loop | Emits event_log envelopes with run/trace IDs |

## 4) Must-Use-Agent vs Service-Preferred Rules

### Must-Use-Agent

Use agents when all of the following are true:
- Task is primarily **unstructured reasoning** (summary/review/planning/repair)
- Requires **multi-step tool-calling** or iterative feedback loops
- Result quality benefits from chain-level context sharing (memory/score bus)
- Latency is acceptable in async/SSE workflows

### Service-Preferred

Use normal services (non-agent) when any of the following dominates:
- Task is deterministic CRUD/query/transform
- Strict low-latency sync API path is required
- Output schema is fixed and does not benefit from long reasoning
- Failure mode should be simple retries, not planner-style adaptation

## 5) Known Gaps (for Issue #45-#47)

- Lifecycle contracts are fragmented (route-level SSE events differ by feature)
- Some agent paths emit `trace_id`, others do not expose consistent envelopes
- Agent invocation style differs between `agents/*` and `repro/agents/*`
- No single `AgentRuntime` interface yet for plan/execute/finalize semantics

## 6) Agent Class Inventory (Business Agents)

`src/paperbot/agents`:
- `CodeAnalysisAgent`
- `ConferenceResearchAgent`
- `DeepResearchAgent`
- `PaperTrackerAgent`
- `QualityAgent`
- `ResearchAgent`
- `ReviewerAgent`
- `ScholarProfileAgent`
- `SemanticScholarAgent`
- `VerificationAgent`

`src/paperbot/repro/agents`:
- `PlanningAgent`
- `CodingAgent`
- `DebuggingAgent`
- `VerificationAgent`

## 7) Ownership and Next Actions

- Research API owner: normalize analyze/review event contracts before runtime migration
- Scholar Tracking owner: isolate profile lookup from agent dependency where possible
- Paper2Code owner: keep orchestrator stable while introducing runtime adapter
- Platform owner: introduce unified `AgentRuntime` + `SourceCollector` contracts in next issue
