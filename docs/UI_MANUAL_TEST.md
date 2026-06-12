# PaperBot Web UI 手工测试手册
## Web UI Manual Test Plan

| Field | Value |
|---|---|
| Document ID | PB-UIT-2026-001 |
| Version | 1.0 |
| Last Updated | 2026-04-17 |
| Scope | `web/` Next.js 前端全部用户可见功能 |
| Audience | 测试人员 / 产品 / 开发自测 |

---

## 1. 如何使用本手册

1. 按章节顺序从上到下做一遍 = **完整回归**。
2. 发现 bug 时，把失败的 `TC-UI-XXX` 编号连同截图贴到 issue 里。
3. 每个用例以"用户场景 + 操作 + 预期"三段式呈现，仿 Apple / Google QA 规范：
   - **Scenario** — 这个功能为谁、为什么存在
   - **Precondition** — 测试前必须的状态
   - **Steps** — 鼠标/键盘可复现操作
   - **Expected** — 肉眼应当看到的画面
   - **Pass / Fail** — 一句话判定
4. 优先级：
   - **P0** 核心路径，必过
   - **P1** 常用功能，应过
   - **P2** 次要路径，尽量过

---

## 2. 测试准备

### 2.1 环境启动

打开两个终端：

| 终端 | 命令 | 用途 |
|---|---|---|
| A | `python -m uvicorn src.paperbot.api.main:app --reload --port 8000` | 后端 API |
| B | `cd web && npm run dev` | 前端（默认 http://localhost:3000） |
| C (可选) | `arq paperbot.infrastructure.queue.arq_worker.WorkerSettings` | 异步任务 |

### 2.2 测试账号

- 打开 http://localhost:3000/login
- 用注册好的账号登录，如未注册 → /register
- 登录后应跳转到 `/dashboard`
- 右上角应显示头像和用户名

### 2.3 浏览器

推荐 **Chrome 最新版** + 屏幕分辨率 ≥ 1440×900。同时在 Safari、Firefox 各抽查一个 P0 用例。

---

## 3. 全局导航 (Sidebar)

### TC-UI-NAV-001 · 侧边栏九大模块跳转

**Priority**: P0

**Scenario**: 作为任何用户，我希望任何时刻都能通过左侧栏一键切换到 9 个核心模块，且当前页的图标高亮。

**Steps**:
1. 登录后依次点击侧边栏：Dashboard → Research → Signals → Scholars → Papers → Skills → DeepCode Studio → Wiki → Settings
2. 观察每次点击后的 URL 与高亮状态

**Expected**:
| 点击项 | URL | 当前项高亮 |
|---|---|---|
| Dashboard | `/dashboard` | ✅ |
| Research | `/research` | ✅ |
| Signals | `/signals` | ✅ |
| Scholars | `/scholars` | ✅ |
| Papers | `/papers` | ✅ |
| Skills | `/skills` | ✅ |
| DeepCode Studio | `/studio` | ✅ |
| Wiki | `/wiki` | ✅ |
| Settings | `/settings` | ✅ |

- 每页首次加载 < 3 秒（本地开发忽略冷启动）
- 没有 404 / 500 页面
- 没有控制台 `console.error`

**Pass**: 9 个链接全部跳转成功且高亮正确

---

## 4. Dashboard（每日总览）

### TC-UI-DASH-001 · 首屏信息完整

**Priority**: P0

**Scenario**: 我每天早上打开 PaperBot 首先看这页，希望一屏内看到：今日摘要、待读队列、关注学者动向、会议截止雷达、工作流入口。

**Steps**:
1. 进入 `/dashboard`
2. 不滚动，观察首屏

**Expected** — 按从上到下顺序应看到：
- [ ] 欢迎/用户卡片（含用户名）
- [ ] Stats Bar：今日论文数 / Judge 数 / 订阅者数等数字
- [ ] Reading Queue 面板：≥ 1 条"待读论文"
- [ ] Track Spotlight：当前激活 Track 的简介 + 本周热度
- [ ] Workflow Dock：显示"Analyze & Dispatch"、"Newsletter"等快捷卡片
- [ ] Deadline Radar：即将截止的 3 个会议
- [ ] Scholar Signals：关注学者最新论文

**Pass**: 上述 7 个区块均渲染且无 "Loading..." 卡住超 5 秒

---

### TC-UI-DASH-002 · Deadline Radar 显示与匹配

**Priority**: P1

**Scenario**: 我 track 里写了关键词 `llm alignment`，希望 Radar 里命中的会议有视觉高亮（如徽标 "Matched"）。

**Precondition**: 至少创建一个 Track 且关键词含 `llm`（参见 TC-UI-RES-002）

**Steps**:
1. Dashboard 底部找到 "Deadline Radar" 卡片
2. 检查每条会议卡片：名称、CCF 等级徽章（A/B）、剩余天数、关键词 tag

**Expected**:
- 至少看到 `KDD 2026 / ACL 2026 / CVPR 2026 / NeurIPS 2026` 其中两个
- 每条显示 `CCF-A` 或 `CCF-B` 彩色徽章
- 倒计时形如 "12 days left"
- 与 track 关键词命中的条目有视觉强调（边框 / Matched 标签）
- 点击会议名称在新标签页打开官网

**Pass**: 至少 3 条会议卡片齐全 + 匹配视觉生效 + 跳转正常

**Known Gap**: 会议数据是硬编码 7 条，不是实时抓取。

---

### TC-UI-DASH-003 · Reading Queue → Analyze 按钮

**Priority**: P0

**Scenario**: 我在队列里看到一篇感兴趣的论文，点 "Analyze" 直接进入分析流程。

**Steps**:
1. Dashboard 的 Reading Queue 面板，任选一条
2. 点击行末的 **Analyze** 按钮

**Expected**:
- 跳转到该论文详情页 `/papers/<id>` 或触发 SSE 分析面板
- 分析流程出现"进行中"状态指示
- 控制台无报错

**Pass**: 点击后有分析进度反馈，无白屏

---

## 5. Research（研究轨迹）

### TC-UI-RES-001 · 创建 Track

**Priority**: P0

**Scenario**: 作为新用户，我要开启一个研究方向 "RAG for code generation"，设置关键词、venues 和阈值。

**Steps**:
1. 进入 `/research`
2. 右上角点击 **"New Track"** 或 **"+"** 按钮
3. 填写：
   - Name: `RAG for code generation`
   - Keywords: `retrieval augmented generation, code generation, repository context`
   - Venues (可选): `NeurIPS, ICLR, ACL`
4. 点 **Create**

**Expected**:
- Modal 关闭
- Track 列表顶部出现刚创建的 Track，带"Active"标记
- 右上角 TrackSelector / Pills 显示新名称

**Pass**: Track 成功创建并默认激活

---

### TC-UI-RES-002 · Search 多源论文搜索

**Priority**: P0

**Scenario**: 我在激活的 Track 下搜索 `retrieval augmented generation`，期望结果来自 arXiv / Semantic Scholar / OpenAlex / HuggingFace 多个源，相同论文被合并。

**Steps**:
1. `/research` → **Search** 入口（或 SearchBox）
2. 输入 `retrieval augmented generation`
3. 回车或点搜索按钮

**Expected**:
- 下方出现结果列表（≤ 20 条）
- 每条卡片显示：标题、作者、发表年、来源徽章（如 `arXiv | S2`）
- 至少 1 条有多个来源徽章 → 证明发生过去重合并
- 列表按相关性降序（顶部评分最高）
- 支持排序/筛选下拉菜单

**Pass**: 结果出现 + 多源合并可见 + 排序正确

---

### TC-UI-RES-003 · Saved 标签：导出论文库

**Priority**: P0

**Scenario**: 我要把 saved 列表里 10 篇论文导出成 BibTeX 发给导师，或 CSL-JSON 导入 Zotero。

**Precondition**: Saved 列表里已有 ≥ 2 篇论文

**Steps**:
1. `/research` → 切到 **Saved** Tab
2. 用 checkbox 勾选 2-3 篇
3. 点右上角 **Export (N)** 下拉
4. 依次尝试 `BibTeX / RIS / Markdown / Zotero (CSL-JSON)` 四项

**Expected**:
- 点击 BibTeX 下载 `.bib` 文件，用文本编辑器打开有 `@article{...}` 条目
- 点 RIS 下载 `.ris`，内容以 `TY -` 开头
- 点 Markdown 下载 `.md`，是带链接的列表
- 点 Zotero 下载 `.csl.json`，合法 JSON 数组

**Pass**: 四种格式均下载成功且内容合法

---

### TC-UI-RES-004 · Saved 标签：BibTeX 导入

**Priority**: P1

**Scenario**: 同事发我 20 条 BibTeX，我要批量导入当前 Track。

**Steps**:
1. Saved Tab → 点 **Import** 按钮（图标为上传箭头）
2. 弹出 "Import BibTeX" 对话框
3. 粘贴 ≥ 2 条 BibTeX 文本
4. 确认 `Track name` 为当前激活 Track
5. 点 **Import**

**Expected**:
- 对话框显示结果提示："Imported N, Duplicates M, Failed 0"
- 关闭对话框后列表刷新，新增条目可见
- 重复 BibTeX 不会被二次入库

**Pass**: 导入统计正确 + 列表刷新可见

---

### TC-UI-RES-005 · Zotero 双向同步

**Priority**: P1

**Scenario**: 我 Zotero 里有一个 `PaperBot-Sync` collection，想拉到 PaperBot；之后把这里标注的论文推回去。

**Precondition**: 持有 Zotero API Key + library ID

**Steps**:
1. Saved Tab → 点 **Zotero** 按钮
2. 对话框选择 **Pull** 模式
3. 填 Library Type（user/group）、Library ID、API Key
4. 勾选 **Dry Run** 试跑 → 点 **Pull**
5. 查看预览 → 取消 Dry Run → 再次 Pull
6. 切换到 **Push** → 勾选若干论文 → 推回

**Expected**:
- Dry Run 输出 "Would import N papers"，不实际写库
- 正式 Pull 后列表出现来自 Zotero 的论文
- Push 后在 Zotero 客户端能看到新 items

**Pass**: Pull / Push 双向都成功 + Dry Run 生效

---

### TC-UI-RES-006 · Related Work 草稿生成

**Priority**: P1

**Scenario**: 我选中 15 篇相关论文，让系统自动生成一段 Related Work Markdown 草稿，含 `[Author Year]` 引用。

**Steps**:
1. Saved Tab → 勾选 ≥ 5 篇论文
2. 点 **Related Work** 按钮
3. 弹出对话框，输入 Topic: `retrieval augmented generation`
4. 点 **Generate**（或回车）

**Expected**:
- 底部出现生成进度指示
- 几秒后显示 Markdown 草稿预览
- 文本含多处 `[Author Year]` 格式引用（如 `[Lewis 2020]`）
- 提供 "Copy" / "Download" 按钮

**Pass**: 生成文本 > 200 字 + 含 ≥ 3 个规范引用

**Known Gap**: LLM 可能产生引用幻觉，需人工核对作者年份。

---

### TC-UI-RES-007 · Memory 标签：记忆系统

**Priority**: P0

**Scenario**: 我在对话里说过"我偏好 PyTorch 而非 JAX"，下次提问时希望助手自动带上这条上下文。

**Steps**:
1. `/research` → **Memory** Tab
2. 点 **Add Memory** 或 **+**
3. 选 Kind = `preference`，内容 `偏好 PyTorch 而非 JAX`，Scope = `global`
4. 保存后，在搜索框或 context 预览里输入 `训练框架`
5. 观察是否命中该记忆

**Expected**:
- 记忆卡片出现在列表
- 检索 `训练框架` 时该条目出现在结果顶部
- 删除按钮（垃圾桶）可移除；删除后刷新再搜，条目消失

**Pass**: 添加 + 检索命中 + 删除 三步完整

---

## 6. Signals（学者/主题动向）

### TC-UI-SIG-001 · Signals Workspace 浏览

**Priority**: P1

**Scenario**: 我希望在一个看板上同时看到"关注学者新论文、热门话题、评分雷达"。

**Steps**:
1. 进入 `/signals`
2. 滚动整页

**Expected**:
- 顶部有时间范围筛选器（7d / 30d / 90d）
- 至少出现：学者信号卡片、PIS 影响力雷达图、趋势词云或热词表
- 切换时间范围后数据刷新

**Pass**: 页面结构完整 + 时间筛选联动

---

## 7. Scholars（学者订阅）

### TC-UI-SCH-001 · Watchlist 浏览与添加

**Priority**: P0

**Scenario**: 我要添加 "Andrej Karpathy" 到学者关注列表，并看到他最近论文。

**Steps**:
1. 进入 `/scholars`
2. 顶部输入框输入 `Andrej Karpathy` 或 ORCID
3. 点 **Add** / **Follow**
4. 列表出现该学者后点击其头像/姓名

**Expected**:
- Watchlist 新增学者卡片（头像 / 最新 paper 计数 / PIS 分数）
- 点击后进入 `/scholars/<id>` 详情页
- 详情页显示：H-index、近 5 篇论文、引用速度图、趋势动量

**Pass**: 添加成功 + 详情页图表渲染

---

### TC-UI-SCH-002 · PIS 影响力雷达

**Priority**: P1

**Scenario**: 在学者详情页我希望看到 PIS 综合得分 + 两个因子（citation velocity / trend momentum）的可视化。

**Steps**:
1. 在 `/scholars/<id>` 页面找到 **ImpactRadar** / **VelocityChart**
2. 悬停图表查看 tooltip

**Expected**:
- 雷达/条形图显示 PIS 总分
- 子因子：citation velocity (近 30/90 天引用速率)、trend momentum (主题热度走势)
- Hover 显示精确数值

**Pass**: 图表渲染 + hover 正常

---

## 8. Papers（论文库）

### TC-UI-PAP-001 · 论文详情页

**Priority**: P0

**Scenario**: 从任意卡片点进论文，希望看到：标题、作者、摘要、TLDR、Judge 五维评分、Analyze/Review 入口。

**Steps**:
1. `/papers` 列表点任一卡片
2. 进入 `/papers/<id>`

**Expected**:
- 标题、作者、venue、year
- Abstract 折叠/展开
- 若已 Judge，显示 `JudgeRadarChart`（relevance/novelty/rigor/impact/clarity 五维）
- SentimentChart（舆情曲线）
- 按钮：`Analyze` / `Review` / `Generate Code` / `Save` / `Related Repos`

**Pass**: 核心区块齐全 + 五维雷达可见（若已评分）

---

### TC-UI-PAP-002 · 触发五维 Judge

**Priority**: P0

**Scenario**: 该论文还没评分，我点 **Judge** 让系统跑一次五维 + 多轮校准。

**Steps**:
1. 详情页点 **Judge** / **Analyze**
2. 观察进度指示

**Expected**:
- 按钮变 loading
- 5-30s 内返回五个维度分数（1-5 分）和简短理由
- 雷达图从空变填充
- rationale 含 "Median-calibrated from multiple judge runs."

**Pass**: 五维数值返回 + 雷达刷新

---

### TC-UI-PAP-003 · Deep Review 三阶段

**Priority**: P1

**Scenario**: 我要让 AI 模拟审稿人，对这篇做 Preliminary Screening → Deep Critique → Final Decision 三阶段评审。

**Steps**:
1. 详情页点 **Review** / **Deep Review**
2. 观察流式面板

**Expected**:
- 依次出现三段：
  - `Preliminary Screening`（scope/quality 概览）
  - `Deep Critique`（strengths / weaknesses / methodology）
  - `Final Decision`（Overall Score 1-10 + Accept/Reject/Borderline + Confidence）
- 每阶段流式输出，不是一次性弹全部

**Pass**: 三阶段可见 + 最终 verdict 字段齐全

---

## 9. Skills

### TC-UI-SKL-001 · Skill 目录浏览

**Priority**: P2

**Steps**:
1. `/skills` 列表页
2. 点任一 skill 卡片

**Expected**:
- 列表显示 skill 名称、描述、来源（`claude_code` / `opencode` / `github_copilot`）
- 详情显示 skill 元数据

**Pass**: 列表和详情均渲染

**Known Gap**: "Claude Code 集成"目前只是 `.claude/skills` 目录发现，不是完整 SDK 接入。

---

## 10. DeepCode Studio

### TC-UI-STU-001 · Agent Board 首屏

**Priority**: P0

**Scenario**: 我要开始一个 Paper2Code 会话，Studio 应提供多面板布局：左侧代理列表、中间画布、右侧文件/日志。

**Steps**:
1. 进入 `/studio`
2. 观察布局

**Expected**:
- 三栏布局：Sidebar (agents) / Canvas (DAG) / Right panel (files / logs / tasks)
- 顶部有 session 选择器 + **Run All (N)** 按钮（N = 可执行节点数）

**Pass**: 三栏齐全 + Run All 按钮显示

---

### TC-UI-STU-002 · 端到端 Run（Paper2Code 五阶段）

**Priority**: P0

**Scenario**: 我要把一篇论文跑通 Paper2Code 完整五阶段：Planning → Blueprint → Env → Generation → Verification。

**Precondition**: 已 link 一篇论文到 session（通过 NewPaperModal）

**Steps**:
1. 点 **Run All**
2. 弹出 "End-to-End Execution" 对话框，确认 workspace 路径
3. 确认后观察画布

**Expected**:
- 画布节点依次变色：pending → running (蓝) → done (绿) / failed (红)
- 阶段顺序：planning → blueprint → environment → generation → verification
- 右侧 ExecutionLog 实时输出
- 某节点失败时可点重试
- 完成后 Run All 按钮变成"Completed"

**Pass**: 五阶段完整跑完 + 有可见进度

**Known Gap**: 缺少 libpango 时 PDF 报告生成会失败，但执行主流程不受影响。

---

### TC-UI-STU-003 · Runbook 文件面板

**Priority**: P0

**Scenario**: 跑完一轮后我要浏览生成的代码文件、看修改 diff、出问题时回滚快照。

**Steps**:
1. 右侧切到 **Files** 面板
2. 点任一文件打开（Monaco 编辑器）
3. 修改内容后观察 Changes 计数
4. 点 **Snapshot** 创建快照
5. 再改几行 → 点 **Diff** 查看对比
6. 点 **Revert** 回滚

**Expected**:
- 文件树可展开/折叠
- 编辑器支持语法高亮
- Changes 计数器随编辑增减
- Snapshot 创建成功有 toast 提示
- Diff 视图显示 +/- 行对比
- Revert 后编辑器内容回到快照时刻

**Pass**: 读/写/快照/diff/回滚 五步全通

---

### TC-UI-STU-004 · Sandbox 执行 + 资源监控

**Priority**: P1

**Scenario**: 代码生成后在沙盒里运行，希望看到 CPU/内存实时曲线与 stdout 流。

**Steps**:
1. 触发一次 Run 后切到 **Execution Log** / **Reproduction Log**
2. 观察日志是否滚动输出
3. 切到资源面板（若有 Metrics / Monitor）

**Expected**:
- 日志每秒更新，无延迟堆积
- CPU / Memory 曲线每秒 1 帧
- 任务完成后日志保持可回看
- 可以 **Cancel** / **Retry** 运行中的作业

**Pass**: 日志流 + 指标流 + Cancel/Retry 全可用

---

### TC-UI-STU-005 · AgentBoard for Paper（`/studio/agent-board/<paperId>`）

**Priority**: P1

**Scenario**: 从论文详情页点 "Open in Studio"，直接进入该论文的 AgentBoard 会话。

**Steps**:
1. 论文详情页点相应入口
2. 跳转 `/studio/agent-board/<paperId>`

**Expected**:
- Board 已自动关联该论文
- Papers 面板显示当前论文卡片
- 可直接 Run All

**Pass**: 关联关系正确 + 无重复 session 残留

---

## 11. Wiki

### TC-UI-WIKI-001 · 概念浏览

**Priority**: P2

**Steps**:
1. 进入 `/wiki`
2. 浏览概念列表 → 点任一条目

**Expected**:
- 概念详情显示：定义、相关论文、相关 tracks
- 搜索框可按关键词过滤

**Pass**: 浏览 + 搜索工作

---

## 12. Settings

### TC-UI-SET-001 · Account 标签

**Priority**: P0

**Steps**:
1. `/settings` → **Account** Tab
2. 修改 Display Name → Save
3. 重新登录，确认 name 已更新

**Expected**:
- 表单保存成功有 toast
- Email 只读、不可修改
- 修改密码：需先输当前密码，两次新密码一致
- 有 Sign Out 和 Delete Account 两个危险操作入口

**Pass**: 改名生效 + 改密码生效

---

### TC-UI-SET-002 · Daily Brief 配置

**Priority**: P0

**Scenario**: 我要让每日简报在 08:00 发到我邮箱 + Slack 频道。

**Steps**:
1. Settings → **Daily Brief** Tab
2. 填 Recipient: `你的邮箱`
3. Output directory: `./reports/dailypaper`
4. 切 **Artifact persistence** 开关 → On
5. 保存

**Expected**:
- 开关有动效，Save 后 toast "Saved"
- 刷新页面设置保留

**Pass**: 配置持久化

---

### TC-UI-SET-003 · Model Providers 配置

**Priority**: P0

**Steps**:
1. Settings → **Model Providers** Tab
2. 填入 OPENAI_API_KEY / ANTHROPIC_API_KEY（或其他 provider）
3. 点 **Test Connection**

**Expected**:
- 测试按钮变 loading → 返回 ✅ `Connection OK` 或 ❌ 错误信息
- 保存后 Key 以 `sk-****last4` 脱敏显示

**Pass**: 连通性测试 + 脱敏显示都工作

---

### TC-UI-SET-004 · Scholar Subscriptions（如存在）

**Priority**: P1

**Steps**:
1. Settings 页若看到 **Scholar Subscriptions** 卡片，尝试增删一个订阅
2. 回到 `/scholars` 确认变化

**Expected**:
- 增加后 Watchlist 出现、删除后消失

**Pass**: 增删与主视图同步

---

## 13. 推送渠道（手动冒烟）

### TC-UI-PUSH-001 · 七渠道测试发送

**Priority**: P1

**Scenario**: 管理员希望验证 Telegram / Slack / Discord / Feishu / WeCom / DingTalk / Email 七个渠道配置正确。

**Precondition**:
- `.env` 或 `config/config.yaml` 中已配置各渠道 webhook / token
- ARQ worker 运行

**Steps**（每个渠道一次）：
1. 在 Dashboard 的 **Workflow Dock** 卡片点 **Test Push**（若无按钮则用 curl 走后端）
2. 等待目标应用出现测试消息

**Expected**:
- 每个渠道收到一条测试消息，含标题 + 论文条目 + 评分
- 消息格式符合该平台（如 Slack 的 Block Kit、Telegram 的 Markdown）

**Pass**: 至少配置过的 ≥ 2 个渠道收到消息

---

## 14. 错误与边界场景

### TC-UI-ERR-001 · 未登录访问保护页

**Priority**: P0

**Steps**:
1. 登出
2. 地址栏直接访问 `/dashboard` / `/studio`

**Expected**:
- 被重定向到 `/login`
- 登录后自动回到原来要访问的页面

**Pass**: 保护路由生效

---

### TC-UI-ERR-002 · 后端不可用时的降级

**Priority**: P1

**Steps**:
1. 前端保持打开
2. 关闭后端 uvicorn
3. 继续点击各页面

**Expected**:
- 每页显示明确错误提示（非白屏）
- 控制台看到 fetch 失败，但 UI 不崩溃
- 重启后端后页面可恢复（可能需要刷新）

**Pass**: 降级而非崩溃

---

### TC-UI-ERR-003 · 空状态（Empty State）

**Priority**: P2

**Steps**:
1. 新账号首次登录（或删光所有 Track / Paper / Memory）
2. 遍历 Research / Papers / Scholars / Memory 四处

**Expected**:
- 每处都显示引导式空状态：插图 / 说明 / 主 CTA 按钮（如"创建第一个 Track"）
- 不是空白 div

**Pass**: 4 个空态页各有对应插画/文案

---

## 15. 浏览器兼容性（抽查）

### TC-UI-BRO-001 · Safari / Firefox 冒烟

**Priority**: P2

**Steps**: 在 Safari 和 Firefox 各跑一遍 **TC-UI-NAV-001、TC-UI-DASH-001、TC-UI-STU-002**

**Expected**: 功能可用，布局无明显错位

**Pass**: 两个浏览器各 3 个用例通过

---

## 16. 响应式（抽查）

### TC-UI-RES-001 · 窄屏适配

**Priority**: P2

**Steps**: 把 Chrome 窗口缩到 1024×768，再到 768×1024（平板竖屏）

**Expected**:
- 侧边栏折叠成图标栏 或 变抽屉式
- 所有卡片重排，不横向滚动
- 无文字被截断

**Pass**: 两档窄屏均无破版

---

## 17. 自测报告模板

每轮自测填一份贴到 PR：

```markdown
# UI Manual Test Report

- **Date**: YYYY-MM-DD
- **Tester**: <name>
- **Branch / Commit**: <sha>
- **Browser**: Chrome 131 / Safari 17 / Firefox 130
- **Screen**: 1440×900

## Summary

| 模块 | P0 | P1 | P2 | 通过 | 失败 | 阻塞 |
|---|---|---|---|---|---|---|
| 全局导航 | 1 | 0 | 0 | 1 | 0 | 0 |
| Dashboard | 2 | 1 | 0 | 3 | 0 | 0 |
| Research | 4 | 3 | 0 | 7 | 0 | 0 |
| Signals | 0 | 1 | 0 | 1 | 0 | 0 |
| Scholars | 1 | 1 | 0 | 2 | 0 | 0 |
| Papers | 2 | 1 | 0 | 3 | 0 | 0 |
| Skills | 0 | 0 | 1 | 1 | 0 | 0 |
| Studio | 4 | 1 | 0 | 4 | 1 | 0 |
| Wiki | 0 | 0 | 1 | 1 | 0 | 0 |
| Settings | 3 | 1 | 0 | 4 | 0 | 0 |
| Push | 0 | 1 | 0 | 1 | 0 | 0 |
| 错误边界 | 1 | 1 | 1 | 3 | 0 | 0 |
| 兼容性 | 0 | 0 | 1 | 1 | 0 | 0 |
| 响应式 | 0 | 0 | 1 | 1 | 0 | 0 |
| **合计** | **18** | **11** | **5** | **33** | **1** | **0** |

## 失败详情

### TC-UI-STU-004 · Sandbox 资源监控（FAIL）
- **现象**: 指标曲线长时间停在 0
- **复现率**: 3/3
- **截图**: <link>
- **怀疑原因**: metrics stream 端点未连接
- **Blocks release**: No（日志仍可用）

## 发布结论

- [x] Approve for release
- [ ] Block — 需先修 TC-XXX
```

---

## 18. 已知限制（不视为 bug）

| 项目 | 说明 | 处理方式 |
|---|---|---|
| Deadline Radar | 会议数据硬编码，不会自动刷新 | 未来接入 AI Deadlines 数据源 |
| Claude Code 集成 | 只是 skill 目录发现，非 SDK 集成 | 按需扩展 |
| PDF 报告 | 需系统有 `libpango`（macOS: `brew install pango`） | 缺少时降级为 Markdown |
| 冷启动 | 首次加载 ≥ 5s，与 Next.js 热重载相关 | 二次加载正常 |

---

**End of Document**
