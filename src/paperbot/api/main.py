"""
PaperBot API - FastAPI backend for CLI and Web interfaces
Supports Server-Sent Events (SSE) for streaming responses
"""

import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI

from paperbot.api.middleware import install_api_auth, install_cors, install_rate_limiting
from .routes import (
    track,
    gen_code,
    chat,
    runs,
    jobs,
    sandbox,
    runbook,
    memory,
    research,
    paperscool,
    newsletter,
    obsidian,
    harvest,
    model_endpoints,
    embedding_settings,
    studio_chat,
    repro_context,
    feed,
    intelligence,
    push_commands,
    agent_board,
    wiki,
    auth,
    studio_skills,
)
from .routes import events as events_route
from paperbot.api.error_handling import install_api_error_handling
from paperbot.infrastructure.event_log.logging_event_log import LoggingEventLog
from paperbot.infrastructure.event_log.composite_event_log import CompositeEventLog
from paperbot.infrastructure.event_log.sqlalchemy_event_log import SqlAlchemyEventLog
from paperbot.infrastructure.event_log.event_bus_event_log import EventBusEventLog

# Load repo .env deterministically so API keys match local config.
# override=True is intentional to avoid picking up a different .env from another cwd.
_repo_root = Path(__file__).resolve().parents[3]
_env_path = _repo_root / ".env"
_override = os.getenv("PAPERBOT_DOTENV_OVERRIDE", "true").lower() in {"1", "true", "yes"}
if _env_path.exists():
    load_dotenv(_env_path, override=_override)
else:
    load_dotenv(find_dotenv(usecwd=True), override=False)

app = FastAPI(
    title="PaperBot API",
    description="API for scholar tracking, paper analysis, and code generation",
    version="0.1.0",
)

install_api_error_handling(app)
install_api_auth(app)
install_rate_limiting(app)
install_cors(app)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "version": "0.1.0"}


# Include routers
app.include_router(track.router, prefix="/api", tags=["Scholar Tracking"])
app.include_router(gen_code.router, prefix="/api", tags=["Paper2Code"])
app.include_router(chat.router, prefix="/api", tags=["Chat"])
app.include_router(runs.router, prefix="/api", tags=["Runs"])
app.include_router(jobs.router, prefix="/api", tags=["Jobs"])
app.include_router(sandbox.router, prefix="/api", tags=["Sandbox"])
app.include_router(runbook.router, prefix="/api", tags=["Runbook"])
app.include_router(memory.router, prefix="/api", tags=["Memory"])
app.include_router(research.router, prefix="/api", tags=["Research"])
app.include_router(paperscool.router, prefix="/api", tags=["PapersCool"])
app.include_router(newsletter.router, prefix="/api", tags=["Newsletter"])
app.include_router(obsidian.router, prefix="/api", tags=["Obsidian"])
app.include_router(harvest.router, prefix="/api", tags=["Harvest"])
app.include_router(model_endpoints.router, prefix="/api", tags=["Model Endpoints"])
app.include_router(embedding_settings.router, prefix="/api", tags=["Embedding Settings"])
app.include_router(studio_chat.router, prefix="/api", tags=["Studio Chat"])
app.include_router(studio_skills.router, prefix="/api", tags=["Studio Skills"])
app.include_router(repro_context.router, prefix="/api/research/repro/context", tags=["P2C"])
app.include_router(feed.router, prefix="/api", tags=["Feed"])
app.include_router(intelligence.router, prefix="/api", tags=["Intelligence"])
app.include_router(push_commands.router, prefix="/api", tags=["Push"])
app.include_router(agent_board.router, tags=["Agent Board"])
app.include_router(wiki.router, prefix="/api", tags=["Wiki"])
app.include_router(auth.router)
app.include_router(events_route.router, prefix="/api", tags=["Events"])


@app.on_event("startup")
async def _startup_eventlog():
    # Phase-0: create a single event log backend and store on app.state.
    # Per-request run_id/trace_id are generated in handlers.
    # Phase-7: EventBusEventLog added as third backend for SSE fan-out delivery.
    bus = EventBusEventLog()
    backends = [LoggingEventLog(), bus]
    try:
        backends.insert(1, SqlAlchemyEventLog())
    except Exception:
        # If SQLAlchemy isn't available or DB init fails, keep SSE available via the bus.
        pass
    app.state.event_log = CompositeEventLog(backends)
    obsidian.initialize_obsidian_runtime(app)


@app.on_event("shutdown")
async def _shutdown_runtime():
    event_log = getattr(app.state, "event_log", None)
    if event_log is not None:
        try:
            event_log.close()
        except Exception:
            pass
    obsidian.shutdown_obsidian_runtime(app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
