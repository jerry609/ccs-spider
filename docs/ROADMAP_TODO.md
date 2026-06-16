# PaperBot Roadmap & TODO

> 对标 HuggingFace Daily Papers / AlphaXiv 的完整功能规划。
> 本文件同时作为迭代清单使用：完成一项请将 `[ ]` 更新为 `[x]`。


## 已完成进度（2026-02-10）

- [x] 修复 Judge Prompt f-string 语法错误（CI collection error）
- [x] DailyPaper 去除 `top_k_per_query` 过早截断（确保 `top_n` 能生效）
- [x] 新增 Repo 批量富化 API：`POST /api/research/paperscool/repos`
- [x] 新增学者网络 API：`POST /api/research/scholar/network`
- [x] 新增学者趋势 API：`POST /api/research/scholar/trends`
- [x] 打通前端 API 代理（Next route handlers）

---

## 对标结论

| 维度 | PaperBot 现状 | 差距 |
|------|--------------|------|
| AI 分析 | **领先**（5 维 Judge + Trend + Insight + Relevance） | — |
| 学者追踪 | **领先**（多 Agent + PIS 评分） | — |
| 深度评审 | **独有**（模拟同行评审） | — |
| Paper2Code | **独有**（论文→代码骨架） | — |
| 多通道推送 | **领先**（Email/Slack/钉钉） | 内容为纯文本，需富文本 |
| 论文持久化 | **缺失** | HF/AlphaXiv 都有完整论文 DB |
| 社区/交互 | 仅 feedback | HF 有 upvote/评论，AlphaXiv 有逐行批注 |
| 论文详情页 | 无 | HF 每篇论文有独立页面 |
| AI 问答 | 无 | AlphaXiv 核心卖点 |
| 个性化推荐 | Track + Memory 骨架 | 未形成推荐闭环 |
| 资源关联 | 无 | HF 关联 Models/Datasets/Demos |
| 学者网络 | S2 客户端已有 | 未暴露 coauthor/引用网络 API |

---

## Phase 1 — 数据根基

> 不做这一步，后续所有功能（Trending/推荐/详情页/收藏）都没有数据基础。

### 1.1 Paper Registry（论文持久化）

- [x] 新增 `PaperModel` 表
  - 字段：`id`（自增）、`arxiv_id`（唯一索引）、`doi`、`title`、`authors_json`、`abstract`、`url`、`external_url`、`pdf_url`、`source`（papers_cool / arxiv_api / semantic_scholar）、`venue`、`published_at`、`first_seen_at`、`keywords_json`、`metadata_json`
  - 唯一约束：`(arxiv_id)` 或 `(doi)` 去重
  - 文件：`src/paperbot/infrastructure/stores/models.py`
- [x] 新增 Alembic 迁移
- [x] 新增 `PaperStore`（CRUD + upsert + 按 arxiv_id/doi 查重）
  - 文件：`src/paperbot/infrastructure/stores/paper_store.py`
- [x] `build_daily_paper_report()` 完成后自动入库
  - 文件：`src/paperbot/application/workflows/dailypaper.py`
  - 逻辑：遍历 `report["queries"][*]["top_items"]`，逐条 upsert
- [x] Judge 评分写入 `PaperModel` 或关联表 `paper_judge_scores`
  - 字段：`paper_id`、`query`、`overall`、`relevance`、`novelty`、`rigor`、`impact`、`clarity`、`recommendation`、`one_line_summary`、`scored_at`
- [x] `PaperFeedbackModel` 的 `paper_id` 关联到 `PaperModel.id`（目前 paper_id 是自由文本）
- [x] 论文 ID 归一化工具函数 `normalize_paper_id(url_or_id) -> arxiv_id | doi`
  - 文件：`src/paperbot/domain/paper_identity.py`

### 1.2 收藏 / 阅读列表

- [x] 新增 `PaperReadingStatusModel` 表（或扩展 `PaperFeedbackModel`）
  - 字段：`user_id`、`paper_id`、`status`（unread/reading/read/archived）、`saved_at`、`read_at`
- [x] API：`GET /api/research/papers/saved`（用户收藏列表，支持排序：judge_score / saved_at / published_at）
- [x] API：`POST /api/research/papers/{paper_id}/status`（更新阅读状态）
- [x] API：`GET /api/research/papers/{paper_id}`（论文详情，聚合 judge + feedback + summary）
- [x] 前端：收藏列表页面组件
  - 文件：`web/src/components/research/SavedPapersList.tsx`

### 1.3 GitHub Repo 关联（Repo Enrichment）

- [x] 新增 `PaperRepoModel` 表
  - 字段：`paper_id`、`repo_url`、`owner`、`name`、`stars`、`forks`、`last_commit_at`、`language`、`description`、`fetched_at`
- [x] Enrichment 服务：从论文 abstract/url/external_url 中提取 GitHub 链接并补元数据
  - 当前实现：`src/paperbot/api/routes/paperscool.py`（后续可下沉到 service）
  - 提取来源：`github_url/external_url/url/pdf_url/alternative_urls + snippet/abstract`
  - 调用 GitHub API 补元数据（stars/forks/language/updated_at）
- [x] DailyPaper 生成后异步调用 repo enrichment
- [x] API：`GET /api/research/papers/{paper_id}/repos`
- [x] API：`POST /api/research/paperscool/repos`（批量，含 stars/活跃度）

---

## Phase 2 — 体验提升

### 2.1 论文详情页

- [ ] 前端页面：`web/src/app/papers/[id]/page.tsx`
  - 基本信息卡片（标题/作者/venue/日期/链接）
  - AI Summary 区块
  - Judge 雷达图（复用现有 `JudgeRadarCard` 组件）
  - Trend 关联（该论文所属 query 的趋势分析）
  - Feedback 操作栏（like/save/cite/dislike）
  - 关联 GitHub Repo 卡片（stars/活跃度/语言）
  - 相似论文列表（Phase 3 实现后接入）
- [ ] API：`GET /api/research/papers/{paper_id}` 聚合返回上述所有数据

### 2.2 AI Chat with Paper

- [ ] API：`POST /api/research/papers/{paper_id}/ask`
  - 请求：`{ "question": "...", "user_id": "..." }`
  - 上下文构建：title + abstract + judge scores + ai_summary + keywords
  - 调用 `LLMService`（task_type=chat）
  - 返回：`{ "answer": "...", "context_used": [...] }`
  - 文件：`src/paperbot/api/routes/paper_qa.py`
- [ ] 前端：论文卡片/详情页的 "Ask AI" 按钮 → 对话弹窗
  - 文件：`web/src/components/research/PaperQADialog.tsx`
- [ ] 可选增强：如果有 PDF URL，抽取全文作为上下文（Phase 3）

### 2.3 富文本推送

- [x] HTML 邮件模板（BestBlogs 风格）
  - 文件：`src/paperbot/application/services/email_template.py`（共享模板）
  - 布局：本期导读 → 三步精选流程 → 分层推荐（Must Read / Worth Reading / Skim）
  - 每篇论文"方法大框"：研究问题 / 核心方法 / 关键证据 / 适用场景 / 创新点（从 Judge 五维 rationale 自动拼）
  - `_send_email()` 发送 `multipart/alternative`（text + html）
  - SMTP 和 Resend 两个渠道共享同一模板
- [ ] Slack Block Kit 消息
  - 将 `_send_slack()` 的 `text` payload 替换为 `blocks` 结构
  - 包含 Header Block + Section Blocks（论文卡片）+ Divider
- [ ] 钉钉 Markdown 优化
  - 优化 `_send_dingtalk()` 的 markdown 格式
  - 添加论文链接、评分标签

### 2.4 RSS / Atom Feed

- [ ] API：`GET /api/feed/rss`（最近 N 天的 DailyPaper 聚合为 RSS 2.0）
  - 参数：`days`（默认 7）、`query`（可选过滤）、`track_id`（可选）
  - 文件：`src/paperbot/api/routes/feed.py`
  - 依赖 Paper Registry（从 DB 读取论文列表）
- [ ] API：`GET /api/feed/atom`（Atom 1.0 格式）
- [ ] DailyPaper 生成后同时输出静态 RSS 文件到 `reports/feed.xml`

---

## Phase 3 — 智能推荐与学者网络

### 3.1 学者合作网络（Coauthor Graph）

- [x] API：`POST /api/research/scholar/network`
  - 参数：`scholar_id` 或 `scholar_name`、`max_papers`、`recent_years`、`max_nodes`
  - 返回：`{ "scholar", "stats", "nodes", "edges" }`
    - node：`{ "id", "name", "type", "collab_papers", "citation_sum" }`
    - edge：`{ "source", "target", "weight", "sample_titles" }`
  - 实现：调用 `SemanticScholarClient.get_author()` + `get_author_papers()` 聚合 coauthor 图
  - 文件：`src/paperbot/api/routes/research.py`
  - 基础设施：`src/paperbot/infrastructure/api_clients/semantic_scholar.py`
- [ ] 前端：学者关系图可视化（复用 xyflow / d3-force）
  - 文件：`web/src/components/research/ScholarNetworkGraph.tsx`

### 3.2 学者趋势分析

- [x] API：`POST /api/research/scholar/trends`
  - 参数：`scholar_id` 或 `scholar_name`、`max_papers`、`year_window`
  - 返回：
    - `publication_velocity`：`[{ "year", "papers", "citations" }]`
    - `topic_distribution` / `venue_distribution`
    - `trend_summary`：`{ "publication_trend", "citation_trend", "active_years" }`
  - 实现：从 S2 author papers 聚合统计并生成趋势方向
  - 文件：`src/paperbot/api/routes/research.py`
- [ ] 前端：学者趋势图表（年度发表量/引用趋势/主题变迁时间线）
  - 文件：`web/src/components/research/ScholarTrendsChart.tsx`

### 3.3 个性化推荐（Re-rank）

- [ ] 兴趣向量提取
  - 从用户 feedback（like/save 的论文 title+abstract）+ Track keywords 构建兴趣 embedding
  - 文件：`src/paperbot/application/services/interest_profile.py`
  - 复用 `ResearchTrackEmbeddingModel` 的 embedding 能力
- [ ] DailyPaper re-rank
  - 在 `build_daily_paper_report()` 之后、输出之前，用兴趣向量对 `global_top` 重排序
  - `personalized_score = base_score * 0.5 + interest_similarity * 0.3 + judge_overall * 0.2`
- [ ] API：`GET /api/research/recommended`（为你推荐，基于 Track + Feedback）
- [ ] 前端："为你推荐" 区块（DailyPaper 页面或独立页面）

### 3.4 论文 Trending

- [ ] Trending 评分公式
  - `trending_score = judge_overall * 0.4 + feedback_likes * 0.3 + recency_decay * 0.2 + repo_stars * 0.1`
  - `recency_decay = exp(-days_since_publish / 14)`
- [ ] API：`GET /api/research/trending`
  - 参数：`days`（默认 7）、`limit`（默认 20）、`track_id`（可选）
  - 依赖 Paper Registry + Judge 分数 + Feedback 数据
- [ ] 前端：Trending 页面
  - 文件：`web/src/app/trending/page.tsx`

### 3.5 相似论文推荐

- [ ] 论文 Embedding 存储
  - 新增 `PaperEmbeddingModel` 表（paper_id、model、embedding_json、dim）
  - DailyPaper 入库后异步计算 title+abstract embedding
- [ ] 相似度检索
  - 给定 paper_id → 取其 embedding → cosine similarity top-K
  - 文件：`src/paperbot/application/services/paper_similarity.py`
- [ ] API：`GET /api/research/papers/{paper_id}/similar`
- [ ] 前端：论文详情页 "Related Papers" 区块

### 3.6 订阅管理（Per-Topic / Per-Author）

- [ ] 新增 `SubscriptionModel` 表
  - 字段：`user_id`、`type`（keyword/author/venue）、`value`（具体关键词/作者 ID/venue 名）、`notify_channels`（email/slack/dingtalk）、`enabled`、`created_at`
- [ ] API：`POST /api/research/subscriptions`（创建订阅）
- [ ] API：`GET /api/research/subscriptions`（列出订阅）
- [ ] API：`DELETE /api/research/subscriptions/{id}`
- [ ] ARQ Cron：按订阅配置生成个性化 DailyPaper
  - 每个订阅独立生成搜索结果 → 合并 → 推送
  - 文件：`src/paperbot/infrastructure/queue/arq_worker.py`
- [ ] 有新匹配论文时触发推送（增量检测：对比上次推送的论文 ID 列表）

---


## Phase 3.7 — Agent Browser 自动化能力（基于 vercel-labs/agent-browser）

> 参考仓库：<https://github.com/vercel-labs/agent-browser>
> 目标：把"网页交互型"任务从静态 API 拉取升级为可观测、可回放的浏览器 Agent 流程。
> 定位：**仅用于复杂/高交互采集与回归验证场景**，默认流程保持现有 API connector，不强制改造。

### 4.1 Source Capture Agent（网页采集增强）

- [ ] 新增 Browser Source Runner：支持登录后抓取（HF Papers、arXiv、OpenReview）
  - 输入：source 配置、cookies/session、抓取策略
  - 输出：结构化 paper candidates + 抓取轨迹（screenshots + step logs）
- [ ] 新增反爬/失败回退链路
  - 失败后自动 fallback 到现有 API connector（papers.cool / arXiv API）
  - 记录 fallback 原因到 run metadata
- [ ] 新增 DOM 语义抽取模板
  - 把标题/作者/摘要/链接抽取规则模板化，支持 source 版本升级时快速修复

### 4.2 Workflow UX Agent（前端交互与 E2E 验证）

- [ ] 为 Search → DailyPaper → Analyze 流程增加 browser-driven E2E 回归
  - 覆盖 SSE 增量渲染（Judge/Trend/Insight）和 DAG 状态恢复
- [ ] 自动录制关键节点截图与性能指标
  - 首屏可见时间、分析阶段空白时长、首条增量结果时间（TTFR）
- [ ] 将 E2E 结果接入 CI artifacts
  - 每次 PR 自动上传步骤日志和失败页面快照

### 4.3 Community/Platform Agent（平台对标能力）

- [ ] 新增 HF/AlphaXiv 对标监测 Agent
  - 周期性抓取公开页面能力矩阵（发现/排序/交互/推送）
  - 生成差距报告写入 `docs/benchmark/`（markdown）
- [ ] 新增 Daily push 预览 Agent
  - 自动打开邮件/Slack/钉钉渲染预览页面并截图
  - 验证 BestBlogs 风格模板在多端一致性
- [ ] 新增 Browser Extension smoke test
  - 校验 arXiv 页面注入按钮、详情弹层、跳转链路

### 4.4 运维与安全

- [ ] 新增 Browser session 密钥管理
  - cookies/token 通过环境变量或密钥服务注入，禁止明文入库
- [ ] 新增 Agent 审计日志
  - 记录访问域名、操作步骤、耗时、失败原因（可用于问题追踪）
- [ ] 新增速率限制与并发隔离策略
  - 避免批量采集触发封禁，支持 source 级并发控制



## Phase 3.8 — Agent Runtime 统一（接口契约）

> 目标：把现有多套 Agent 调用方式收敛到统一契约，避免 API/Workflow 各写一套生命周期。

### 3.8.1 Agent 模块盘点（当前）

- [x] 盘点现有 Agent 入口与责任边界（形成清单）
  - API 直接调用：`analyze/review/track/research/gen_code`（5 个入口）
  - Agent 类：`src/paperbot/agents` 13 个业务 Agent（不含 Base），`src/paperbot/repro/agents` 4 个业务 Agent（不含 Base）
  - 输出文档：`docs/agent_inventory.md`
- [x] 标记哪些场景必须 Agent、哪些保持普通 service
  - 必须 Agent：复杂推理、长链路、多步工具调用
  - 非必须：纯 CRUD、低延迟同步 API、简单规则处理

### 3.8.2 契约抽象（必须先做）

- [x] 定义统一 `AgentRuntime` 接口
  - 生命周期：`input -> plan -> execute -> emit events -> finalize`
  - 文件：`src/paperbot/core/abstractions/agent_runtime.py`
- [x] 定义统一 `AgentMessage` / `AgentResult` / `AgentError` schema
  - 兼容当前 runbook/event_log 字段：`run_id`、`trace_id`、`stage`、`agent_name`
- [x] 定义 `SourceCollector` 接口（API Source / Browser Source 同契约）
  - 文件：`src/paperbot/application/ports/source_collector.py`

### 3.8.3 观测统一（SSE + 日志 + 事件）

- [x] 把 workflow SSE 事件映射到统一事件总线
  - Search/DailyPaper/Analyze/Judge/Trend 都输出同一 event envelope
- [x] 打通 `trace_id` 贯穿：API -> workflow -> agent -> store/event_log
- [x] 前端统一消费事件协议（避免每个页面写一套 event parser）

### 3.8.4 迁移顺序（按风险从低到高）

- [x] Step 1：`analyze` + `review` 接入 `AgentRuntime`（低耦合）
- [ ] Step 2：`track` + `research` 接入 `AgentRuntime`（中耦合）
- [ ] Step 3：`gen_code`（Paper2Code）迁移到统一 Runtime 适配层（高耦合）
- [ ] Step 4：为每步补回归测试 + runbook 对账测试

### 3.8.5 工程规则

- [ ] 执行策略：**每个 issue 对应 1 个 commit**（禁止跨 issue 混提）
- [ ] commit message 必须包含 issue 编号（如 `feat: xxx (#41)`）

---


## 多智能体系统现状与 OpenClaw 评估

### 现有多智能体管线

PaperBot 目前包含 **5 套多智能体系统**，覆盖学者追踪、Paper2Code、深度评审三大场景，以及底层协作框架。

#### A. 学者追踪管线（ScholarWorkflowCoordinator）

| 阶段 | Agent | 职责 |
|------|-------|------|
| 1 | ResearchAgent | 论文元数据、摘要、venue 分析 |
| 2 | CodeAnalysisAgent | GitHub 仓库健康度评估 |
| 3 | QualityAgent | 综合质量评估（结合 research + code） |
| 4 | InfluenceCalculator | PIS 影响力评分（引用速度、趋势动量） |
| 5 | ReportWriter | Markdown 报告生成 |

**协调机制**：
- `ScoreShareBus`：阶段间评分共享（pub/sub），后续阶段可读取前序评分
- `FailFastEvaluator`：基于阈值的早期中断（低质量论文跳过深度分析）
- `PipelineContext`：阶段间状态传递 dataclass
- 文件：`core/workflow_coordinator.py` + `core/collaboration/score_bus.py` + `core/fail_fast.py`

#### B. Paper2Code 管线（Orchestrator）

| 阶段 | Agent | 职责 |
|------|-------|------|
| 1 | PlanningAgent | Blueprint 蒸馏 + 计划生成 |
| 2 | CodingAgent | 代码生成（含 RAG + CodeMemory） |
| 3 | VerificationAgent | 语法/导入/测试验证 |
| 4 | DebuggingAgent | 错误修复（自愈调试循环） |

**协调机制**：
- `PipelineProgress`：阶段追踪 + 进度回调
- Repair Loop：验证失败 → 调试修复 → 重新验证，最多 `max_repair_loops`（默认 3）次
- 共享 `context` 字典：阶段间传递 plan/files/errors
- `ParallelOrchestrator`：独立阶段并行执行扩展
- 文件：`repro/orchestrator.py`

#### C. 深度评审管线（ReviewerAgent）

| 阶段 | 职责 |
|------|------|
| Preliminary Screening | 初筛：结构/完整性/基本质量 |
| Deep Critique | 深度批评：方法论/实验/贡献 |
| Final Decision | 最终决策：Accept/Reject + Novelty Score |

**协调机制**：单 Agent 内部 3 阶段顺序执行，每阶段独立 LLM 调用。
- 文件：`agents/review/agent.py`

#### D. 协作框架层

| 组件 | 职责 | 文件 |
|------|------|------|
| `AgentCoordinator` | Agent 注册、消息广播（pub/sub）、结果收集与合成 | `core/collaboration/coordinator.py` |
| `ScoreShareBus` | 阶段间评分共享、阈值判断、加权聚合 | `core/collaboration/score_bus.py` |
| `HostOrchestrator` | LLM 驱动的"主持人"，生成多 Agent 协作引导语 | `core/collaboration/host.py` |
| `AgentMessage` / `MessageType` | 统一消息信封（insight/result/question/error） | `core/collaboration/messages.py` |
| `BaseAgent` | Template Method 模式（validate→execute→post_process） | `agents/base.py` |

#### E. 独立 Agent 清单

| Agent | 用途 | 文件 |
|-------|------|------|
| SemanticScholarAgent | S2 API 论文/作者检索 | `agents/scholar_tracking/semantic_scholar_agent.py` |
| ScholarProfileAgent | 学者画像构建 | `agents/scholar_tracking/scholar_profile_agent.py` |
| PaperTrackerAgent | 论文动态追踪 | `agents/scholar_tracking/paper_tracker_agent.py` |
| DeepResearchAgent | 深度研究分析 | `agents/scholar_tracking/deep_research_agent.py` |
| ConferenceAgent | 顶会论文抓取 | `agents/conference/agent.py` |
| VerificationAgent | 声明验证 | `agents/verification/agent.py` |

### OpenClaw 迁移评估

**OpenClaw 核心定位**：个人 AI 助手框架（TypeScript/Node.js ≥22），通过 Gateway 控制面连接 WhatsApp/Telegram/Slack/Discord 等消息渠道，核心能力是消息路由 + Skills 插件系统 + 多渠道 Agent 路由。

**PaperBot 多智能体 vs OpenClaw 对比**：

| 维度 | PaperBot | OpenClaw |
|------|----------|----------|
| 语言 | Python（asyncio） | TypeScript（Node.js） |
| Agent 协作模式 | 顺序管线 + 评分共享 + 修复循环 | 消息渠道路由 + 独立 Agent 会话隔离 |
| Agent 间通信 | ScoreShareBus / AgentCoordinator（进程内 pub/sub） | Gateway WebSocket（跨进程，面向用户消息） |
| 编排粒度 | 细粒度阶段控制（FailFast/ScoreThreshold） | 粗粒度渠道→Agent 路由 |
| 扩展机制 | BaseAgent 子类化 + DI 容器 | Skills 插件 + MCP 工具 |
| 典型用途 | 领域特定管线（论文分析/代码生成） | 通用对话助手 + 工具调用 |

**结论：直接迁移不适合，但可作为接入层整合。**

OpenClaw 的"多 Agent"是指将不同消息渠道路由到不同 Agent 实例，而非 PaperBot 所需的多 Agent 协作编排（管线化、评分共享、修复循环）。PaperBot 的 ScholarWorkflowCoordinator 和 Paper2Code Orchestrator 需要的是 **任务级编排**（类似 LangGraph / CrewAI），而非 OpenClaw 的 **渠道级路由**。

### OpenClaw 整合方案（推荐）

将 PaperBot 作为 OpenClaw 的 **Skill 插件**接入，用户可以在 WhatsApp/Telegram/Slack 等渠道直接与 PaperBot 交互：

- [ ] 开发 OpenClaw Skill 包装层
  - 将 PaperBot REST API 封装为 OpenClaw Skill
  - 支持命令：`/search <query>`、`/daily`、`/judge <paper>`、`/track <scholar>`
  - 文件：`openclaw-skill/` 目录（TypeScript，独立包）
- [ ] Skill 调用 PaperBot API
  - 通过 HTTP 调用 PaperBot FastAPI 后端（`/api/research/paperscool/search` 等）
  - SSE 流式结果转换为 OpenClaw 消息分块输出
- [ ] 推送渠道对接
  - 复用 OpenClaw 的多渠道能力（WhatsApp/Telegram/Discord）替代 PaperBot 自有的 DailyPushService
  - DailyPaper 结果通过 OpenClaw Gateway 推送到用户的首选渠道
- [ ] OpenClaw Cron 集成
  - 使用 OpenClaw 的 Cron 系统触发 PaperBot 定时任务（替代或补充 ARQ Worker）

### 多智能体框架升级路线（替代方案）

如果目标是增强 PaperBot 自身的多智能体能力，更合适的方向是引入 Python 原生的 Agent 编排框架：

- [ ] 评估 LangGraph / CrewAI / AutoGen 作为编排层
  - LangGraph：图状态机，适合 Scholar Pipeline 的条件分支 + FailFast
  - CrewAI：角色化 Agent 协作，适合 Paper2Code 的规划→编码→验证流程
  - AutoGen：多 Agent 对话，适合 HostOrchestrator 的讨论式协作
- [ ] 统一 Agent 抽象层
  - 现有 `BaseAgent`（Template Method）+ `repro.agents.BaseAgent`（两套独立实现）需统一
  - 统一的 `AgentProtocol`：`run(context) -> AgentResult`
  - 统一的 Tool/Capability 注册（替代分散的 `capabilities` list）
- [ ] 可观测性增强
  - 现有 Phase-0 EventLog 扩展为完整 OpenTelemetry trace
  - 每个 Agent 调用自动生成 span（包含 LLM token usage、延迟、评分）
  - 文件：`core/observability/` 目录

---

## Phase 4 — 平台化能力（远期）

> 以下功能视需求和资源情况推进，不急于落地。

### 4.1 多用户体系

- [ ] 用户注册/登录（JWT / OAuth2）
- [ ] API Key 管理
- [ ] 用户隔离（所有查询带 user_id scope）

### 4.2 社区交互

- [ ] 论文评论系统（评论表 + API）
- [ ] Upvote 系统（基于 `PaperFeedbackModel.action = "upvote"`）
- [ ] 作者 Claiming（关联 S2 author ID 到用户账户）

### 4.3 浏览器集成

- [ ] Chrome 插件：arXiv 页面注入 PaperBot Judge 评分 + AI Summary
- [ ] URL 替换：`arxiv.org` → `paperbot.xxx/papers/arxiv_id`

### 4.4 全文 PDF 处理

- [ ] PDF 下载 + 文本提取
- [ ] 全文索引（用于 AI Chat with Paper 的深度问答）
- [ ] 逐段批注（类 AlphaXiv）

### 4.5 论文框架图提取（Paper Framework Figure Extraction）

> 目标：自动提取论文中的方法框架图（通常是 Figure 1），嵌入邮件推送和论文详情页。
> 三条提取路径 + LaTeX 快速通道，后续做 A/B Test 对比效果。

**路径 A：LaTeX 源码直提（arXiv 论文优先，精度最高）**

- [ ] arXiv 提供 LaTeX 源码包下载（`https://arxiv.org/e-print/{arxiv_id}`）
- [ ] 解压 `.tar.gz` → 解析 `.tex` 文件中的 `\includegraphics` 命令
- [ ] 定位框架图：匹配 `\begin{figure}` 环境 + caption 关键词（"overview"、"framework"、"architecture"、"pipeline"）
- [ ] 直接提取对应图片文件（`.pdf`/`.png`/`.eps`）→ 转换为 PNG/WebP
- 优点：无损质量、精准定位、无需模型推理
- 缺点：仅限 arXiv 有源码的论文（覆盖率 ~70-80%）

**路径 B：MinerU 文档布局检测（PDF 结构化解析，推荐）**

- [ ] 使用 [MinerU](https://github.com/opendatalab/MinerU)（30k+ stars）解析 PDF
  - LayoutLMv3 布局检测 → 自动识别 figure 区域 + 导出图片 + 关联 caption 文本
  - 输出 Markdown/JSON + 图片目录，figure 作为独立元素
- [ ] 遍历 MinerU 输出的 figure 列表 → 匹配 caption 关键词定位框架图
- [ ] 备选：[Docling](https://github.com/DS4SD/docling)（IBM 出品，结构化文档解析）或 [Marker](https://github.com/vikparuchuri/marker)（PDF→Markdown，速度快）
- 优点：从 PDF 内部结构提取原始图片（无损）、自动关联 caption、文档专用模型准确率高
- 缺点：需下载 LayoutLMv3 权重（~1.5GB），首次推理较慢

**路径 C：SAM 3 视觉语义分割（扫描版 PDF fallback）**

- [ ] 使用 [SAM 3](https://pyimagesearch.com/2026/01/26/sam-3-concept-based-visual-understanding-and-segmentation/)（Meta Segment Anything Model 3）
  - 支持 concept-based text prompt 分割：`"framework diagram"` / `"architecture overview figure"`
  - PDF 页面渲染为高 DPI 图片 → SAM 3 分割 → 裁剪导出
- [ ] 适用场景：扫描版 PDF（图片型，无内嵌矢量图）、MinerU 提取失败的 fallback
- 优点：不依赖 PDF 内部结构，纯视觉语义理解，对扫描件友好
- 缺点：图片质量受渲染 DPI 影响（有损）、拿不到 caption 文本、模型较重（ViT-H）

**路径 D：PyMuPDF 轻量启发式（兜底方案）**

- [ ] PyMuPDF（fitz）直接提取 PDF 内嵌位图
- [ ] 启发式定位：页面位置（前 3 页）+ 图片尺寸（宽度 > 页面 50%）+ 周围文本匹配（"Figure 1"）
- [ ] 可选：LLM 视觉模型辅助判断（传入候选图片 → 判断哪张是框架图）
- 优点：零模型依赖、速度极快
- 缺点：矢量图可能提取为空、启发式规则覆盖率有限

**提取策略（级联 fallback）：**

```
LaTeX 源码可用？ ──是──→ 路径 A（LaTeX 直提）
       │否
       ▼
MinerU 提取成功？ ──是──→ 路径 B（布局检测）
       │否
       ▼
扫描版 PDF？ ──是──→ 路径 C（SAM 3 分割）
       │否
       ▼
路径 D（PyMuPDF 启发式兜底）
```

**A/B Test 计划：**

- [ ] 收集 100 篇论文样本（50 有 LaTeX 源码 + 50 仅 PDF）
- [ ] 人工标注 ground truth（每篇论文的框架图是哪张）
- [ ] 对比指标：提取成功率、图片质量（SSIM）、定位准确率、耗时
- [ ] 确定生产环境的级联策略和各路径权重

**通用后处理：**

- [ ] 图片压缩 + 尺寸归一化（邮件内嵌 ≤600px 宽）
- [ ] 上传到对象存储（S3/R2）或 base64 内嵌邮件
- [ ] 缓存：`PaperModel` 新增 `framework_figure_url` 字段
- [ ] 邮件模板集成：在方法大框下方展示框架图缩略图

**文件规划：**

- [ ] `src/paperbot/application/services/figure_extractor.py` — 统一入口 + 级联调度
- [ ] `src/paperbot/application/services/extractors/latex_extractor.py` — 路径 A
- [ ] `src/paperbot/application/services/extractors/mineru_extractor.py` — 路径 B
- [ ] `src/paperbot/application/services/extractors/sam3_extractor.py` — 路径 C
- [ ] `src/paperbot/application/services/extractors/pymupdf_extractor.py` — 路径 D
- [ ] 依赖：`magic-pdf`（MinerU）、`segment-anything-3`、`PyMuPDF`、`tarfile`、`Pillow`

---

## 实现依赖关系

```
Phase 1.1 (Paper Registry)
  ├── Phase 1.2 (收藏/阅读列表)
  ├── Phase 1.3 (Repo Enrichment)
  ├── Phase 2.1 (论文详情页)
  ├── Phase 2.2 (AI Chat with Paper)
  ├── Phase 2.4 (RSS Feed)
  ├── Phase 3.4 (Trending)
  └── Phase 3.5 (相似论文)

Phase 3.1 (学者网络) ── 独立，仅依赖 S2 客户端（已有）
Phase 3.2 (学者趋势) ── 独立，仅依赖 S2 客户端 + TrendAnalyzer（已有）
Phase 2.3 (富文本推送) ── 独立，仅依赖现有 DailyPushService
Phase 3.3 (个性化推荐) ── 依赖 Paper Registry + Embedding
Phase 3.6 (订阅管理) ── 依赖 Paper Registry + DailyPushService

OpenClaw Skill ── 独立，仅依赖 PaperBot REST API（已有）
多智能体框架升级 ── 独立，影响 core/collaboration/* + agents/*
```

## 涉及文件索引

| 文件 | 改动类型 | 关联 Phase |
|------|---------|-----------|
| `src/paperbot/infrastructure/stores/models.py` | 新增 Model | 1.1, 1.2, 1.3, 3.5, 3.6 |
| `src/paperbot/infrastructure/stores/paper_store.py` | **新建** | 1.1 |
| `src/paperbot/domain/paper_identity.py` | **新建** | 1.1 |
| `src/paperbot/application/workflows/dailypaper.py` | 修改 | 1.1, 3.3 |
| `src/paperbot/application/services/repo_enrichment.py` | **新建** | 1.3 |
| `src/paperbot/application/services/scholar_trends.py` | **新建** | 3.2 |
| `src/paperbot/application/services/interest_profile.py` | **新建** | 3.3 |
| `src/paperbot/application/services/paper_similarity.py` | **新建** | 3.5 |
| `src/paperbot/application/services/daily_push_service.py` | 修改 | 2.3 |
| `src/paperbot/application/services/templates/daily_email.html` | **新建** | 2.3 |
| `src/paperbot/api/routes/paper_qa.py` | **新建** | 2.2 |
| `src/paperbot/api/routes/scholar.py` | **新建** | 3.1, 3.2 |
| `src/paperbot/api/routes/feed.py` | **新建** | 2.4 |
| `src/paperbot/api/routes/paperscool.py` | 修改 | 1.1, 1.3 |
| `src/paperbot/api/routes/research.py` | 修改 | 1.2, 3.4 |
| `src/paperbot/infrastructure/api_clients/semantic_scholar.py` | 修改 | 3.1 |
| `src/paperbot/infrastructure/queue/arq_worker.py` | 修改 | 3.6 |
| `web/src/app/papers/[id]/page.tsx` | **新建** | 2.1 |
| `web/src/app/trending/page.tsx` | **新建** | 3.4 |
| `web/src/components/research/PaperQADialog.tsx` | **新建** | 2.2 |
| `web/src/components/research/SavedPapersList.tsx` | **新建** | 1.2 |
| `web/src/components/research/ScholarNetworkGraph.tsx` | **新建** | 3.1 |
| `web/src/components/research/ScholarTrendsChart.tsx` | **新建** | 3.2 |
| `openclaw-skill/` | **新建** | OpenClaw Skill |
| `src/paperbot/core/collaboration/coordinator.py` | 重构 | 多智能体升级 |
| `src/paperbot/core/collaboration/score_bus.py` | 重构 | 多智能体升级 |
| `src/paperbot/agents/base.py` | 重构 | 多智能体升级 |

---

## 现有可复用基础

| 已有能力 | 文件 | 可用于 |
|---------|------|-------|
| S2 作者/论文/引用 API | `infrastructure/api_clients/semantic_scholar.py` | 学者网络、学者趋势 |
| LLM Trend Analyzer | `application/workflows/analysis/trend_analyzer.py` | 学者主题迁移分析 |
| Paper Judge（5 维评分） | `application/workflows/analysis/paper_judge.py` | Trending 评分、详情页 |
| Track + Memory + Feedback | `api/routes/research.py` + stores | 个性化推荐 |
| Track Embedding | `infrastructure/stores/models.py:ResearchTrackEmbeddingModel` | 兴趣向量、相似论文 |
| DailyPushService | `application/services/daily_push_service.py` | 富文本推送 |
| LLMService | `application/services/llm_service.py` | AI Chat with Paper |
| XYFlow DAG | `web/src/components/research/TopicWorkflowDashboard.tsx` | 学者网络图 |
| AgentCoordinator | `core/collaboration/coordinator.py` | 多智能体消息协调 |
| ScoreShareBus | `core/collaboration/score_bus.py` | 管线阶段间评分共享 |
| ScholarWorkflowCoordinator | `core/workflow_coordinator.py` | 5 阶段学者分析管线 |
| Paper2Code Orchestrator | `repro/orchestrator.py` | 4 Agent 编排 + 修复循环 |
| BaseAgent (Template Method) | `agents/base.py` | Agent 通用抽象 |

---

## Progress Log

> 每次完成请追加一行：日期 + 简述 + 关联文件

- 2025-02-10: 创建 ROADMAP_TODO.md，完成对标分析与功能规划
- 2025-02-10: 新增多智能体系统现状盘点（5 套管线 + 15 个 Agent）与 OpenClaw 迁移评估
- 2026-02-11: 对齐远端 `origin/master` 的 Harvest 基线，保留旧实现到 `backup/feat-dailypaper-sse-stream-pre-harvest-20260211`
- 2026-02-11: 新增 Phase 4（Agent Browser 自动化）任务清单，覆盖采集、E2E、对标监测、安全与限流
- 2026-02-11: 完成 Phase 1 收尾（PaperRepoModel + /papers/{paper_id}/repos + DailyPaper 异步 repo enrichment），并修复 harvest 基线下 paper store 兼容性
- 2026-02-11: 新增 Phase 3.8 Agent Runtime 统一 TODO（契约/事件总线/迁移顺序），并明确 1 issue = 1 commit 规则
- 2026-02-11: 完成 Issue #44（Agent inventory + 边界决策文档），新增 `docs/agent_inventory.md`
- 2026-02-11: 完成 Issue #45（AgentRuntime/SourceCollector 契约 + 兼容适配器 + contract tests）
- 2026-02-11: 完成 Issue #46（SSE envelope 统一 + trace_id 贯穿 + 前端 normalize parser）
- 2026-02-11: 完成 Issue #47（analyze/review 路由迁移到 AgentRuntime，保持 SSE 兼容）
