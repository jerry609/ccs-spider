# paperbot/core/workflow_coordinator.py
"""
学者追踪工作流协调器

负责协调论文分析流水线的各个阶段。

P3 增强:
- ScoreShareBus 评分共享
- FailFastEvaluator 快速失败
"""

from __future__ import annotations

import logging
from typing import Dict, Any, Optional, List, Tuple, TYPE_CHECKING
from pathlib import Path
from dataclasses import dataclass, field

from .collaboration.score_bus import (
    ScoreShareBus,
    StageScore,
    create_code_score,
    create_influence_score,
    create_quality_score,
    create_research_score,
)
from .fail_fast import FailFastEvaluator, FailFastConfig

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from paperbot.application.ports.event_log_port import EventLogPort


@dataclass
class PipelineContext:
    """流水线上下文"""
    paper: Any = None
    scholar_name: Optional[str] = None
    research_result: Dict[str, Any] = field(default_factory=lambda: {})
    code_analysis_result: Dict[str, Any] = field(default_factory=lambda: {})
    quality_result: Dict[str, Any] = field(default_factory=lambda: {})
    influence_result: Any = None
    stages: Dict[str, Any] = field(default_factory=lambda: {})
    status: str = "pending"
    error: Optional[str] = None
    # P3 新增
    skipped_stages: List[str] = field(default_factory=list)
    score_bus: Optional[ScoreShareBus] = None


class ScholarWorkflowCoordinator:
    """
    学者工作流协调器
    
    协调论文分析流水线的各个阶段：
    1. 研究分析 (ResearchAgent)
    2. 代码分析 (CodeAnalysisAgent) 
    3. 质量评估 (QualityAgent)
    4. 影响力计算 (InfluenceCalculator)
    5. 报告生成 (ReportWriter)
    
    P3 增强:
    - 评分共享总线 (ScoreShareBus)
    - Fail-Fast 早期中断
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化协调器
        
        Args:
            config: 配置字典，包含：
                - output_dir: 输出目录
                - report_template: 报告模板名称
                - mode: 运行模式 (production/academic)
                - enable_fail_fast: 是否启用 Fail-Fast
                - fail_fast: Fail-Fast 配置
        """
        self.config = config or {}
        self.output_dir = Path(self.config.get("output_dir", "./output/reports"))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.report_template = self.config.get("report_template", "paper_report.md.j2")
        self.mode = self.config.get("mode", "production")
        
        # P3: Fail-Fast 配置
        self.enable_fail_fast = self.config.get("enable_fail_fast", True)
        fail_fast_config = FailFastConfig.from_dict(self.config.get("fail_fast", {}))
        self.fail_fast_evaluator = FailFastEvaluator(fail_fast_config)
        
        # 延迟初始化组件
        self._research_agent = None
        self._code_analysis_agent = None
        self._quality_agent = None
        self._influence_calculator = None
        self._report_writer = None
        
        logger.info(f"ScholarWorkflowCoordinator initialized with mode={self.mode}, fail_fast={self.enable_fail_fast}")
    
    @property
    def research_agent(self):
        """延迟初始化 ResearchAgent"""
        if self._research_agent is None:
            try:
                from paperbot.agents.research.agent import ResearchAgent
                self._research_agent = ResearchAgent({})
            except ImportError as e:
                logger.warning(f"Failed to import ResearchAgent: {e}")
        return self._research_agent
    
    @property
    def code_analysis_agent(self):
        """延迟初始化 CodeAnalysisAgent"""
        if self._code_analysis_agent is None:
            try:
                from paperbot.agents.code_analysis.agent import CodeAnalysisAgent
                self._code_analysis_agent = CodeAnalysisAgent({})
            except ImportError as e:
                logger.warning(f"Failed to import CodeAnalysisAgent: {e}")
        return self._code_analysis_agent
    
    @property
    def quality_agent(self):
        """延迟初始化 QualityAgent"""
        if self._quality_agent is None:
            try:
                from paperbot.agents.quality.agent import QualityAgent
                self._quality_agent = QualityAgent({})
            except ImportError as e:
                logger.warning(f"Failed to import QualityAgent: {e}")
        return self._quality_agent
    
    @property
    def influence_calculator(self):
        """延迟初始化 InfluenceCalculator"""
        if self._influence_calculator is None:
            try:
                from paperbot.domain.influence.calculator import InfluenceCalculator
                self._influence_calculator = InfluenceCalculator()
            except ImportError as e:
                logger.warning(f"Failed to import InfluenceCalculator: {e}")
        return self._influence_calculator
    
    @property
    def report_writer(self):
        """延迟初始化 ReportWriter"""
        if self._report_writer is None:
            try:
                from paperbot.presentation.reports.writer import ReportWriter
                self._report_writer = ReportWriter(
                    template_name=self.report_template,
                    output_dir=self.output_dir,
                )
            except ImportError as e:
                logger.warning(f"Failed to import ReportWriter: {e}")
        return self._report_writer
    
    async def run_paper_pipeline(
        self,
        paper: Any,
        scholar_name: Optional[str] = None,
        persist_report: bool = True,
        *,
        event_log: "Optional[EventLogPort]" = None,
        run_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Tuple[Optional[Path], Any, Dict[str, Any]]:
        """
        运行论文分析流水线
        
        Args:
            paper: 论文元数据 (PaperMeta)
            scholar_name: 学者名称
            persist_report: 是否持久化报告
            
        Returns:
            (报告路径, 影响力结果, 流水线数据)
        """
        paper_id = getattr(paper, 'paper_id', 'unknown')
        
        # P3: 初始化评分共享总线
        score_bus = ScoreShareBus(
            paper_id=paper_id,
            event_log=event_log,
            run_id=run_id,
            trace_id=trace_id,
            workflow="scholar_pipeline",
        )
        ctx = PipelineContext(paper=paper, scholar_name=scholar_name, score_bus=score_bus)
        
        try:
            # 1. 研究分析
            ctx = await self._run_research_stage(ctx)
            
            # P3: 发布研究评分
            self._publish_research_score(ctx)

            if self.enable_fail_fast:
                early_exit = self.fail_fast_evaluator.evaluate_early_exit(score_bus)
                if early_exit.should_skip:
                    logger.info(f"⚡ Fail-Fast: 提前终止流水线 - {early_exit.reason}")
                    for stage in early_exit.skipped_stages:
                        if stage not in ctx.skipped_stages:
                            ctx.skipped_stages.append(stage)
                        ctx.stages[stage] = {"status": "skipped", "reason": early_exit.reason}
                    ctx.influence_result = self._create_default_influence()
                    ctx.status = "success"
                    return None, ctx.influence_result, self._build_pipeline_data(ctx)
            
            # 检查是否有代码
            has_code = bool(paper.github_url or getattr(paper, 'has_code', False))
            
            # 2. 代码分析（如果有代码）
            if has_code:
                # P3: Fail-Fast 检查
                if self.enable_fail_fast:
                    skip_decision = self.fail_fast_evaluator.should_skip_stage("code", score_bus, has_code)
                    if skip_decision.should_skip:
                        logger.info(f"⚡ Fail-Fast: 跳过代码分析 - {skip_decision.reason}")
                        ctx.skipped_stages.append("code")
                        ctx.stages["code_analysis"] = {
                            "status": "skipped",
                            "reason": skip_decision.reason,
                        }
                    else:
                        ctx = await self._run_code_analysis_stage(ctx)
                        self._publish_code_score(ctx)
                else:
                    ctx = await self._run_code_analysis_stage(ctx)
                    self._publish_code_score(ctx)
            
            # 3. 质量评估
            if self.enable_fail_fast:
                skip_decision = self.fail_fast_evaluator.should_skip_stage("quality", score_bus, has_code)
                if skip_decision.should_skip:
                    logger.info(f"⚡ Fail-Fast: 跳过质量评估 - {skip_decision.reason}")
                    ctx.skipped_stages.append("quality")
                    ctx.stages["quality"] = {"status": "skipped", "reason": skip_decision.reason}
                else:
                    ctx = await self._run_quality_stage(ctx)
                    self._publish_quality_score(ctx)
            else:
                ctx = await self._run_quality_stage(ctx)
                self._publish_quality_score(ctx)
            
            # 4. 影响力计算
            ctx = await self._run_influence_stage(ctx)
            self._publish_influence_score(ctx)
            
            # 5. 报告生成
            report_path = None
            if persist_report and self.report_writer:
                report_path = await self._run_report_stage(ctx)
            
            ctx.status = "success"
            
            return report_path, ctx.influence_result, self._build_pipeline_data(ctx)
            
        except Exception as e:
            logger.error(f"Pipeline failed for paper {paper_id}: {e}")
            ctx.status = "error"
            ctx.error = str(e)
            
            # 返回默认影响力结果
            default_influence = self._create_default_influence()
            return None, default_influence, self._build_pipeline_data(ctx)
    
    def _publish_research_score(self, ctx: PipelineContext) -> None:
        """发布研究阶段评分到总线"""
        if not ctx.score_bus:
            return
        
        citation_count = getattr(ctx.paper, 'citation_count', 0) or 0
        venue_tier = ctx.research_result.get("venue_tier")
        
        score = create_research_score(citation_count, venue_tier)
        ctx.score_bus.publish_score(score)
    
    def _publish_code_score(self, ctx: PipelineContext) -> None:
        """发布代码分析阶段评分到总线"""
        if not ctx.score_bus:
            return
        
        code_result = ctx.code_analysis_result
        has_code = bool(code_result)
        raw_score = code_result.get("reproducibility_score") if code_result else None
        if raw_score is None:
            raw_score = code_result.get("health_score", 0.0) if code_result else 0.0
        health_score = float(raw_score or 0.0)
        if health_score <= 1.0:
            health_score *= 100.0
        is_empty = code_result.get("is_empty_repo", False) if code_result else False
        
        score = create_code_score(has_code, health_score, is_empty)
        ctx.score_bus.publish_score(score)

    def _publish_quality_score(self, ctx: PipelineContext) -> None:
        """发布质量阶段评分到总线"""
        if not ctx.score_bus:
            return

        quality_result = ctx.quality_result or {}
        quality_scores = quality_result.get("quality_scores") or {}
        primary_score = {}
        if isinstance(quality_scores, dict) and quality_scores:
            first_score = next(iter(quality_scores.values()))
            if isinstance(first_score, dict):
                primary_score = first_score

        raw_overall = quality_result.get("quality_score")
        if raw_overall is None:
            raw_overall = primary_score.get("overall_score", 0.0)

        overall_score = float(raw_overall or 0.0)
        if overall_score <= 1.0:
            overall_score *= 100.0

        metric_scores = primary_score.get("scores") or {}
        maintainability = float(metric_scores.get("maintainability", 0.0) or 0.0)
        test_coverage = float(metric_scores.get("test_coverage", 0.0) or 0.0)
        if maintainability <= 1.0:
            maintainability *= 100.0
        if test_coverage <= 1.0:
            test_coverage *= 100.0

        ctx.score_bus.publish_score(
            create_quality_score(
                overall_score,
                maintainability=maintainability,
                test_coverage=test_coverage,
            )
        )

    def _publish_influence_score(self, ctx: PipelineContext) -> None:
        """发布影响力阶段评分到总线"""
        if not ctx.score_bus or ctx.influence_result is None:
            return

        influence = ctx.influence_result
        total_score = float(getattr(influence, "total_score", 0.0) or 0.0)
        academic_score = float(getattr(influence, "academic_score", 0.0) or 0.0)
        engineering_score = float(getattr(influence, "engineering_score", 0.0) or 0.0)
        metrics_breakdown = getattr(influence, "metrics_breakdown", {}) or {}
        momentum_score = 0.0
        if isinstance(metrics_breakdown, dict):
            academic_metrics = metrics_breakdown.get("academic") or {}
            if isinstance(academic_metrics, dict):
                momentum_score = float(academic_metrics.get("momentum_score", 0.0) or 0.0)

        ctx.score_bus.publish_score(
            create_influence_score(
                total_score=total_score,
                academic_score=academic_score,
                engineering_score=engineering_score,
                momentum_score=momentum_score,
            )
        )
    
    async def _run_research_stage(self, ctx: PipelineContext) -> PipelineContext:
        """运行研究分析阶段"""
        if not self.research_agent:
            logger.warning("ResearchAgent not available, skipping research stage")
            return ctx
        
        try:
            result = await self.research_agent.process(
                title=ctx.paper.title,
                abstract=getattr(ctx.paper, 'abstract', '') or '',
            )
            ctx.research_result = result
            ctx.stages["research"] = {"status": "success", "result": result}
        except Exception as e:
            logger.error(f"Research stage failed: {e}")
            ctx.stages["research"] = {"status": "error", "error": str(e)}
        
        return ctx
    
    async def _run_code_analysis_stage(self, ctx: PipelineContext) -> PipelineContext:
        """运行代码分析阶段"""
        if not self.code_analysis_agent:
            logger.warning("CodeAnalysisAgent not available, skipping code analysis stage")
            return ctx
        
        github_url = getattr(ctx.paper, 'github_url', None)
        if not github_url:
            return ctx
        
        try:
            result = await self.code_analysis_agent.process(repo_url=github_url)
            ctx.code_analysis_result = result
            ctx.stages["code_analysis"] = {"status": "success", "result": result}
        except Exception as e:
            logger.error(f"Code analysis stage failed: {e}")
            ctx.stages["code_analysis"] = {"status": "error", "error": str(e)}
        
        return ctx
    
    async def _run_quality_stage(self, ctx: PipelineContext) -> PipelineContext:
        """运行质量评估阶段"""
        if not self.quality_agent:
            logger.warning("QualityAgent not available, skipping quality stage")
            return ctx
        
        try:
            result = await self.quality_agent.process(
                title=ctx.paper.title,
                abstract=getattr(ctx.paper, 'abstract', '') or '',
                research_result=ctx.research_result,
                code_analysis_result=ctx.code_analysis_result,
            )
            ctx.quality_result = result
            ctx.stages["quality"] = {"status": "success", "result": result}
        except Exception as e:
            logger.error(f"Quality stage failed: {e}")
            ctx.stages["quality"] = {"status": "error", "error": str(e)}
        
        return ctx
    
    async def _run_influence_stage(self, ctx: PipelineContext) -> PipelineContext:
        """运行影响力计算阶段"""
        if not self.influence_calculator:
            logger.warning("InfluenceCalculator not available, using default influence")
            ctx.influence_result = self._create_default_influence()
            return ctx
        
        try:
            # 构建 CodeMeta（如果有代码分析结果）
            code_meta = None
            if ctx.code_analysis_result:
                try:
                    from paperbot.domain.paper import CodeMeta
                    code_meta = CodeMeta(
                        repo_url=getattr(ctx.paper, 'github_url', '') or '',
                        stars=ctx.code_analysis_result.get('stars', 0) or 0,
                        forks=ctx.code_analysis_result.get('forks', 0) or 0,
                        has_readme=ctx.code_analysis_result.get('has_readme', False),
                        updated_at=ctx.code_analysis_result.get('updated_at'),
                        last_commit_date=(
                            ctx.code_analysis_result.get('last_commit_date')
                            or ctx.code_analysis_result.get('updated_at')
                        ),
                        reproducibility_score=ctx.code_analysis_result.get('reproducibility_score'),
                    )
                except ImportError:
                    pass
            
            influence = self.influence_calculator.calculate(ctx.paper, code_meta)
            ctx.influence_result = influence
            ctx.stages["influence"] = {"status": "success", "result": influence}
        except Exception as e:
            logger.error(f"Influence stage failed: {e}")
            ctx.influence_result = self._create_default_influence()
            ctx.stages["influence"] = {"status": "error", "error": str(e)}
        
        return ctx
    
    async def _run_report_stage(self, ctx: PipelineContext) -> Optional[Path]:
        """运行报告生成阶段"""
        if not self.report_writer:
            return None
        
        try:
            md = self.report_writer.render_template(
                paper=ctx.paper,
                influence=ctx.influence_result,
                research_result=ctx.research_result,
                code_analysis_result=ctx.code_analysis_result,
                quality_result=ctx.quality_result,
                scholar_name=ctx.scholar_name,
            )
            
            path = self.report_writer.write_report(md, ctx.paper, scholar_name=ctx.scholar_name)
            ctx.stages["report"] = {"status": "success", "path": str(path)}
            return path
        except Exception as e:
            logger.error(f"Report stage failed: {e}")
            ctx.stages["report"] = {"status": "error", "error": str(e)}
            return None
    
    def _create_default_influence(self):
        """创建默认影响力结果"""
        try:
            from paperbot.domain.influence.result import InfluenceResult
            return InfluenceResult(
                total_score=0.0,
                academic_score=0.0,
                engineering_score=0.0,
                explanation="No influence data available.",
                metrics_breakdown={},
            )
        except ImportError:
            # 返回简单字典
            return {
                "total_score": 0.0,
                "academic_score": 0.0,
                "engineering_score": 0.0,
                "explanation": "No influence data available.",
                "metrics_breakdown": {},
                "recommendation": "low",
            }
    
    def _build_pipeline_data(self, ctx: PipelineContext) -> Dict[str, Any]:
        """构建流水线数据"""
        return {
            "status": ctx.status,
            "error": ctx.error,
            "stages": ctx.stages,
            "scholar_name": ctx.scholar_name,
        }
    
    async def run_batch_pipeline(
        self,
        papers: List[Any],
        scholar_name: Optional[str] = None,
        *,
        event_log: "Optional[EventLogPort]" = None,
        run_id: Optional[str] = None,
    ) -> List[Tuple[Optional[Path], Any, Dict[str, Any]]]:
        """
        批量运行论文分析流水线
        
        Args:
            papers: 论文列表
            scholar_name: 学者名称
            
        Returns:
            结果列表
        """
        results = []
        for paper in papers:
            result = await self.run_paper_pipeline(
                paper=paper,
                scholar_name=scholar_name,
                persist_report=True,
                event_log=event_log,
                run_id=run_id,
                trace_id=None,
            )
            results.append(result)
        return results
