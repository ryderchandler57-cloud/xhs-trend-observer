# 从桌面运行

当前统一项目目录：

```text
C:\Users\dell\Desktop\xhs-trend-observer
```

先进入目录：

```powershell
cd "C:\Users\dell\Desktop\xhs-trend-observer"
```

检查环境：

```powershell
.\trend_xhs_observer.cmd -Doctor
```

小样本真实采集：

```powershell
.\trend_xhs_observer.cmd -Query "AI 新工具 信息差" -MaxNotes 1 -PerQuery 1 -QueryLimit 1 -Diagnose -NoOpen -NoSkipCollected
```

接入 Gemini 分析：

```powershell
.\trend_xhs_observer.cmd -Query "AI 新工具 信息差" -MaxNotes 5 -AIAnalyze
```

批量关键词并接入 Gemini：

```powershell
.\trend_xhs_observer.cmd -RunBatch -MaxNotes 5 -AIAnalyze
```

`-AIAnalyze` 需要先在系统环境变量或项目 `.env` 中配置：

```text
GEMINI_API_KEY="你的 Gemini API key"
```

成功后重点看：

- `outputs\<run>\report.html`
- `outputs\<run>\ai_input.md`
- `outputs\<run>\ai_output.json`
- `outputs\<run>\ai_output.md`
- `outputs\<run>\diagnostics\diagnostics.json`

不要再从旧的 `Documents\Codex\...` 日期目录运行或修改这个项目。
