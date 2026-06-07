from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CN_TZ = timezone(timedelta(hours=8))


def load_json(path: str, default: Any = None) -> Any:
    file_path = Path(path)
    if not path or not file_path.exists():
        return default
    return json.loads(file_path.read_text(encoding="utf-8"))


def write_json(path: str, data: Any) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def trim_text(text: Any, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(clean_text(item) for item in value if clean_text(item))
    if isinstance(value, dict):
        for key in ("content", "text", "comment", "body", "summary", "title"):
            if value.get(key):
                return clean_text(value.get(key))
        return json.dumps(value, ensure_ascii=False)
    return re.sub(r"[ \t\r\f\v]+", " ", str(value)).strip()


def as_int(value: Any) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().lower().replace(",", "")
    multiplier = 1
    if text.endswith("w") or text.endswith("万"):
        multiplier = 10000
        text = text[:-1]
    elif text.endswith("k") or text.endswith("千"):
        multiplier = 1000
        text = text[:-1]
    text = re.sub(r"[^0-9.]+", "", text)
    if not text:
        return 0
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return 0


def first_value(*values: Any) -> Any:
    for value in values:
        if value in (None, "", [], {}):
            continue
        return value
    return ""


def note_key(note: dict[str, Any]) -> str:
    note_id = clean_text(note.get("note_id") or note.get("id"))
    if note_id:
        return f"id:{note_id}"
    url = clean_text(note.get("note_url") or note.get("url") or note.get("raw_url"))
    if url:
        return f"url:{url}"
    title = clean_text(note.get("title"))
    return f"title:{title}" if title else ""


def build_note_index(notes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for note in notes:
        key = note_key(note)
        if key:
            out[key] = note
    return out


def load_quality_index(quality_report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    notes = quality_report.get("notes", []) if isinstance(quality_report, dict) else []
    return build_note_index([note for note in notes if isinstance(note, dict)])


def interaction_score(note: dict[str, Any]) -> int:
    return (
        as_int(first_value(note.get("liked_count"), note.get("like_count"))) * 1
        + as_int(first_value(note.get("collected_count"), note.get("collect_count"))) * 2
        + as_int(note.get("comment_count")) * 3
    )


def select_effective_text(note: dict[str, Any], limit: int) -> tuple[str, bool]:
    text = clean_text(note.get("effective_text"))
    truncated = len(text) > limit
    if len(text) <= limit:
        return text, truncated
    priority_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.search(r"(AI|Agent|ChatGPT|Gemini|DeepSeek|Claude|Kimi|Qwen|AIPC|GPU|SaaS|ERP|CRM|RPA|自动化|智能体|工具|行业|价格|成本|方法|案例|痛点|不会|不能|太贵|便宜|效率|岗位|客户|销售|工厂|传统产业)", stripped, re.I):
            priority_lines.append(stripped)
    compact = "\n".join(priority_lines)
    if len(compact) >= 120:
        return trim_text(compact, limit), truncated
    return trim_text(text, limit), truncated


def compact_metadata_only(note: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    return {
        "keyword": note.get("keyword", ""),
        "note_id": note.get("note_id", ""),
        "title": note.get("title", ""),
        "author": source.get("author", ""),
        "note_url": note.get("note_url") or source.get("url", ""),
        "liked_count": as_int(first_value(source.get("liked_count"), source.get("like_count"))),
        "collected_count": as_int(first_value(source.get("collected_count"), source.get("collect_count"))),
        "comment_count": as_int(source.get("comment_count")),
        "effective_text_length": as_int(note.get("effective_text_length")),
        "text_sources": note.get("text_sources", []),
        "analysis_layer": "metadata_only",
        "reason": "effective_text 少于 80 字，且 OCR/评论文本不足；仅用于热度统计。",
    }


def compact_effective_note(
    note: dict[str, Any],
    source: dict[str, Any],
    quality: dict[str, Any],
    layer: str,
    text_limit: int,
) -> tuple[dict[str, Any], bool]:
    effective_text, truncated = select_effective_text(note, text_limit)
    liked_count = as_int(first_value(source.get("liked_count"), source.get("like_count")))
    collected_count = as_int(first_value(source.get("collected_count"), source.get("collect_count")))
    comment_count = as_int(source.get("comment_count"))
    ai_score = as_int(first_value(quality.get("ai_usable_score_after"), quality.get("ai_usable_score"), note.get("ai_usable_score")))
    text_sources = note.get("text_sources", []) or []
    compact = {
        "keyword": note.get("keyword", ""),
        "note_id": note.get("note_id", ""),
        "title": note.get("title", ""),
        "author": source.get("author", ""),
        "publish_time": source.get("publish_time", ""),
        "note_url": note.get("note_url") or source.get("url", ""),
        "url": note.get("note_url") or source.get("url", ""),
        "liked_count": liked_count,
        "collected_count": collected_count,
        "comment_count": comment_count,
        "like_count": liked_count,
        "collect_count": collected_count,
        "effective_text": effective_text,
        "content": effective_text,
        "effective_text_length": as_int(note.get("effective_text_length")),
        "text_sources": text_sources,
        "dom_text_length": as_int(note.get("dom_text_length")),
        "media_ocr_text_length": as_int(note.get("media_ocr_text_length")),
        "comment_text_length": as_int(note.get("comment_text_length")),
        "ai_usable_score": ai_score,
        "quality_tags": quality.get("quality_tags", []),
        "analysis_layer": layer,
        "effective_layer": note.get("effective_layer", ""),
        "ocr_status": note.get("ocr_status", ""),
        "text_truncated": truncated,
    }
    return compact, truncated


def classify_effective_notes(
    *,
    effective_notes: dict[str, Any],
    report_notes: list[dict[str, Any]],
    quality_report: dict[str, Any],
    text_limit: int,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    source_by_key = build_note_index(report_notes)
    quality_by_key = load_quality_index(quality_report)
    layers: dict[str, list[dict[str, Any]]] = {
        "deep_analysis_notes": [],
        "evidence_only_notes": [],
        "metadata_only_notes": [],
        "excluded_notes": [],
    }
    truncated_count = 0
    ocr_enriched_count = 0
    comment_enriched_count = 0
    total_length = 0
    raw_notes = [note for note in effective_notes.get("notes", []) if isinstance(note, dict)]
    for note in raw_notes:
        key = note_key(note)
        source = source_by_key.get(key, {})
        quality = quality_by_key.get(key, {})
        effective_len = as_int(note.get("effective_text_length"))
        media_ocr_len = as_int(note.get("media_ocr_text_length"))
        comment_len = as_int(note.get("comment_text_length"))
        ai_score = as_int(first_value(quality.get("ai_usable_score_after"), quality.get("ai_usable_score"), note.get("ai_usable_score")))
        text_sources = note.get("text_sources", []) or []
        total_length += effective_len
        if "ocr" in text_sources and media_ocr_len > 0:
            ocr_enriched_count += 1
        if "comments" in text_sources and comment_len > 0:
            comment_enriched_count += 1
        no_identity = not note.get("note_id") and not (note.get("note_url") or source.get("url"))
        if no_identity or (effective_len == 0 and not note.get("title")):
            item, truncated = compact_effective_note(note, source, quality, "excluded", text_limit)
            item["exclude_reason"] = "缺少 note_id/链接或有效文本为空。"
            layers["excluded_notes"].append(item)
            truncated_count += int(truncated)
            continue
        if effective_len >= 200 or media_ocr_len >= 150 or comment_len >= 150 or ai_score >= 50:
            item, truncated = compact_effective_note(note, source, quality, "deep_analysis", text_limit)
            layers["deep_analysis_notes"].append(item)
            truncated_count += int(truncated)
        elif 80 <= effective_len <= 199 or interaction_score(source) > 0:
            item, truncated = compact_effective_note(note, source, quality, "evidence_only", text_limit)
            layers["evidence_only_notes"].append(item)
            truncated_count += int(truncated)
        elif effective_len < 80 and media_ocr_len == 0 and comment_len < 80:
            layers["metadata_only_notes"].append(compact_metadata_only(note, source))
        else:
            item, truncated = compact_effective_note(note, source, quality, "evidence_only", text_limit)
            layers["evidence_only_notes"].append(item)
            truncated_count += int(truncated)

    for key in ("deep_analysis_notes", "evidence_only_notes"):
        layers[key].sort(key=lambda item: (as_int(item.get("ai_usable_score")), as_int(item.get("effective_text_length")), interaction_score(item)), reverse=True)
    layers["metadata_only_notes"].sort(key=interaction_score, reverse=True)
    layers["excluded_notes"].sort(key=lambda item: as_int(item.get("effective_text_length")))

    original_counts = {key: len(value) for key, value in layers.items()}
    layers["deep_analysis_notes"] = layers["deep_analysis_notes"][:20]
    layers["evidence_only_notes"] = layers["evidence_only_notes"][:20]
    layers["metadata_only_notes"] = layers["metadata_only_notes"][:20]
    summary = {
        "total_notes": len(raw_notes),
        "deep_analysis_count": original_counts["deep_analysis_notes"],
        "evidence_only_count": original_counts["evidence_only_notes"],
        "metadata_only_count": original_counts["metadata_only_notes"],
        "excluded_count": original_counts["excluded_notes"],
        "ocr_enriched_count": ocr_enriched_count,
        "comment_enriched_count": comment_enriched_count,
        "average_effective_text_length": round(total_length / len(raw_notes), 1) if raw_notes else 0,
        "truncated_note_count": truncated_count,
    }
    if summary["deep_analysis_count"] >= 10:
        summary["analysis_mode"] = "normal_analysis"
    elif summary["deep_analysis_count"] >= 3:
        summary["analysis_mode"] = "limited_analysis"
    else:
        summary["analysis_mode"] = "insufficient_text_evidence"
    return layers, summary


def quality_before_after_counts(quality_report: dict[str, Any]) -> dict[str, int]:
    notes = quality_report.get("notes", []) if isinstance(quality_report, dict) else []
    if not isinstance(notes, list):
        notes = []
    before_deep = 0
    before_metadata = 0
    after_metadata = 0
    for note in notes:
        if not isinstance(note, dict):
            continue
        before_score = as_int(first_value(note.get("ai_usable_score_before"), note.get("ai_usable_score")))
        if before_score >= 50 or "usable_text_rich" in (note.get("quality_tags_before") or []):
            before_deep += 1
        if "metadata_only" in (note.get("quality_tags_before") or []):
            before_metadata += 1
        if "metadata_only" in (note.get("quality_tags") or []):
            after_metadata += 1
    return {
        "deep_analysis_before_count": before_deep,
        "metadata_only_before_count": before_metadata,
        "metadata_only_after_quality_count": after_metadata,
    }


def compact_note(note: dict[str, Any], content_limit: int, max_comments: int) -> dict[str, Any]:
    comments = [trim_text(item, 260) for item in (note.get("comments", []) or [])[:max_comments]]
    valuable = []
    for item in (note.get("valuable_comments", []) or [])[:max_comments]:
        valuable.append(
            {
                "comment": trim_text(item.get("comment", ""), 320),
                "value_score": item.get("value_score", 0),
                "reason": item.get("reason", ""),
                "tags": item.get("tags", []),
            }
        )
    return {
        "keyword": note.get("keyword", ""),
        "note_id": note.get("note_id", ""),
        "title": note.get("title", ""),
        "content": trim_text(note.get("content", ""), content_limit),
        "author": note.get("author", ""),
        "publish_time": note.get("publish_time", ""),
        "like_count": note.get("like_count", ""),
        "collect_count": note.get("collect_count", ""),
        "comment_count": note.get("comment_count", ""),
        "comments": comments,
        "valuable_comments": valuable,
        "images": note.get("images", [])[:6],
        "video": note.get("video", ""),
        "url": note.get("url", ""),
        "access_status": note.get("access_status", ""),
        "extraction_method": note.get("extraction_method", ""),
    }


def build_prompt(package: dict[str, Any]) -> str:
    meta = package.get("meta", {})
    quality = package.get("quality", {})
    input_layer = package.get("input_layer", {})
    quality_summary = package.get("ai_input_quality_summary", {})
    return f"""# 趋势与认知观察分析任务

你是我的“趋势与认知观察助手”。

你的目标不是复述热点，而是基于小红书公开笔记、评论和 OCR 后的图文文本，找出有证据支撑的趋势信号。

## 本次输入
- 搜索主题：{meta.get("topic", "")}
- 搜索关键词：{meta.get("query", "")}
- 本轮关注方向：{"；".join(meta.get("focus_topics", []) or ["全部方向"])}
- 排序方式：{meta.get("sort", "")}
- 采集质量状态：{quality.get("status", "unknown")}
- 笔记数量：{meta.get("note_count", 0)}
- 高价值评论数量：{meta.get("high_value_comment_count", 0)}
- 输入来源：{input_layer.get("source", "legacy")}
- 是否使用 effective-notes.json：{input_layer.get("used_effective_notes", False)}
- 分析模式：{quality_summary.get("analysis_mode", "")}
- 深度分析 note 数：{quality_summary.get("deep_analysis_count", 0)}
- 辅助证据 note 数：{quality_summary.get("evidence_only_count", 0)}
- 仅元数据 note 数：{quality_summary.get("metadata_only_count", 0)}

## 输入解释
- `effective_text` 是主要分析依据，已经合并 DOM 正文、OCR 图片文字和高价值评论文本。
- `text_sources` 标明文字来源：`dom` 表示页面正文，`ocr` 表示图片 OCR，`comments` 表示评论。
- 如果 `text_sources` 包含 `ocr`，说明这部分来自图片识别，可能有少量 OCR 误差；低置信内容必须标注。
- `deep_analysis_notes` 可用于趋势判断；`evidence_only_notes` 只能作为辅助证据；`metadata_only_notes` 只用于热度和标题参考，不要强行分析正文。

## 输出格式
请输出严格 JSON，不要 Markdown，不要代码块。结构必须包含：
{{
  "one_sentence_summary": "",
  "trend_level": "high | medium | low | insufficient",
  "top_insights": [
    {{
      "title": "",
      "type": "trend | pain_point | opportunity | risk | counterintuitive",
      "confidence": "high | medium | low",
      "evidence_note_ids": [],
      "summary": "",
      "why_it_matters": "",
      "suggested_action": ""
    }}
  ],
  "emerging_topics": [],
  "frequent_pain_points": [],
  "recommended_tracking_keywords": [],
  "risk_warnings": [],
  "evidence_map": []
}}

## 分析原则
- 每个判断必须引用 `note_id` 或评论证据。
- 趋势判断必须基于多条 note 的共同信号；单条样本不能扩大成整体趋势。
- 不要再输出“信息差、真实逻辑、认知偏差”这类空泛词，除非有具体证据。
- 如果 OCR 文本噪声较多，标记低置信度。
- 输出必须围绕具体应用场景、具体工具、具体行业、具体痛点、具体人群、具体商业动作。
- 评论区权重高于标题党；真实经历、价格、地点、时间、对比、避坑、教程优先。
- 只把样本当作趋势线索，不要把单个平台样本说成确定事实；
- 如果证据不足，请直接说明“不足以判断”；
- 不要编造数据、来源、坐标、政策或平台行为。

## 使用方法
把同目录的 `ai-input.json` 作为材料输入；优先阅读 `deep_analysis_notes`、`evidence_only_notes`、`ai_input_quality_summary`、`high_value_comments` 四个字段。
"""


def build_input_markdown(package: dict[str, Any]) -> str:
    meta = package.get("meta", {})
    quality = package.get("quality", {})
    notes = package.get("notes", []) or []
    deep_notes = package.get("deep_analysis_notes", []) or []
    evidence_notes = package.get("evidence_only_notes", []) or []
    metadata_notes = package.get("metadata_only_notes", []) or []
    quality_summary = package.get("ai_input_quality_summary", {}) or {}
    observations = package.get("observations", []) or []
    high_value_comments = package.get("high_value_comments", []) or []

    grouped: dict[str, list[dict[str, Any]]] = {}
    for note in notes:
        keyword = note.get("keyword") or "未标注关键词"
        grouped.setdefault(keyword, []).append(note)

    lines: list[str] = [
        "# 小红书趋势分析输入包",
        "",
        "## 运行概览",
        f"- 主题：{meta.get('topic', '')}",
        f"- 查询：{meta.get('query', '')}",
        f"- 生成时间：{meta.get('generated_at', '')}",
        f"- 样本笔记数：{meta.get('note_count', 0)} / {meta.get('total_note_count', 0)}",
        f"- 高价值评论数：{meta.get('high_value_comment_count', 0)}",
        f"- 采集质量：{quality.get('status', 'unknown')}",
        f"- 输入模式：{quality_summary.get('analysis_mode', '')}",
        f"- 深度分析 / 辅助证据 / 仅元数据：{quality_summary.get('deep_analysis_count', 0)} / {quality_summary.get('evidence_only_count', 0)} / {quality_summary.get('metadata_only_count', 0)}",
        f"- OCR 增强 note 数：{quality_summary.get('ocr_enriched_count', 0)}",
        "",
        "## 分析边界",
        "- 这些内容来自登录用户可见的小红书公开页面，只能作为趋势线索。",
        "- effective_text 是主分析依据，已合并 DOM 正文、OCR 图片文字和高价值评论。",
        "- text_sources 包含 ocr 时，请注意 OCR 可能有误识别；没有多条 note 共振时不要扩大为趋势。",
        "- 评论区比标题更重要；请明确指出证据不足或可能被平台样本放大的部分。",
        "- 不要编造政策、行业数据、平台机制、坐标或来源。",
        "",
        "## 深度分析 notes",
    ]

    if deep_notes:
        for index, note in enumerate(deep_notes, start=1):
            lines.extend(
                [
                    "",
                    f"### {index}. {note.get('title') or '未命名帖子'}",
                    f"- note_id：{note.get('note_id', '')}",
                    f"- 来源：{', '.join(note.get('text_sources', []) or [])}",
                    f"- 字数：effective {note.get('effective_text_length', 0)} / OCR {note.get('media_ocr_text_length', 0)} / 评论 {note.get('comment_text_length', 0)}",
                    f"- 互动：点赞 {note.get('liked_count', '')} / 收藏 {note.get('collected_count', '')} / 评论 {note.get('comment_count', '')}",
                    f"- 链接：{note.get('note_url', '')}",
                    "",
                    "effective_text：",
                    note.get("effective_text", "") or "暂无有效文本。",
                ]
            )
    elif not grouped:
        lines.append("- 暂无可分析帖子。")
    if evidence_notes:
        lines.extend(["", "## 辅助证据 notes"])
        for index, note in enumerate(evidence_notes, start=1):
            lines.extend(
                [
                    "",
                    f"### {index}. {note.get('title') or '未命名帖子'}",
                    f"- note_id：{note.get('note_id', '')}",
                    f"- 来源：{', '.join(note.get('text_sources', []) or [])}",
                    f"- 字数：effective {note.get('effective_text_length', 0)} / OCR {note.get('media_ocr_text_length', 0)} / 评论 {note.get('comment_text_length', 0)}",
                    f"- 互动：点赞 {note.get('liked_count', '')} / 收藏 {note.get('collected_count', '')} / 评论 {note.get('comment_count', '')}",
                    "",
                    "effective_text：",
                    note.get("effective_text", "") or "暂无有效文本。",
                ]
            )
    if metadata_notes:
        lines.extend(["", "## 仅元数据 notes", "", "| note_id | 标题 | 赞 | 藏 | 评 |", "| --- | --- | ---: | ---: | ---: |"])
        for note in metadata_notes[:20]:
            title = (note.get("title") or "").replace("|", " ")
            lines.append(f"| {note.get('note_id', '')} | {title} | {note.get('liked_count', 0)} | {note.get('collected_count', 0)} | {note.get('comment_count', 0)} |")

    if not deep_notes and not evidence_notes:
        lines.extend(["", "## 关键词与代表帖子"])
    grouped_items = grouped.items() if not deep_notes and not evidence_notes else []
    for keyword, items in grouped_items:
        lines.extend(["", f"### 关键词：{keyword}", f"- 代表帖子数：{len(items)}"])
        for index, note in enumerate(items[:12], start=1):
            comments = note.get("comments", []) or []
            valuable = note.get("valuable_comments", []) or []
            lines.extend(
                [
                    "",
                    f"#### {index}. {note.get('title') or '未命名帖子'}",
                    f"- note_id：{note.get('note_id', '')}",
                    f"- 作者：{note.get('author', '')}",
                    f"- 发布时间：{note.get('publish_time', '')}",
                    f"- 互动：点赞 {note.get('like_count', '')} / 收藏 {note.get('collect_count', '')} / 评论 {note.get('comment_count', '')}",
                    f"- 提取方式：{note.get('extraction_method', '')}",
                    f"- 链接：{note.get('url', '')}",
                    "",
                    "正文摘要：",
                    trim_text(note.get("content", ""), 900) or "暂无正文。",
                    "",
                    "代表评论：",
                ]
            )
            if comments:
                for comment in comments[:8]:
                    lines.append(f"- {comment}")
            else:
                lines.append("- 暂无评论。")
            if valuable:
                lines.extend(["", "初步高价值评论："])
                for item in valuable[:5]:
                    lines.append(
                        f"- [{item.get('value_score', 0)}分] {item.get('comment', '')}（{item.get('reason', '')}）"
                    )

    lines.extend(["", "## 初步趋势线索"])
    if observations:
        for item in observations[:20]:
            lines.extend(
                [
                    "",
                    f"### {item.get('phenomenon', '')}",
                    f"- 主题：{item.get('theme', '')}",
                    f"- 表面看起来：{item.get('surface_appearance', '')}",
                    f"- 初步真实逻辑：{item.get('underlying_logic', '')}",
                    f"- 为什么最近出现：{item.get('why_recent', '')}",
                    f"- 趋势：{item.get('trend_reflected', '')}",
                    f"- 是否可能夸大：{item.get('possibly_overstated', '')}",
                    f"- 值不值得深挖：{item.get('worth_deeper', '')}",
                ]
            )
    else:
        lines.append("- 暂无初步趋势。")

    lines.extend(["", "## 高价值评论候选"])
    if high_value_comments:
        for item in high_value_comments[:40]:
            lines.append(
                f"- [{item.get('value_score', 0)}分] {item.get('comment', '')} "
                f"｜主题：{item.get('theme', '')} ｜来源：{item.get('note_title', '')}"
            )
    else:
        lines.append("- 暂无高价值评论候选。")

    lines.extend(
        [
            "",
            "## 建议 Gemini 回答的问题",
            "1. 哪些现象不是单纯热点，而是能解释产业、城市、教育、消费或社会规则变化的信号？",
            "2. 哪些评论比正文更有价值？它们透露了什么真实约束？",
            "3. 哪些趋势可能被平台样本或情绪放大？",
            "4. 哪些线索值得继续扩展关键词深挖？",
            "5. 哪些结论可以直接放进网页报告，帮助用户快速判断“为什么会这样”？",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def export_package(args: argparse.Namespace) -> dict[str, Any]:
    report = load_json(args.report, {}) or {}
    audit = load_json(args.quality_audit, {}) or {}
    effective_path = Path(args.effective_notes) if args.effective_notes else Path(args.report).parent / "effective-notes.json"
    quality_path = Path(args.extraction_quality) if args.extraction_quality else Path(args.report).parent / "extraction-quality-report.json"
    effective = load_json(str(effective_path), {}) if effective_path.exists() else {}
    extraction_quality = load_json(str(quality_path), {}) if quality_path.exists() else {}
    notes = report.get("structured_notes", []) or []
    high_value_comments = report.get("high_value_comments", []) or []
    fallback_warning = ""
    layers: dict[str, list[dict[str, Any]]]
    quality_summary: dict[str, Any]
    used_effective = bool(effective and effective.get("notes"))
    if used_effective:
        layers, quality_summary = classify_effective_notes(
            effective_notes=effective,
            report_notes=notes,
            quality_report=extraction_quality,
            text_limit=args.effective_text_limit,
        )
        compact_notes = layers["deep_analysis_notes"] + layers["evidence_only_notes"]
    else:
        fallback_warning = "effective-notes.json 不存在，已回退到旧 AI 输入逻辑，图文 OCR 内容不会进入 Gemini。"
        compact_notes = [
            compact_note(note, args.content_limit, args.comments_per_note)
            for note in notes[: args.max_notes]
        ]
        layers = {
            "deep_analysis_notes": compact_notes[:20],
            "evidence_only_notes": compact_notes[20:40],
            "metadata_only_notes": [],
            "excluded_notes": [],
        }
        quality_summary = {
            "total_notes": len(notes),
            "deep_analysis_count": len(layers["deep_analysis_notes"]),
            "evidence_only_count": len(layers["evidence_only_notes"]),
            "metadata_only_count": 0,
            "excluded_count": 0,
            "ocr_enriched_count": 0,
            "comment_enriched_count": 0,
            "average_effective_text_length": 0,
            "analysis_mode": "limited_analysis" if len(compact_notes) >= 3 else "insufficient_text_evidence",
            "warning": fallback_warning,
            "truncated_note_count": 0,
        }
    quality_summary.update(quality_before_after_counts(extraction_quality))
    quality_summary["warning"] = fallback_warning
    package = {
        "meta": {
            "generated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
            "topic": report.get("meta", {}).get("topic", ""),
            "query": report.get("meta", {}).get("query", ""),
            "focus_topics": report.get("meta", {}).get("focus_topics", []),
            "sort": report.get("meta", {}).get("sort", ""),
            "source": "xiaohongshu_public_content",
            "note_count": len(compact_notes),
            "total_note_count": len(notes),
            "high_value_comment_count": len(high_value_comments),
            "analysis_language": "zh-CN",
        },
        "input_layer": {
            "source": "effective-notes.json" if used_effective else "legacy_structured_notes",
            "used_effective_notes": used_effective,
            "effective_notes_path": str(effective_path) if effective_path.exists() else "",
            "extraction_quality_path": str(quality_path) if quality_path.exists() else "",
            "fallback_warning": fallback_warning,
        },
        "safety_boundary": [
            "Only analyze public content accessible to the logged-in user.",
            "Do not infer private information or bypass verification/risk controls.",
            "Treat platform samples as signals, not definitive population-level facts.",
        ],
        "quality": {
            "status": audit.get("status", "unknown"),
            "counts": audit.get("counts", {}),
            "ratios": audit.get("ratios", {}),
            "recommendations": audit.get("recommendations", []),
            "access_status_breakdown": audit.get("access_status_breakdown", {}),
            "problem_samples": audit.get("problem_samples", []),
            "extraction_quality_summary": extraction_quality.get("summary", {}) if isinstance(extraction_quality, dict) else {},
        },
        "analysis_tasks": [
            "evidence_based_trend_detection",
            "specific_pain_points",
            "concrete_use_cases",
            "tool_industry_people_mapping",
            "risk_and_low_confidence_ocr_detection",
            "recommended_tracking_keywords",
        ],
        "ai_input_quality_summary": quality_summary,
        "existing_analysis_seed": report.get("ai_summary", {}) or {},
        "themes": report.get("themes", []) or [],
        "high_value_comments": high_value_comments[: args.max_high_value_comments],
        "observations": report.get("observations", [])[: args.max_observations],
        "deep_analysis_notes": layers["deep_analysis_notes"],
        "evidence_only_notes": layers["evidence_only_notes"],
        "metadata_only_notes": layers["metadata_only_notes"],
        "excluded_notes": layers["excluded_notes"][:20],
        "notes": compact_notes,
        "source_links": [
            {"title": note.get("title", ""), "url": note.get("note_url") or note.get("url", ""), "note_id": note.get("note_id", "")}
            for note in compact_notes
            if note.get("note_url") or note.get("url")
        ],
    }
    return package


def build_upgrade_report(package: dict[str, Any]) -> str:
    summary = package.get("ai_input_quality_summary", {}) or {}
    input_layer = package.get("input_layer", {}) or {}
    quality = package.get("quality", {}) or {}
    extraction_summary = quality.get("extraction_quality_summary", {}) if isinstance(quality.get("extraction_quality_summary"), dict) else {}
    input_chars = len(json.dumps(package, ensure_ascii=False))
    lines = [
        "# AI 输入升级报告",
        "",
        f"- 是否使用 effective-notes.json：{input_layer.get('used_effective_notes', False)}",
        f"- 输入来源：{input_layer.get('source', '')}",
        f"- fallback 是否发生：{bool(input_layer.get('fallback_warning'))}",
        f"- warning：{input_layer.get('fallback_warning') or summary.get('warning') or '无'}",
        f"- OCR 前 deep_analysis_notes 数量：{summary.get('deep_analysis_before_count', '')}",
        f"- OCR 后 deep_analysis_notes 数量：{summary.get('deep_analysis_count', 0)}",
        f"- OCR 前 metadata_only 数量：{summary.get('metadata_only_before_count', extraction_summary.get('metadata_only_before', ''))}",
        f"- OCR 后 metadata_only 数量：{summary.get('metadata_only_count', 0)}",
        f"- Gemini 输入总字符数：{input_chars}",
        f"- 被截断 note 数：{summary.get('truncated_note_count', 0)}",
        f"- 当前 analysis_mode：{summary.get('analysis_mode', '')}",
        f"- OCR 增强 note 数：{summary.get('ocr_enriched_count', 0)}",
        f"- 评论增强 note 数：{summary.get('comment_enriched_count', 0)}",
        "",
        "## 分层结果",
        "",
        f"- deep_analysis_notes：{len(package.get('deep_analysis_notes', []) or [])}（完整候选 {summary.get('deep_analysis_count', 0)}）",
        f"- evidence_only_notes：{len(package.get('evidence_only_notes', []) or [])}（完整候选 {summary.get('evidence_only_count', 0)}）",
        f"- metadata_only_notes：{len(package.get('metadata_only_notes', []) or [])}（完整候选 {summary.get('metadata_only_count', 0)}）",
        f"- excluded_notes：{len(package.get('excluded_notes', []) or [])}（完整候选 {summary.get('excluded_count', 0)}）",
    ]
    return "\n".join(lines).strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Export an AI-ready XHS analysis package and prompt.")
    parser.add_argument("--report", required=True)
    parser.add_argument("--quality-audit", default="")
    parser.add_argument("--effective-notes", default="")
    parser.add_argument("--extraction-quality", default="")
    parser.add_argument("--out", required=True)
    parser.add_argument("--prompt-out", required=True)
    parser.add_argument("--markdown-out", default="")
    parser.add_argument("--effective-out", default="")
    parser.add_argument("--effective-markdown-out", default="")
    parser.add_argument("--quality-summary-out", default="")
    parser.add_argument("--upgrade-report-out", default="")
    parser.add_argument("--max-notes", type=int, default=80)
    parser.add_argument("--max-observations", type=int, default=40)
    parser.add_argument("--max-high-value-comments", type=int, default=80)
    parser.add_argument("--comments-per-note", type=int, default=12)
    parser.add_argument("--content-limit", type=int, default=1800)
    parser.add_argument("--effective-text-limit", type=int, default=1500)
    args = parser.parse_args()

    package = export_package(args)
    write_json(args.out, package)
    out_dir = Path(args.out).parent
    compat_out = out_dir / "ai_input.json"
    if Path(args.out).resolve() != compat_out.resolve():
        write_json(str(compat_out), package)
    effective_out = Path(args.effective_out) if args.effective_out else out_dir / "ai_input_effective.json"
    effective_md_out = Path(args.effective_markdown_out) if args.effective_markdown_out else out_dir / "ai_input_effective.md"
    quality_summary_out = Path(args.quality_summary_out) if args.quality_summary_out else out_dir / "ai_input_quality_summary.json"
    upgrade_report_out = Path(args.upgrade_report_out) if args.upgrade_report_out else out_dir / "ai-input-upgrade-report.md"
    write_json(str(effective_out), package)
    write_json(str(quality_summary_out), package.get("ai_input_quality_summary", {}))
    effective_md_out.parent.mkdir(parents=True, exist_ok=True)
    effective_md_out.write_text(build_input_markdown(package), encoding="utf-8")
    upgrade_report_out.parent.mkdir(parents=True, exist_ok=True)
    upgrade_report_out.write_text(build_upgrade_report(package), encoding="utf-8")
    prompt = build_prompt(package)
    prompt_path = Path(args.prompt_out)
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt, encoding="utf-8")
    if args.markdown_out:
        markdown_path = Path(args.markdown_out)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(build_input_markdown(package), encoding="utf-8")
        print(f"AI input markdown written: {args.markdown_out}")
    print(f"AI input package written: {args.out}")
    if Path(args.out).resolve() != compat_out.resolve():
        print(f"AI input compat JSON written: {compat_out}")
    print(f"AI effective input JSON written: {effective_out}")
    print(f"AI effective input Markdown written: {effective_md_out}")
    print(f"AI input quality summary written: {quality_summary_out}")
    print(f"AI input upgrade report written: {upgrade_report_out}")
    print(f"AI analysis prompt written: {args.prompt_out}")
    print(f"ai_notes={package['meta']['note_count']}")
    print(f"ai_high_value_comments={package['meta']['high_value_comment_count']}")
    print(f"ai_analysis_mode={package.get('ai_input_quality_summary', {}).get('analysis_mode', '')}")
    warning = package.get("input_layer", {}).get("fallback_warning", "")
    if warning:
        print(f"warning={warning}")


if __name__ == "__main__":
    main()
