"""
HarvestPipeline integration tests.

Tests the complete harvest pipeline with mocked harvesters.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from paperbot.domain.harvest import HarvestedPaper, HarvestResult, HarvestSource
from paperbot.application.workflows.harvest_pipeline import (
    HarvestConfig,
    HarvestFinalResult,
    HarvestPipeline,
    HarvestProgress,
)


@pytest.fixture
def mock_harvesters():
    """Create mock harvesters with predefined responses."""
    arxiv_papers = [
        HarvestedPaper(
            title="Transformer Architecture for NLP",
            source=HarvestSource.ARXIV,
            abstract="We propose transformers.",
            arxiv_id="2301.00001",
            year=2023,
        ),
        HarvestedPaper(
            title="BERT Pre-training",
            source=HarvestSource.ARXIV,
            abstract="We introduce BERT.",
            arxiv_id="2301.00002",
            year=2023,
        ),
    ]

    s2_papers = [
        HarvestedPaper(
            title="Transformer Architecture for NLP",  # Duplicate
            source=HarvestSource.SEMANTIC_SCHOLAR,
            abstract="We propose transformers for various NLP tasks.",
            doi="10.1234/transformer",
            arxiv_id="2301.00001",
            semantic_scholar_id="s2-001",
            year=2023,
            citation_count=500,
        ),
        HarvestedPaper(
            title="GPT Language Models",
            source=HarvestSource.SEMANTIC_SCHOLAR,
            abstract="Generative pre-training for language understanding.",
            doi="10.1234/gpt",
            semantic_scholar_id="s2-002",
            year=2023,
            citation_count=1000,
        ),
    ]

    openalex_papers = [
        HarvestedPaper(
            title="Vision Transformers",
            source=HarvestSource.OPENALEX,
            abstract="Transformers for computer vision.",
            doi="10.1234/vit",
            openalex_id="W001",
            year=2024,
            citation_count=300,
        ),
    ]

    def create_harvester(source, papers, error=None):
        harvester = MagicMock()
        harvester.source = source
        harvester.search = AsyncMock(
            return_value=HarvestResult(
                source=source,
                papers=papers,
                total_found=len(papers),
                error=error,
            )
        )
        harvester.close = AsyncMock()
        return harvester

    return {
        "arxiv": create_harvester(HarvestSource.ARXIV, arxiv_papers),
        "semantic_scholar": create_harvester(HarvestSource.SEMANTIC_SCHOLAR, s2_papers),
        "openalex": create_harvester(HarvestSource.OPENALEX, openalex_papers),
    }


@pytest.fixture
def pipeline(tmp_path, mock_harvesters):
    """Create HarvestPipeline with mocked dependencies."""
    db_url = f"sqlite:///{tmp_path / 'test_harvest.db'}"
    pipeline = HarvestPipeline(db_url=db_url)

    # Inject mock harvesters
    def get_mock_harvester(source):
        return mock_harvesters.get(source)

    pipeline._get_harvester = get_mock_harvester
    return pipeline


class TestHarvestPipelineRun:
    """Tests for harvest pipeline execution."""

    @pytest.mark.asyncio
    async def test_run_full_pipeline(self, pipeline):
        """Run full harvest pipeline with all sources."""
        config = HarvestConfig(
            keywords=["transformer", "NLP"],
            max_results_per_source=50,
            expand_keywords=False,  # Skip expansion for predictable test
            recommend_venues=False,  # Skip venue recommendation
        )

        progress_messages = []
        final_result = None

        async for item in pipeline.run(config):
            if isinstance(item, HarvestProgress):
                progress_messages.append(item)
            elif isinstance(item, HarvestFinalResult):
                final_result = item

        # Verify progress messages
        phases = [p.phase for p in progress_messages]
        assert "Expanding" in phases
        assert "Initializing" in phases
        assert "Harvesting" in phases
        assert "Deduplicating" in phases
        assert "Storing" in phases

        # Verify final result
        assert final_result is not None
        assert final_result.status == "success"
        assert final_result.papers_found == 5  # 2 + 2 + 1
        assert final_result.papers_deduplicated > 0  # Transformer paper is duplicate
        assert final_result.duration_seconds > 0

    @pytest.mark.asyncio
    async def test_run_with_keyword_expansion(self, pipeline):
        """Pipeline expands keywords when enabled."""
        config = HarvestConfig(
            keywords=["LLM"],  # Should expand to "large language model"
            expand_keywords=True,
            recommend_venues=False,
        )

        async for item in pipeline.run(config):
            if isinstance(item, HarvestProgress) and item.phase == "Expanding":
                assert item.message == "Expanding keywords..."

    @pytest.mark.asyncio
    async def test_run_with_venue_recommendation(self, pipeline):
        """Pipeline recommends venues when enabled and no venues specified."""
        config = HarvestConfig(
            keywords=["security"],
            venues=None,  # No venues specified
            expand_keywords=False,
            recommend_venues=True,
        )

        found_recommend_phase = False
        async for item in pipeline.run(config):
            if isinstance(item, HarvestProgress) and item.phase == "Recommending":
                found_recommend_phase = True

        assert found_recommend_phase

    @pytest.mark.asyncio
    async def test_run_with_specific_sources(self, pipeline):
        """Pipeline uses only specified sources."""
        config = HarvestConfig(
            keywords=["test"],
            sources=["arxiv"],  # Only arXiv
            expand_keywords=False,
            recommend_venues=False,
        )

        final_result = None
        async for item in pipeline.run(config):
            if isinstance(item, HarvestFinalResult):
                final_result = item

        assert final_result is not None
        assert "arxiv" in final_result.source_results
        # Should not query other sources
        # (mock harvesters would have papers, so we check papers_found)
        assert final_result.papers_found == 2  # Only arXiv papers

    @pytest.mark.asyncio
    async def test_run_creates_harvest_run_record(self, pipeline):
        """Pipeline creates harvest run record in database."""
        config = HarvestConfig(
            keywords=["test"],
            expand_keywords=False,
            recommend_venues=False,
        )

        final_result = None
        async for item in pipeline.run(config):
            if isinstance(item, HarvestFinalResult):
                final_result = item

        # Verify harvest run was created
        run = pipeline.paper_store.get_harvest_run(final_result.run_id)
        assert run is not None
        assert run.status == "success"
        assert run.papers_found > 0

    @pytest.mark.asyncio
    async def test_run_stores_papers(self, pipeline):
        """Pipeline stores papers in database."""
        config = HarvestConfig(
            keywords=["test"],
            expand_keywords=False,
            recommend_venues=False,
        )

        async for item in pipeline.run(config):
            pass  # Just run to completion

        # Verify papers were stored
        paper_count = pipeline.paper_store.get_paper_count()
        assert paper_count > 0

    @pytest.mark.asyncio
    async def test_run_deduplicates_papers(self, pipeline):
        """Pipeline deduplicates papers across sources."""
        config = HarvestConfig(
            keywords=["test"],
            sources=["arxiv", "semantic_scholar"],
            expand_keywords=False,
            recommend_venues=False,
        )

        final_result = None
        async for item in pipeline.run(config):
            if isinstance(item, HarvestFinalResult):
                final_result = item

        # Transformer paper appears in both sources
        assert final_result.papers_deduplicated > 0
        # Total found - new papers = deduplicated
        total_raw = final_result.papers_found
        stored = final_result.papers_new
        # Due to deduplication, stored < total_raw
        assert stored < total_raw or final_result.papers_deduplicated > 0

    @pytest.mark.asyncio
    async def test_run_with_year_filter(self, pipeline, mock_harvesters):
        """Pipeline passes year filters to harvesters."""
        config = HarvestConfig(
            keywords=["test"],
            year_from=2023,
            year_to=2024,
            sources=["arxiv"],
            expand_keywords=False,
            recommend_venues=False,
        )

        async for item in pipeline.run(config):
            pass

        # Verify harvester was called with year filters
        mock_harvesters["arxiv"].search.assert_called_once()
        call_kwargs = mock_harvesters["arxiv"].search.call_args[1]
        assert call_kwargs["year_from"] == 2023
        assert call_kwargs["year_to"] == 2024


class TestHarvestPipelineErrorHandling:
    """Tests for error handling in harvest pipeline."""

    @pytest.mark.asyncio
    async def test_partial_failure(self, tmp_path):
        """Pipeline handles partial source failures."""
        db_url = f"sqlite:///{tmp_path / 'test_partial.db'}"
        pipeline = HarvestPipeline(db_url=db_url)

        # Create harvesters with one failing
        def get_harvester(source):
            if source == "arxiv":
                harvester = MagicMock()
                harvester.source = HarvestSource.ARXIV
                harvester.search = AsyncMock(
                    return_value=HarvestResult(
                        source=HarvestSource.ARXIV,
                        papers=[
                            HarvestedPaper(
                                title="Working Paper",
                                source=HarvestSource.ARXIV,
                            )
                        ],
                        total_found=1,
                    )
                )
                harvester.close = AsyncMock()
                return harvester
            elif source == "semantic_scholar":
                harvester = MagicMock()
                harvester.source = HarvestSource.SEMANTIC_SCHOLAR
                harvester.search = AsyncMock(
                    return_value=HarvestResult(
                        source=HarvestSource.SEMANTIC_SCHOLAR,
                        papers=[],
                        total_found=0,
                        error="Rate limit exceeded",
                    )
                )
                harvester.close = AsyncMock()
                return harvester
            return None

        pipeline._get_harvester = get_harvester

        config = HarvestConfig(
            keywords=["test"],
            sources=["arxiv", "semantic_scholar"],
            expand_keywords=False,
            recommend_venues=False,
        )

        final_result = None
        async for item in pipeline.run(config):
            if isinstance(item, HarvestFinalResult):
                final_result = item

        assert final_result.status == "partial"  # Not full success
        assert "semantic_scholar" in final_result.errors
        assert final_result.papers_new == 1  # From arXiv

        await pipeline.close()

    @pytest.mark.asyncio
    async def test_all_sources_fail(self, tmp_path):
        """Pipeline handles all sources failing."""
        db_url = f"sqlite:///{tmp_path / 'test_all_fail.db'}"
        pipeline = HarvestPipeline(db_url=db_url)

        def get_failing_harvester(source):
            if source == "arxiv":
                harvester = MagicMock()
                harvester.source = HarvestSource.ARXIV
                harvester.search = AsyncMock(
                    return_value=HarvestResult(
                        source=HarvestSource.ARXIV,
                        papers=[],
                        total_found=0,
                        error="Connection timeout",
                    )
                )
                harvester.close = AsyncMock()
                return harvester
            return None

        pipeline._get_harvester = get_failing_harvester

        config = HarvestConfig(
            keywords=["test"],
            sources=["arxiv"],
            expand_keywords=False,
            recommend_venues=False,
        )

        final_result = None
        async for item in pipeline.run(config):
            if isinstance(item, HarvestFinalResult):
                final_result = item

        assert final_result.status == "failed"
        assert "arxiv" in final_result.errors
        assert final_result.papers_new == 0

        await pipeline.close()

    @pytest.mark.asyncio
    async def test_harvester_exception(self, tmp_path):
        """Pipeline handles harvester exceptions gracefully."""
        db_url = f"sqlite:///{tmp_path / 'test_exception.db'}"
        pipeline = HarvestPipeline(db_url=db_url)

        def get_throwing_harvester(source):
            if source == "arxiv":
                harvester = MagicMock()
                harvester.source = HarvestSource.ARXIV
                harvester.search = AsyncMock(
                    side_effect=Exception("Unexpected error")
                )
                harvester.close = AsyncMock()
                return harvester
            return None

        pipeline._get_harvester = get_throwing_harvester

        config = HarvestConfig(
            keywords=["test"],
            sources=["arxiv"],
            expand_keywords=False,
            recommend_venues=False,
        )

        final_result = None
        async for item in pipeline.run(config):
            if isinstance(item, HarvestFinalResult):
                final_result = item

        assert final_result is not None
        assert "arxiv" in final_result.errors
        assert "Unexpected error" in final_result.errors["arxiv"]

        await pipeline.close()


class TestHarvestPipelineRunSync:
    """Tests for synchronous pipeline execution."""

    @pytest.mark.asyncio
    async def test_run_sync_returns_final_result(self, pipeline):
        """run_sync returns only the final result."""
        config = HarvestConfig(
            keywords=["test"],
            expand_keywords=False,
            recommend_venues=False,
        )

        result = await pipeline.run_sync(config)

        assert isinstance(result, HarvestFinalResult)
        assert result.status in ("success", "partial", "failed")


class TestHarvestPipelineContextManager:
    """Tests for context manager protocol."""

    @pytest.mark.asyncio
    async def test_context_manager(self, tmp_path, mock_harvesters):
        """Pipeline can be used as async context manager."""
        db_url = f"sqlite:///{tmp_path / 'test_ctx.db'}"

        async with HarvestPipeline(db_url=db_url) as pipeline:
            # Inject mock harvesters
            def get_mock_harvester(source):
                return mock_harvesters.get(source)

            pipeline._get_harvester = get_mock_harvester

            config = HarvestConfig(
                keywords=["test"],
                expand_keywords=False,
                recommend_venues=False,
            )

            result = await pipeline.run_sync(config)
            assert result is not None

        # Pipeline should be closed after context exits


class TestHarvestPipelineRunId:
    """Tests for run ID generation."""

    def test_new_run_id_format(self):
        """Run ID follows expected format."""
        run_id = HarvestPipeline.new_run_id()

        assert run_id.startswith("harvest-")
        parts = run_id.split("-")
        assert len(parts) == 4  # harvest-YYYYMMDD-HHMMSS-suffix

    def test_new_run_id_unique(self):
        """Each run ID is unique."""
        ids = [HarvestPipeline.new_run_id() for _ in range(10)]
        assert len(set(ids)) == 10

    @pytest.mark.asyncio
    async def test_custom_run_id(self, pipeline):
        """Pipeline accepts custom run ID."""
        config = HarvestConfig(
            keywords=["test"],
            expand_keywords=False,
            recommend_venues=False,
        )

        custom_id = "custom-run-123"
        final_result = None

        async for item in pipeline.run(config, run_id=custom_id):
            if isinstance(item, HarvestFinalResult):
                final_result = item

        assert final_result.run_id == custom_id


class TestHarvestPipelineServices:
    """Tests for lazy-loaded services."""

    def test_query_rewriter_lazy_init(self, tmp_path):
        """QueryRewriter is lazily initialized."""
        db_url = f"sqlite:///{tmp_path / 'test_lazy.db'}"
        pipeline = HarvestPipeline(db_url=db_url)

        assert pipeline._query_rewriter is None
        _ = pipeline.query_rewriter
        assert pipeline._query_rewriter is not None

    def test_venue_recommender_lazy_init(self, tmp_path):
        """VenueRecommender is lazily initialized."""
        db_url = f"sqlite:///{tmp_path / 'test_lazy.db'}"
        pipeline = HarvestPipeline(db_url=db_url)

        assert pipeline._venue_recommender is None
        _ = pipeline.venue_recommender
        assert pipeline._venue_recommender is not None

    def test_paper_store_lazy_init(self, tmp_path):
        """PaperStore is lazily initialized."""
        db_url = f"sqlite:///{tmp_path / 'test_lazy.db'}"
        pipeline = HarvestPipeline(db_url=db_url)

        assert pipeline._paper_store is None
        _ = pipeline.paper_store
        assert pipeline._paper_store is not None
