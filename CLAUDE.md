# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Development Commands

### Python Backend

```bash
# Install dependencies (use dev extras for testing tools)
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # includes pytest-asyncio, respx, aioresponses, etc.
pip install -r requirements.txt # alternative: full deps without dev extras

# Run database migrations (required before first run)
export PAPERBOT_DB_URL="sqlite:///data/paperbot.db"
alembic upgrade head

# Start API server (dev mode with auto-reload)
python -m uvicorn src.paperbot.api.main:app --reload --port 8000

# Start ARQ worker (Redis-backed task queue for DailyPaper cron, etc.)
arq paperbot.infrastructure.queue.arq_worker.WorkerSettings

# Code formatting & type checking
python -m black .
python -m isort .
pyright src/
```

### Testing

**Important**: `asyncio_mode = "strict"` in pyproject.toml — every async test must be decorated with `@pytest.mark.asyncio` explicitly.

```bash
# Run all tests
pytest -q

# Run a single test file or test
pytest tests/unit/test_di_container.py
pytest tests/unit/test_di_container.py::test_singleton -v

# Run CI offline gates (same tests as GitHub Actions)
# CI uses requirements-ci.txt (lighter, no weasyprint/heavy deps)
PYTHONPATH=src pytest -q \
  tests/unit/test_scholar_from_config.py \
  tests/unit/test_source_registry_modes.py \
  tests/unit/test_arq_worker_settings.py \
  tests/unit/test_jobs_routes_import.py \
  tests/unit/test_dailypaper.py \
  tests/unit/test_paper_judge.py \
  tests/unit/test_memory_module.py \
  tests/unit/test_memory_metric_collector.py \
  tests/unit/test_llm_service.py \
  tests/unit/test_di_container.py \
  tests/unit/test_pipeline.py \
  tests/integration/test_eventlog_sqlalchemy.py \
  tests/integration/test_crawler_contract_parsers.py \
  tests/integration/test_arxiv_connector_fixture.py \
  tests/e2e/test_api_track_fullstack_offline.py

# Eval smoke tests (also in CI)
python evals/runners/run_scholar_pipeline_smoke.py
python evals/runners/run_track_pipeline_smoke.py
python evals/runners/run_eventlog_replay_smoke.py

# Memory evals (also in CI)
PYTHONPATH=src pytest -q evals/memory/test_deletion_compliance.py evals/memory/test_retrieval_hit_rate.py
```

**Test patterns**: Tests use stub/fake classes (e.g., `_FakeLLMService`) rather than `unittest.mock`. HTTP mocking via `respx` and `aioresponses`. Use `tmp_path` for temp SQLite DBs. Reset singletons in `setup_method` (e.g., `Container._instance = None`).

### Web Dashboard (Next.js)

```bash
cd web
npm install
npm run dev      # Dev server at http://localhost:3000
npm run build    # Production build
npm run start    # Production serve
npm run lint     # ESLint
```

Stack: Next.js 16 + React 19 + Tailwind CSS **v4** + Zustand (state) + Vercel AI SDK v5. Monaco editor for code, XTerm for terminal, @xyflow/react for DAG visualization.

### Terminal CLI (Ink/React)

Requires Node.js >= 18.

```bash
cd cli
npm install
npm run dev        # Hot-reload dev mode (tsx watch)
npm run build      # Production build
npm run start      # Run CLI
npm run typecheck  # tsc --noEmit
npm run lint       # ESLint
```

## Architecture Overview

PaperBot is a multi-agent research workflow framework with three main components:

1. **Python Backend** (`src/paperbot/`) - FastAPI server with SSE streaming, multi-agent orchestration
2. **Web Dashboard** (`web/`) - Next.js App Router, pages: dashboard, papers, research, scholars, settings, studio, wiki, workflows
3. **Terminal UI** (`cli/`) - Ink/React CLI (installable as `paperbot` global command)

### Core Python Layers

```
src/paperbot/
├── domain/              # Domain models: Paper, Scholar, Track, Feedback, Enrichment, Harvest
├── application/         # Use cases and orchestration
│   ├── ports/           # Interface definitions (enrichment, event_log, paper_registry, etc.)
│   ├── services/        # Application services (anchor, paper_search, llm_service, etc.)
│   ├── workflows/       # Pipeline orchestrators (dailypaper, scholar_pipeline, harvest_pipeline)
│   │   └── analysis/    # Paper analysis: judge, summarizer, trend_analyzer, relevance_assessor
│   ├── collaboration/   # Cross-agent message schemas
│   └── registries/      # Source/provider registries
├── agents/              # Multi-agent implementations (BaseAgent in base.py)
│   ├── research/        # Paper analysis agents
│   ├── scholar_tracking/# Scholar monitoring agents
│   ├── review/          # Peer review simulation
│   ├── verification/    # Claim verification
│   └── prompts/         # Agent prompt templates
├── api/                 # FastAPI server
│   ├── main.py          # App setup, CORS, router registration
│   ├── streaming.py     # SSE utilities
│   └── routes/          # Endpoint handlers
├── core/                # Core abstractions
│   ├── collaboration/   # AgentCoordinator, ScoreShareBus, FailFastEvaluator
│   ├── di/              # Dependency injection container (Container.instance() singleton)
│   ├── pipeline/        # Task pipeline framework
│   └── report_engine/   # Report generation with Jinja2 templates
├── repro/               # Paper2Code / DeepCode Studio pipeline
│   ├── orchestrator.py  # Multi-stage execution
│   ├── agents/          # Planning/Coding/Debugging/Verification agents
│   ├── nodes/           # Pipeline nodes: planning, blueprint, analysis, environment, generation, verification
│   ├── memory/          # CodeMemory (cross-file context) + SymbolIndex (AST indexing)
│   ├── rag/             # CodeRAG - pattern retrieval from knowledge_base
│   └── *_executor.py    # Execution backends: docker, e2b
├── infrastructure/      # External services and persistence
│   ├── llm/providers/   # OpenAI, Anthropic, Ollama providers
│   ├── stores/          # SQLAlchemy repositories
│   ├── connectors/      # Data sources: arxiv, openalex, reddit, zotero, hf_daily, paperscool, x
│   ├── harvesters/      # Bulk paper harvesters (arxiv, openalex, semantic_scholar)
│   ├── adapters/        # Search adapters (arxiv, s2, openalex, hf, paperscool)
│   ├── crawling/        # HTTP downloader, request layer, parsers
│   ├── event_log/       # Event persistence (SQLAlchemy, memory, composite, logging)
│   ├── api_clients/     # Semantic Scholar, GitHub clients
│   └── queue/           # ARQ worker (Redis-backed async job queue)
├── context_engine/      # Research context & track routing
└── workflows/           # Workflow orchestration, scheduler
```

### Key Architectural Patterns

- **Domain-Driven Design**: `domain/` models → `application/ports/` interfaces → `infrastructure/` implementations
- **Multi-Agent Orchestration**: `AgentCoordinator` registers agents, broadcasts tasks, collects results. `ScoreShareBus` enables cross-stage evaluation sharing. `FailFastEvaluator` stops low-quality work early.
- **Paper2Code Pipeline**: Multi-stage (Planning → Blueprint → Environment → Generation → Verification) with `CodeMemory` for stateful cross-file context and `CodeRAG` for pattern retrieval. Executors run code in Docker or E2B sandboxes.
- **DI Container**: Single `Container.instance()` holds all services. Reset with `Container._instance = None` in tests.
- **ARQ Task Queue**: Redis-backed async jobs for DailyPaper cron and background tasks.

### API Endpoints

Key SSE-streaming endpoints:
- `GET /api/track` - Scholar tracking
- `POST /api/analyze` - Paper analysis
- `POST /api/gen-code` - Paper2Code generation
- `POST /api/review` - Deep review simulation
- `POST /api/research/*` - Personalized research context & tracks
- `GET/POST /api/runbook/*` - DeepCode Studio file management (list/read/write/snapshot/diff/revert)
- `GET/POST /api/sandbox/*` - Studio execution: queue, run logs, resource metrics

## Configuration

Copy `env.example` to `.env` and configure:

```bash
# Required for LLM features
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# Optional
SEMANTIC_SCHOLAR_API_KEY=...
GITHUB_TOKEN=...

# Runtime
PAPERBOT_DB_URL=sqlite:///data/paperbot.db
PAPERBOT_OFFLINE=true              # Disable all external network calls
PAPERBOT_MODE=academic             # or "production"

# Redis (for ARQ worker)
PAPERBOT_REDIS_HOST=127.0.0.1
PAPERBOT_REDIS_PORT=6379

# Studio file access (comma-separated allowed dir prefixes beyond /tmp and cwd)
PAPERBOT_RUNBOOK_ALLOW_DIR_PREFIXES=/path/to/projects

# Execution backend
PAPERBOT_EXECUTOR=docker           # or "e2b" or "auto"
```

Configuration files:
- `config/config.yaml` - Main app config (models, venues, thresholds)
- `config/settings.py` - Dataclass settings with env var overrides
- `config/validated_settings.py` - Pydantic-validated settings wrapper
- `config/models.py` - Pydantic models: AppConfig, LLMConfig, ReproConfig, PipelineConfig
- `config/scholar_subscriptions.yaml` - Tracked scholars

## Code Style

- **Python**: Black (line-length 100, target py310), isort (Black profile), pyright (basic mode)
- **TypeScript**: Follow existing patterns in `web/src/` and `cli/src/`
- **Commits**: Conventional Commits format (`feat:`, `fix:`, `refactor:`, `docs:`)

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs on push/PR to `master`:
- Matrix: Python 3.10, 3.11, 3.12
- Uses `requirements-ci.txt` (not `requirements.txt`) for lighter installs
- Runs: offline UT/IT/E2E gates → eval smoke tests → memory evals

## Adding New Components

### New Agent
1. Create `src/paperbot/agents/{feature}/agent.py` extending `BaseAgent`
2. Implement `async def execute(self, input) -> ExecutionResult`
3. Add prompt templates in `agents/prompts/{feature}/`
4. Register in DI container or coordinator
5. Add unit tests in `tests/unit/`

### New API Endpoint
1. Create `src/paperbot/api/routes/{feature}.py` with `APIRouter`
2. Use SSE streaming if needed (see `streaming.py`)
3. Register router in `api/main.py`
4. Add integration tests

### Database Changes
1. Add model in `infrastructure/stores/models.py`
2. Create migration: `alembic revision --autogenerate -m "description"`
3. Apply: `alembic upgrade head`
