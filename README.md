# 趋势与认知观察助手

这个项目用你本人登录后的本地 Edge/Chrome 浏览器访问小红书，整理你手动也能打开的公开笔记和评论，再生成网页报告、结构化数据、Excel、SQLite、Markdown 和 AI 分析输入包。

它的目标不是追热点，而是筛选“有信息差、能解释产业/社会运行逻辑、值得继续深挖”的内容。

## 安全边界

- 不绕过验证码、扫码验证、风控、权限限制或账号限制。
- 只整理你本人登录后也能访问的公开内容。
- 遇到 Cookie 失效、验证、真实 404、App-only 或权限不足，会记录到 `access_status`、`quality-audit.json` 和 `diagnostics/`，不会强行规避。

## 第一次运行

先进入项目目录：

```powershell
cd "C:\Users\dell\Desktop\xhs-trend-observer"
```

检查环境：

```powershell
.\trend_xhs_observer.cmd -Doctor
```

如果提示依赖缺失：

```powershell
.\trend_xhs_observer.cmd -InstallDeps
```

建议先跑样例数据，确认网页和导出流程正常：

```powershell
.\trend_xhs_observer.cmd -SmokeTest -NoOpen
```

## 保存和复用登录态

第一次正式采集会打开一个干净的浏览器窗口。如果 `.env` 里没有可用 Cookie，脚本会提示你在浏览器里登录/扫码，然后回到 PowerShell 按 Enter。登录成功后，Cookie 会写入项目根目录的 `.env`，下次优先复用，不需要每次扫码。

只刷新 Cookie、不采集数据：

```powershell
.\trend_xhs_observer.cmd -CookieOnly -RefreshCookie
```

不使用 `.env`，只依赖当前浏览器 profile：

```powershell
.\trend_xhs_observer.cmd -Query "AI 传统产业 城市流动" -NoCookieEnv
```

## 正式采集

查看可选关注方向：

```powershell
.\trend_xhs_observer.cmd -ListFocusTopics
```

推荐命令：

```powershell
.\trend_xhs_observer.cmd -Query "AI 传统产业 城市流动 教育体制 年轻人消费 信息差" -MaxNotes 30 -PerQuery 6 -Sort latest -Diagnose
```

单关键词直接接 Gemini 分析：

```powershell
.\trend_xhs_observer.cmd -Query "AI 新工具 信息差" -MaxNotes 5 -AIAnalyze
```

批量关键词模式会生成 `outputs\batch-YYYYMMDD-HHMM\`：

```powershell
.\trend_xhs_observer.cmd -RunBatch -MaxNotes 5 -AIAnalyze
```

只采集某几个方向，例如“城市流动”和“教育体制”：

```powershell
.\trend_xhs_observer.cmd -Query "AI 传统产业 城市流动 教育体制 年轻人消费 信息差" -FocusTopics "3,4" -QueryLimit 8 -MaxNotes 30 -PerQuery 6 -Sort latest -Diagnose
```

也可以用中文片段：

```powershell
.\trend_xhs_observer.cmd -Query "AI 传统产业 城市流动 教育体制" -FocusTopics "城市,教育" -QueryLimit 8 -MaxNotes 30 -PerQuery 6 -Sort latest -Diagnose
```

参数说明：

- `-Query`：你的主题关键词。脚本会自动扩展成多条小查询，不会把整句一股脑搜索。
- `-FocusTopics`：限制关注方向，支持编号如 `3,4`，也支持中文片段如 `城市,教育`。
- `-QueryLimit`：限制扩展搜索词数量，避免一次运行搜索太多方向。
- `-MaxNotes`：最多保留多少条笔记。
- `-PerQuery`：每个扩展关键词最多采集多少条。
- `-Sort`：`general` 综合、`latest` 最新、`popular` 热门、`comments` 评论多、`collects` 收藏多。
- `-Diagnose`：保存搜索/详情失败诊断，包括截图和 `diagnostics.json`。
- `-NoOpen`：跑完后不自动打开网页。
- `-NoDetail`：不打开详情页，只保留搜索卡片线索。
- `-NoSkipCollected`：重复采集已经收过的笔记。
- `-AIAnalyze`：读取 `ai_input.md`，调用 Gemini 生成 `ai_output.json` / `ai_output.md`，并整合进 `report.html`。
- `-RunBatch`：使用默认趋势观察关键词池顺序采集，输出到 `outputs\batch-YYYYMMDD-HHMM\`。

默认会用 `work/collected-note-ids.json` 跳过已采集笔记。

## Gemini 分析

使用 `-AIAnalyze` 前，先把 Gemini key 放到系统环境变量或项目 `.env`：

```text
GEMINI_API_KEY="你的 Gemini API key"
```

也兼容 `GOOGLE_API_KEY`。没有 key 时，脚本会明确停止并提示缺少 `GEMINI_API_KEY` 或 `GOOGLE_API_KEY`，不会伪造 AI 分析。

Gemini 输出文件：

- `ai_input.md`：给 Gemini 的完整分析材料，按关键词组织代表帖子、正文、评论、初步趋势和建议问题。
- `ai_output.json`：Gemini 返回的结构化分析。
- `ai_output.md`：Gemini 分析的 Markdown 版本。
- `report.html`：整合 Gemini 分析后的网页报告。

## 详情页打开逻辑

采集器会保留搜索结果里的真实 `note_id`、`xsec_token`、`xsec_source`。搜索卡片里经常同时有隐藏的 `/explore/{id}` 和可见的 `/search_result/{id}?xsec_token=...`，程序必须点击可见、有尺寸、可交互的真实 DOM 卡片链接。

禁止用拼接 `/explore/{id}`、`page.goto`、`safe_goto`、`window.open` 作为详情页回退。点击后如果没有进入 `modal`、`popup` 或 `url_changed` 详情态，会记录为失败，不会伪装成详情成功。

如果详情页打不开，会区分：

- `ok`：详情页正常打开；
- `route_404`：真实 404 或链接失效；
- `note_unavailable_or_app_only`：笔记不可用或仅 App 查看；
- `cookie_invalid_or_login_required`：Cookie 失效或需要登录；
- `verification_or_rate_limited`：出现验证或频率限制；
- `timeout`：详情页超时。

如果连续多条详情失败，会停止继续打开详情页，避免一直卡在打不开的帖子里。

## 输出文件

每次运行会在 `outputs/<主题>-<时间>/` 下生成：

- `report.html`：网页报告，直接用浏览器打开。
- `index.html`：同一份网页报告的兼容副本。
- `trend-report.json`：最终报告数据。
- `normalized-research.json`：归一化观察结果。
- `raw-xhs.json`：原始采集数据。
- `structured-notes.json`：统一字段笔记数据。
- `structured-notes.csv`：CSV。
- `structured-notes.xlsx`：Excel，包含笔记和高价值评论两个 sheet。
- `xhs-notes.sqlite`：本地 SQLite 数据库。
- `trend-report.md`：Markdown 报告。
- `quality-audit.json` / `quality-audit.md`：采集质量审计。
- `ai-input.json` / `ai_input.json`：可交给 AI 二次分析的输入包。
- `ai_input.md`：Gemini 分析材料。
- `ai_output.json` / `ai_output.md`：使用 `-AIAnalyze` 时生成的 Gemini 分析结果。
- `ai-analysis-prompt.md`：配套中文分析提示词。
- `diagnostics/`：使用 `-Diagnose` 时的截图和失败诊断。

## 网页里能看什么

`report.html` / `index.html` 包含：

- 搜索关键词和采集质量指标；
- 每个关键词采集数量、详情页打开数量、JSON 解析成功数量和评论数量；
- 高频关键词；
- Gemini 趋势分析；
- 笔记列表；
- 原始帖子列表和可展开评论；
- 高价值评论榜；
- AI 分析摘要；
- 趋势标签；
- 避坑清单；
- KOL/博主分析；
- 评论情绪；
- 爆款笔记结构拆解；
- 竞品/渠道对比线索；
- 原文链接；
- 导出 Markdown / JSON / HTML 按钮。

## 质量状态怎么看

终端最后会打印：

- `Detail records`：真实进入详情页的数量；
- `API detail fallback records`：详情页被拦截但接口补读成功的数量；
- `Detail/comment evidence records`：有详情正文或评论证据的数量；
- `Records with comments`：抓到评论的笔记数量；
- `Comments captured`：评论总数；
- `Quality status`：`ok`、`partial`、`needs_login`、`blocked_by_xhs_verification` 或 `failed`。

如果 `Quality status` 不是 `ok`，优先打开同目录的 `quality-audit.md` 看建议。

`quality-audit.json` 里还会有 `problem_samples`，列出最多 12 条问题样本，包括标题、原文链接、详情页最终地址、失败分类和错误信息。正式采集遇到大量 404 或扫码页时，先看这个字段，比直接翻 `raw-xhs.json` 快很多。

## 离线重建页面

如果已经有 `raw-xhs.json`，可以不访问小红书，重新生成网页和导出文件：

```powershell
.\trend_xhs_observer.cmd -NoXhs -RawXhs "C:\path\to\raw-xhs.json" -Query "你的主题" -NoOpen
```

## 定时任务

创建每天 09:30 自动运行的 Windows 任务：

```powershell
.\trend_xhs_observer.cmd -Query "AI 传统产业 城市流动 教育体制" -ScheduleDaily 09:30
```

## 最小可运行版本

```powershell
cd "C:\Users\dell\Desktop\xhs-trend-observer"
.\trend_xhs_observer.cmd -SmokeTest -NoOpen
```

成功标志：

- 终端出现 `VALIDATION OK`；
- 终端出现 `Quality status: ok`；
- `outputs\smoke-test\report.html` 可打开；
- `outputs\smoke-test\ai_input.md`、`ai-input.json`、`quality-audit.json`、`structured-notes.xlsx`、`xhs-notes.sqlite` 存在。
