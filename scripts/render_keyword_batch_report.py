from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CN_TZ = timezone(timedelta(hours=8))


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def as_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        parts: list[str] = []
        for key in [
            "title",
            "summary",
            "comment",
            "why_high_value",
            "concept",
            "explanation",
            "why_it_matters",
            "topic",
            "reason",
            "evidence",
            "follow_up_keywords",
        ]:
            value = item.get(key)
            if isinstance(value, list):
                value = " / ".join(str(part) for part in value if part)
            if value:
                parts.append(str(value))
        return "：".join(parts)
    return str(item)


def collect_runs(batch_root: Path) -> list[dict[str, Any]]:
    candidates = []
    if (batch_root / "trend-report.json").exists():
        candidates.append(batch_root)
    runs_dir = batch_root / "keywords"
    if runs_dir.exists():
        candidates.extend(path for path in runs_dir.iterdir() if path.is_dir())
    candidates.extend(path for path in batch_root.iterdir() if path.is_dir() and (path / "trend-report.json").exists())
    unique = []
    seen: set[Path] = set()
    for path in sorted(candidates):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)

    rows: list[dict[str, Any]] = []
    for path in unique:
        report = load_json(path / "trend-report.json", {}) or {}
        raw = load_json(path / "raw-xhs.json", {}) or {}
        ai = load_json(path / "ai_output.json", {}) or {}
        diagnostics = report.get("diagnostics_summary", {}) or {}
        meta = report.get("meta", {}) or {}
        raw_meta = raw.get("meta", {}) if isinstance(raw, dict) else {}
        raw_queries = raw_meta.get("queries") or []
        keyword = meta.get("query") or (raw_queries[0] if raw_queries else "") or meta.get("topic", "")
        local_comments = report.get("high_value_comments", []) or []
        rows.append(
            {
                "keyword": keyword or path.name,
                "run_dir": str(path),
                "report_html": str(path / "report.html"),
                "ai_output_md": read_text(path / "ai_output.md"),
                "meta": {
                    "records": len(raw.get("records", []) or []),
                    "observations": len(report.get("observations", []) or []),
                    "detail_records": meta.get("detail_record_count", 0),
                    "json_parser_success": meta.get("json_parser_success_count", 0),
                    "comment_records": meta.get("comment_record_count", 0),
                    "comments": meta.get("total_comment_count", 0),
                    "quality_warning": meta.get("quality_warning", ""),
                    "quality_status": (load_json(path / "quality-audit.json", {}) or {}).get("status", "unknown"),
                },
                "gemini": {
                    "provider": ai.get("meta", {}).get("analysis_provider", ""),
                    "model": ai.get("meta", {}).get("model", ""),
                    "core_trends": ai.get("core_trends", []) or [],
                    "hot_topics": ai.get("hot_topics", []) or [],
                    "key_viewpoints": ai.get("key_viewpoints", []) or [],
                    "high_value_posts": ai.get("high_value_posts", []) or [],
                    "high_value_comments": ai.get("high_value_comments", []) or [],
                    "high_score_comment_excerpts": ai.get("high_score_comment_excerpts", []) or [],
                    "core_concepts": ai.get("core_concepts", []) or [],
                    "topic_extensions": ai.get("topic_extensions", []) or [],
                    "report_recommendations": ai.get("report_recommendations", []) or [],
                    "highlighted_trends": ai.get("highlighted_trends", []) or [],
                    "risk_notes": ai.get("risk_notes", []) or [],
                    "follow_up_keywords": ai.get("follow_up_keywords", []) or [],
                },
                "local_high_value_comments": local_comments[:12],
                "observations": report.get("observations", [])[:20],
                "themes": report.get("themes", []) or [],
                "raw_posts": report.get("raw_posts", [])[:20],
                "diagnostics": diagnostics,
            }
        )
    return rows


def build_data(batch_root: Path) -> dict[str, Any]:
    runs = collect_runs(batch_root)
    return {
        "meta": {
            "title": "小红书信息差发现仪表盘",
            "generated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
            "project_root": str(Path(__file__).resolve().parents[1]),
            "batch_root": str(batch_root),
            "keyword_count": len(runs),
            "total_records": sum(item["meta"]["records"] for item in runs),
            "total_comments": sum(item["meta"]["comments"] for item in runs),
        },
        "keywords": runs,
    }


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>小红书信息差发现仪表盘</title>
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", Arial, sans-serif; background: #f5f6f2; color: #202320; line-height: 1.6; }
    .page { max-width: 1180px; margin: 0 auto; padding: 22px 16px 56px; }
    header { display: grid; gap: 12px; padding-bottom: 18px; border-bottom: 1px solid #d9ded7; }
    h1 { margin: 0; font-size: 32px; line-height: 1.2; }
    h2 { margin: 0 0 10px; font-size: 20px; }
    h3 { margin: 0 0 8px; font-size: 17px; }
    button, input, textarea { font: inherit; }
    input, textarea { border: 1px solid #d9ded7; border-radius: 8px; padding: 9px 10px; background: #fff; }
    textarea { width: 100%; min-height: 84px; resize: vertical; }
    .panel, .keyword-card { background: #fff; border: 1px solid #d9ded7; border-radius: 8px; padding: 14px; margin: 14px 0; box-shadow: 0 16px 40px rgba(32, 35, 32, .07); }
    .grid { display: grid; gap: 10px; }
    .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    .btn { border: 1px solid #d9ded7; background: #fff; color: #202320; min-height: 38px; padding: 8px 12px; border-radius: 8px; cursor: pointer; }
    .btn.primary { background: #202320; color: #fff; border-color: #202320; }
    .metric { display: inline-flex; gap: 5px; color: #626a63; font-size: 13px; border: 1px solid #d9ded7; border-radius: 999px; padding: 4px 8px; background: #fff; }
    .metric b { color: #202320; }
    .command { font-family: Consolas, monospace; white-space: pre-wrap; background: #f0f3ee; border: 1px solid #d9ded7; border-radius: 8px; padding: 10px; }
    .chip { display: inline-flex; border: 1px solid #d9ded7; border-radius: 999px; padding: 4px 8px; background: #f8faf7; color: #626a63; font-size: 13px; }
    .block { border-top: 1px solid #d9ded7; padding-top: 10px; margin-top: 10px; }
    .highlight { border-left: 4px solid #bd3f32; padding-left: 10px; }
    .priority-section { border-top: 1px solid #d9ded7; padding-top: 10px; margin-top: 10px; }
    .priority-title { display:flex; justify-content:space-between; gap:8px; align-items:center; margin-bottom:8px; }
    .priority-title h3 { margin:0; font-size:16px; }
    .priority-grid { display:grid; gap:8px; }
    .mini-card { border:1px solid #d9ded7; border-radius:8px; padding:9px; background:#fff; }
    .mini-card h4 { margin:0 0 5px; font-size:14px; line-height:1.35; }
    .mini-card p { margin:0 0 6px; font-size:13px; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
    .mini-card details { color:#626a63; font-size:13px; }
    .mini-card summary { color:#08776f; cursor:pointer; }
    .comment-card { border-left:4px solid #bd3f32; }
    details { margin-top: 8px; }
    summary { cursor: pointer; color: #08776f; }
    a { color: #08776f; }
    @media (min-width: 820px) { .two { grid-template-columns: 1.2fr .8fr; } header { grid-template-columns: 1fr auto; align-items: end; } .priority-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .priority-grid.compact { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
  </style>
</head>
<body>
  <div class="page">
    <header>
      <div>
        <p style="color:#bd3f32;font-weight:700;margin:0 0 6px">趋势与认知观察助手</p>
        <h1>小红书信息差发现仪表盘</h1>
        <p id="subtitle" style="color:#626a63;margin:8px 0 0"></p>
      </div>
      <div class="row">
        <button class="btn" onclick="copyBatchCommand()">复制批量命令</button>
        <button class="btn" onclick="downloadCmd()">下载批处理</button>
        <button class="btn primary" onclick="window.print()">打印</button>
      </div>
    </header>

    <section class="panel">
      <h2>关键词工作台</h2>
      <div class="grid two">
        <div class="grid">
          <input id="keywordInput" placeholder="输入关键词，例如：AI 新工具 信息差">
          <textarea id="keywordBulk" placeholder="也可以一行一个关键词，批量追加"></textarea>
        </div>
        <div class="grid">
          <input id="maxNotesInput" type="number" min="1" value="5" placeholder="MaxNotes">
          <div class="row">
            <button class="btn primary" onclick="addKeyword()">添加关键词</button>
            <button class="btn" onclick="addBulk()">批量追加</button>
            <button class="btn" onclick="clearKeywords()">清空列表</button>
          </div>
        </div>
      </div>
      <div id="keywordList" class="grid" style="margin-top:12px"></div>
      <div class="block">
        <b>批量执行命令</b>
        <div class="command" id="batchCommand"></div>
      </div>
    </section>

    <section id="summary" class="panel"></section>
    <section id="keywordBlocks"></section>
  </div>

  <script id="app-data" type="application/json">__DATA__</script>
  <script>
    const data = JSON.parse(document.getElementById('app-data').textContent);
    let keywords = JSON.parse(localStorage.getItem('xhsKeywordQueue') || '[]');

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[ch]));
    }
    function psQuote(value) { return String(value || '').replace(/"/g, '`"'); }
    function singleCommand(keyword, maxNotes) {
      return `.\\trend_xhs_observer.cmd -Query "${psQuote(keyword)}" -MaxNotes ${maxNotes || 5} -AIAnalyze`;
    }
    function batchCommand() {
      const maxNotes = document.getElementById('maxNotesInput').value || 5;
      const joined = keywords.map(k => k.keyword).join(';');
      return `.\\run_keyword_batch.cmd -Keywords "${psQuote(joined)}" -MaxNotes ${maxNotes}`;
    }
    function projectRootCdCommand() {
      const root = String(data.meta?.project_root || '').replace(/"/g, '');
      return root ? `cd /d "${root}"` : 'cd /d "%~dp0"';
    }
    function saveKeywords() {
      localStorage.setItem('xhsKeywordQueue', JSON.stringify(keywords));
    }
    function addKeyword() {
      const input = document.getElementById('keywordInput');
      const keyword = input.value.trim();
      if (!keyword) return alert('请输入关键词');
      if (!keywords.some(k => k.keyword === keyword)) keywords.push({ keyword });
      input.value = '';
      saveKeywords();
      renderQueue();
    }
    function addBulk() {
      const input = document.getElementById('keywordBulk');
      const rows = input.value.split(/\r?\n|;/).map(s => s.trim()).filter(Boolean);
      for (const keyword of rows) {
        if (!keywords.some(k => k.keyword === keyword)) keywords.push({ keyword });
      }
      input.value = '';
      saveKeywords();
      renderQueue();
    }
    function clearKeywords() {
      keywords = [];
      saveKeywords();
      renderQueue();
    }
    async function copyText(text) {
      try { await navigator.clipboard.writeText(text); alert('已复制到剪贴板'); }
      catch { prompt('复制这段命令：', text); }
    }
    function copyBatchCommand() {
      if (!keywords.length) return alert('请先添加关键词');
      copyText(batchCommand());
    }
    function downloadCmd() {
      if (!keywords.length) return alert('请先添加关键词');
      const content = ['@echo off', projectRootCdCommand(), batchCommand(), 'pause', ''].join('\r\n');
      const blob = new Blob([content], { type: 'application/octet-stream' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'run_xhs_keywords.cmd';
      a.click();
      URL.revokeObjectURL(url);
    }
    function renderQueue() {
      const maxNotes = document.getElementById('maxNotesInput').value || 5;
      document.getElementById('keywordList').innerHTML = keywords.map((item, index) => {
        const cmd = singleCommand(item.keyword, maxNotes);
        return `<div class="block"><b>${index + 1}. ${escapeHtml(item.keyword)}</b><div class="command">${escapeHtml(cmd)}</div><div class="row"><button class="btn" data-copy-index="${index}" type="button">复制单条命令</button></div></div>`;
      }).join('') || '<p style="color:#626a63">还没有添加关键词。</p>';
      document.getElementById('batchCommand').textContent = keywords.length ? batchCommand() : '添加关键词后生成批量执行命令。';
    }
    function list(items) {
      const values = (items || []).map(item => {
        if (typeof item === 'string') return item;
        if (!item || typeof item !== 'object') return String(item || '');
        const keys = ['title', 'summary', 'comment', 'why_high_value', 'reason', 'concept', 'explanation', 'why_it_matters', 'topic', 'evidence', 'follow_up_keywords'];
        return keys.map(key => Array.isArray(item[key]) ? item[key].filter(Boolean).join(' / ') : item[key]).filter(Boolean).join('：');
      }).filter(Boolean);
      return values.length ? `<ul>${values.slice(0, 8).map(v => `<li>${escapeHtml(v)}</li>`).join('')}</ul>` : '<p style="color:#626a63">暂无。</p>';
    }
    function renderSummary() {
      const meta = data.meta || {};
      document.getElementById('subtitle').textContent = `${meta.generated_at || ''} · 关键词 ${meta.keyword_count || 0} · 记录 ${meta.total_records || 0} · 评论 ${meta.total_comments || 0}`;
      document.getElementById('summary').innerHTML = `<h2>总览</h2><div class="row"><span class="metric">关键词 <b>${meta.keyword_count || 0}</b></span><span class="metric">采集记录 <b>${meta.total_records || 0}</b></span><span class="metric">评论 <b>${meta.total_comments || 0}</b></span></div>`;
    }
    function asText(item) {
      if (typeof item === 'string') return item;
      if (!item || typeof item !== 'object') return String(item || '');
      const keys = ['title', 'summary', 'comment', 'why_high_value', 'reason', 'concept', 'explanation', 'why_it_matters', 'topic', 'evidence', 'underlying_logic', 'why_now', 'reflected_trend'];
      return keys.map(key => Array.isArray(item[key]) ? item[key].filter(Boolean).join(' / ') : item[key]).filter(Boolean).join('：');
    }
    function shortText(value, limit = 90) {
      const cleaned = String(value ?? '').replace(/\s+/g, ' ').trim();
      return cleaned.length > limit ? `${cleaned.slice(0, limit - 1)}…` : cleaned;
    }
    function asArray(value) { return Array.isArray(value) ? value.filter(Boolean) : []; }
    function scorePost(post) {
      return Number(post.like_count || 0) + Number(post.collect_count || 0) * 2 + Number(post.comment_count || 0) * 3;
    }
    function miniCard(card) {
      return `<article class="mini-card ${card.className || ''}">
        <h4>${escapeHtml(card.rank || '')} ${escapeHtml(shortText(card.title || '未命名', 42))}</h4>
        <p>${escapeHtml(shortText(card.line || '暂无结论', 110))}</p>
        <div class="row">${asArray(card.tags).slice(0, 5).map(tag => `<span class="chip">${escapeHtml(tag)}</span>`).join('')}</div>
        <details><summary>展开详情</summary><div>${asArray(card.details).filter(Boolean).map(escapeHtml).join('<br>')}${card.url ? `<br><a href="${escapeHtml(card.url)}" target="_blank" rel="noreferrer">打开来源</a>` : ''}</div></details>
      </article>`;
    }
    function prioritySection(code, title, cards, compact = false) {
      const rows = cards.length ? cards : [{ title: '暂无足够信号', line: '本轮样本还不够支撑这个榜单。', tags: ['待采集'], details: [] }];
      return `<section class="priority-section">
        <div class="priority-title"><h3>${escapeHtml(code)} ${escapeHtml(title)}</h3><span class="metric">${rows.length} 条</span></div>
        <div class="priority-grid ${compact ? 'compact' : ''}">${rows.map(miniCard).join('')}</div>
      </section>`;
    }
    function commentCards(item) {
      const g = item.gemini || {};
      const rows = [];
      const seen = new Set();
      const add = (comment, title, score, details, url = '') => {
        const value = shortText(comment, 170);
        if (!value || seen.has(value)) return;
        seen.add(value);
        rows.push({ title, line: value, score, details, url, className: 'comment-card' });
      };
      for (const row of asArray(g.high_score_comment_excerpts)) add(row.comment || asText(row), row.topic || '高价值评论', 100, [row.why_high_value || row.reason || asText(row)]);
      for (const row of asArray(g.high_value_comments)) add(row.comment || asText(row), row.topic || '高价值评论', 90, [row.reason || asText(row)]);
      for (const row of asArray(item.local_high_value_comments)) add(row.comment, row.note_title || row.theme || '高价值评论', Number(row.value_score || 0) + 50, [row.reason], row.note_url || '');
      for (const obs of asArray(item.observations)) {
        const source = obs.source || {};
        for (const row of asArray(obs.valuable_comments)) add(row.comment, source.title || obs.phenomenon, Number(row.value_score || 0) + 40, [row.reason, asArray(row.tags).join(' / ')], source.url || '');
      }
      return rows.sort((a, b) => b.score - a.score).slice(0, 6).map((row, index) => ({ ...row, rank: `#${index + 1}`, tags: [`评论价值 ${row.score}`, '独立展示'] }));
    }
    function renderKeywordBlocks() {
      document.getElementById('keywordBlocks').innerHTML = (data.keywords || []).map(item => {
        const g = item.gemini || {};
        const m = item.meta || {};
        const observations = asArray(item.observations);
        const comments = commentCards(item);
        const top = observations.slice().sort((a, b) => Number(b.research_score || 0) - Number(a.research_score || 0)).slice(0, 4).map((row, index) => ({
          rank: `#${index + 1}`,
          title: row.phenomenon || row.source?.title,
          line: row.underlying_logic || row.trend_reflected,
          tags: [row.theme, `分数 ${row.research_score || 0}`, row.confidence],
          details: [row.surface_appearance, row.why_recent, row.possibly_overstated],
          url: row.source?.url || ''
        }));
        const infoGaps = asArray(g.core_trends).map((row, index) => ({
          rank: `#${index + 1}`,
          title: row.title || '未命名信息差',
          line: row.underlying_logic || row.summary,
          tags: ['信息差', row.exaggeration_risk ? '有夸大风险' : '待核验'],
          details: [row.surface_appearance, row.why_now, row.reflected_trend, row.exaggeration_risk]
        })).concat(top).slice(0, 6);
        const clusters = asArray(item.themes).map((row, index) => ({
          rank: `#${index + 1}`,
          title: row.theme,
          line: `样本 ${row.count || 0} 条，均分 ${row.avg_score || '-'}`,
          tags: asArray(row.top_signals),
          details: [`代表信号：${asArray(row.top_signals).join(' / ') || '暂无'}`]
        })).concat(asArray(g.highlighted_trends).map(row => ({ title: asText(row), line: 'Gemini 标记的趋势信号', tags: ['趋势'], details: [asText(row)] }))).slice(0, 6);
        const counterWords = /反直觉|反常识|不是|反而|便宜|责任|不行|为什么|竟然|不信|难以|无法/;
        const counter = infoGaps.concat(comments).filter(row => counterWords.test([row.title, row.line].join(' '))).slice(0, 5).map((row, index) => ({ ...row, rank: `#${index + 1}`, tags: ['反常识'].concat(asArray(row.tags).slice(0, 2)) }));
        const raw = asArray(item.raw_posts).slice().sort((a, b) => scorePost(b) - scorePost(a)).slice(0, 8).map((post, index) => ({
          rank: `#${index + 1}`,
          title: post.title || '未命名帖子',
          line: asArray(post.comments)[0] || post.extraction_method || '暂无摘要',
          tags: [`赞 ${post.like_count ?? '-'}`, `藏 ${post.collect_count ?? '-'}`, `评 ${post.comment_count ?? '-'}`],
          details: [`作者：${post.author || '-'}`, `方式：${post.extraction_method || '-'}`].concat(asArray(post.comments).slice(0, 4)),
          url: post.url || ''
        }));
        return `<article class="keyword-card">
          <h2>${escapeHtml(item.keyword)}</h2>
          <div class="row">
            <span class="metric">采集 <b>${m.records || 0}</b></span>
            <span class="metric">详情 <b>${m.detail_records || 0}</b></span>
            <span class="metric">评论 <b>${m.comments || 0}</b></span>
            <span class="metric">质量 <b>${escapeHtml(m.quality_status || '')}</b></span>
          </div>
          ${prioritySection('P1', '今日最有价值发现', top)}
          ${prioritySection('P2', '高赞评论排行榜', comments)}
          ${prioritySection('P3', '信息差排行榜', infoGaps)}
          ${prioritySection('P4', '趋势聚类', clusters, true)}
          ${prioritySection('P5', '反常识发现', counter)}
          ${prioritySection('P6', '原始帖子列表', raw, true)}
          <details><summary>原始 JSON / Gemini Markdown</summary><pre class="command">${escapeHtml(item.ai_output_md || '')}</pre></details>
        </article>`;
      }).join('') || '<section class="panel">暂无关键词分析块。</section>';
    }
    document.getElementById('maxNotesInput').addEventListener('input', renderQueue);
    document.getElementById('keywordInput').addEventListener('keydown', event => {
      if (event.key === 'Enter') addKeyword();
    });
    document.getElementById('keywordList').addEventListener('click', event => {
      const button = event.target.closest('[data-copy-index]');
      if (!button) return;
      const keyword = keywords[Number(button.dataset.copyIndex)]?.keyword;
      if (keyword) copyText(singleCommand(keyword, document.getElementById('maxNotesInput').value || 5));
    });
    renderSummary();
    renderQueue();
    renderKeywordBlocks();
  </script>
</body>
</html>
"""


def render_html(data: dict[str, Any]) -> str:
    embedded = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return HTML.replace("__DATA__", embedded)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a multi-keyword XHS batch report.")
    parser.add_argument("--batch-root", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-html", required=True)
    args = parser.parse_args()

    data = build_data(Path(args.batch_root))
    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    out_html = Path(args.out_html)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(render_html(data), encoding="utf-8")
    print(f"keyword batch JSON written: {out_json}")
    print(f"keyword batch HTML written: {out_html}")
    print(f"keyword_blocks={len(data.get('keywords', []))}")


if __name__ == "__main__":
    main()
