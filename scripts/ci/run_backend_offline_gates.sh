#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

export PYTHONPATH="${PYTHONPATH:-src}"

python -m pytest -q "$@" \
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
  tests/unit/repro/test_agents.py \
  tests/unit/repro/test_blueprint.py \
  tests/unit/repro/test_memory.py \
  tests/unit/repro/test_orchestrator.py \
  tests/unit/repro/test_rag.py \
  tests/integration/test_eventlog_sqlalchemy.py \
  tests/integration/test_crawler_contract_parsers.py \
  tests/integration/test_arxiv_connector_fixture.py \
  tests/integration/test_repro_deepcode.py \
  tests/test_generation_agent.py \
  tests/test_repro_planning.py \
  tests/test_repro_agent.py \
  tests/test_repro_e2e.py \
  tests/test_repro_models.py \
  tests/e2e/test_api_track_fullstack_offline.py
