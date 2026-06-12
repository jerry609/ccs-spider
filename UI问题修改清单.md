# PaperBot UI 自测问题修改清单

> 基于《产品UI页面自测问题记录.pdf》整理，已排除 DeepCode Studio 模块。
> 生成日期：2026-04-18
> 优先级：**P0 阻塞** / **P1 影响核心体验** / **P2 优化项** / **⚑ 需产品决策**

---

## P0 阻塞类（登录/注册 + 白屏）

| # | 模块 | 问题 | 建议修复方向 |
|---|------|------|------|
| 1 | Auth §2.3 | Sign in 报 `Internal server error`（python-jose） | 检查 `requirements.txt` / `pyproject.toml` 是否装了 `python-jose[cryptography]`；在 `api/routes/auth.py` 里捕获 JWT 异常返回 401 而不是 500 |
| 2 | Auth §2.3 | 注册接口返回 400/500 | 检查后端用户注册校验与 DB 迁移，补齐错误信息（字段级） |
| 3 | Auth §2.3 | 密码重置邮件未发送 | 确认 SMTP 配置或邮件服务未接入——如未接入请在 UI 上隐藏入口 |
| 4 | Error §14 TC-UI-ERR-002 | 非法路由白屏 | 补 `app/not-found.tsx` + `app/error.tsx`，提供全局兜底 |
| 5 | Error §14 TC-UI-ERR-001 | 未登录访问受保护页仅报错，未重定向 | 中间件 `middleware.ts` 对受保护路径重定向到 `/signin?next=...` |

---

## P1 核心体验问题

### Dashboard §4
- **TC-UI-DASH-002 Deadline Radar 未实现**：要么接数据要么从首页移除卡片，避免"死控件"。
- **TC-UI-DASH-003 Analyze 按钮跳转目的不清**：该按钮应直接触发分析或明确跳转上下文；当前跳到 `research` 没有语义。⚑ 需产品决策：保留 or 删除。

### Research §5
- **TC-UI-RES-002 搜索结构化卡片不显示、结果无评分**
  - 检查 `SearchResultCard` 组件是否渲染了结构化字段（摘要/作者/年份/venue）
  - 五维评分需调用 `/api/analyze` 的轻量版或复用 `paper_judge`
- **TC-UI-RES-004 BibTeX Import 的 Track Name** 应改为下拉（从 `GET /api/tracks` 拉取），而不是自由文本
- **TC-UI-RES-007 Memory**
  - Workspace Library 与 Library 功能重复 → 合并或明确区分
  - ⚑ 需架构决策：自研 Memory vs 集成 mem0。当前 FTS5+sqlite-vec 混合方案若不跟进，建议接 mem0

### Scholars §7
- **TC-UI-SCH-001**：`/scholars/<id>` 详情页数据不会自动刷新 → 加 SWR/polling 或手动 Refresh 按钮
- **TC-UI-SCH-002 PIS Radar 无数据** → 后端 `/api/scholars/<id>/pis` 是否实现？未实现则隐藏雷达

### Papers §8
- **TC-UI-PAP-001 / TC-UI-PAP-002 五维雷达不可见 / 无法触发**
  - 检查 `PaperJudgeRadar` 组件是否挂在详情页
  - 触发入口加到 Abstract 上方，复用 `paper_judge` MCP 工具
- **TC-UI-PAP-001 Abstract 导航栏 "Deep Intelligence" / "Reproduction" 多余** → 删除或下沉到 Studio 入口
- **TC-UI-PAP-003 Deep Review 功能已找不到**：全局搜 `deep[-_]review`、`DeepReview` 确认是否仍有路由；若已废弃删除菜单项；若保留需恢复入口

### Settings §12
- **TC-UI-SET-001 Account 页面重构**：信息分区混乱，建议三段式「个人信息 / 安全 / 订阅」
- **TC-UI-SET-003 Model Providers**：Studio 走 Claude Code，PaperBot 后端仍需 OpenAI/Anthropic Key → 明确说明"此处 Key 仅用于 PaperBot 后端分析/判分"，避免与 Studio CLI 混淆
- **TC-UI-SET-004 Scholar Subscriptions 卡片**：Settings 里放订阅列表语义错误 → 移到 `/scholars` 页或删除

---

## P2 优化项

- Dashboard TC-UI-DASH-001：构建后回归验证即可
- Research TC-UI-RES-001 / 003：保持
- Skills TC-UI-SKL-001：保持
- Wiki TC-UI-WIKI-001：保持
- Auth 密码重置左侧说明文案不清晰（§2.3）→ 加引导文案

---

## ⚑ 需产品决策（不是 bug）

| 议题 | 建议选项 |
|------|------|
| Signals 模块（§6 TC-UI-SIG-001 FAIL）"非核心功能，商讨删除" | A. 删除路由+菜单；B. 留 MVP 版接 Reddit/X 连接器 |
| Memory 自研 vs mem0（§5 TC-UI-RES-007） | 保留自研需补 UI+评测；接 mem0 需改 `infrastructure/memory/` 抽象 |
| Dashboard Analyze 按钮的存在意义（§4 TC-UI-DASH-003） | 删除 or 明确跳转到单篇分析 |
| Papers 详情页 Deep Intelligence / Reproduction 导航（§8） | 统一只保留 Studio 入口 |
| Settings 的 Scholar Subscriptions 卡片（§12 TC-UI-SET-004） | 移到 `/scholars` |

---

## 修复顺序建议

1. **第一批（P0，本周）**：Auth 5 项 + 全局白屏/重定向 → 否则任何测试都跑不下去
2. **第二批（P1-后端）**：Scholars/Papers 五维雷达数据打通、Research 搜索卡片结构化
3. **第三批（P1-前端重构）**：Settings 重构、Papers 导航清理、BibTeX Track 下拉
4. **第四批（⚑ 决策后）**：Signals 去留、Memory 方案选型
