from __future__ import annotations

import argparse
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CN_TZ = timezone(timedelta(hours=8))
DEFAULT_MODEL = "gemini-flash-lite-latest"
GEMINI_API_KEY_ENV_NAMES = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
LEGACY_GEMINI_API_KEY_ENV_NAMES = ("GOOGLE_AI_API_KEY",)
CHECKED_GEMINI_API_KEY_ENV_NAMES = GEMINI_API_KEY_ENV_NAMES + LEGACY_GEMINI_API_KEY_ENV_NAMES
ANALYSIS_KEYS = [
    "one_sentence_summary",
    "trend_level",
    "top_insights",
    "emerging_topics",
    "frequent_pain_points",
    "recommended_tracking_keywords",
    "risk_warnings",
    "evidence_map",
    "top_findings",
    "high_value_comments",
    "hidden_comment_insights",
    "info_gap_rankings",
    "cognitive_upgrades",
    "trend_clusters",
    "controversies",
    "counterintuitive_findings",
    "next_search_keywords",
    "source_post_refs",
    "risk_notes",
]
SCALAR_ANALYSIS_KEYS = {"one_sentence_summary", "trend_level"}
NOTE_ID_RE = re.compile(r"\b[0-9a-f]{20,32}\b", re.IGNORECASE)


class GeminiTimeoutError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def resolve_path(path: str | Path) -> Path:
    file_path = Path(path)
    if not file_path.is_absolute():
        file_path = Path.cwd() / file_path
    return file_path


def load_env_file(path: str) -> dict[str, Any]:
    env_path = resolve_path(path) if path else None
    meta: dict[str, Any] = {
        "env_file": str(env_path) if env_path else "",
        "env_file_exists": bool(env_path and env_path.exists()),
        "loaded_env_vars": [],
        "skipped_env_vars": [],
    }
    if not env_path or not env_path.exists():
        return meta
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        value = value.strip().strip('"').strip("'")
        if key and not os.environ.get(key, "").strip():
            os.environ[key] = value
            meta["loaded_env_vars"].append(key)
        elif key:
            meta["skipped_env_vars"].append(key)
    return meta


def get_gemini_api_key(env_meta: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    key_meta = {
        "env_file": env_meta.get("env_file", ""),
        "env_file_exists": bool(env_meta.get("env_file_exists")),
        "checked_env_vars": list(CHECKED_GEMINI_API_KEY_ENV_NAMES),
        "loaded_env_vars": env_meta.get("loaded_env_vars", []),
        "api_key_source": "",
        "api_key_present": False,
    }
    for name in CHECKED_GEMINI_API_KEY_ENV_NAMES:
        value = os.environ.get(name, "").strip().strip('"').strip("'")
        if value:
            key_meta["api_key_source"] = name
            key_meta["api_key_present"] = True
            return value, key_meta
    return "", key_meta


def missing_api_key_message(key_meta: dict[str, Any]) -> str:
    env_file = key_meta.get("env_file") or "未指定"
    exists = "存在" if key_meta.get("env_file_exists") else "不存在"
    checked = ", ".join(key_meta.get("checked_env_vars") or CHECKED_GEMINI_API_KEY_ENV_NAMES)
    return (
        "Missing Gemini API key. "
        f"当前读取的 .env 文件：{env_file}（{exists}）。"
        f"已检查环境变量：{checked}。"
        "请在 .env 中填写 GEMINI_API_KEY=你的Gemini_API_Key，"
        "也支持 GOOGLE_API_KEY=你的Gemini_API_Key；保存后用 --env-file .env 重新运行。"
    )


def ensure_effective_ai_input(input_json_path: str, ai_input_json: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    if not input_json_path:
        return ai_input_json, "", ""
    primary_path = resolve_path(input_json_path)
    run_dir = primary_path.parent
    effective_notes = run_dir / "effective-notes.json"
    effective_input = run_dir / "ai_input_effective.json"
    input_layer = ai_input_json.get("input_layer") if isinstance(ai_input_json, dict) else {}
    if not effective_notes.exists():
        return ai_input_json, str(primary_path), ""
    if isinstance(input_layer, dict) and input_layer.get("used_effective_notes"):
        return ai_input_json, str(primary_path), "effective_input_confirmed"
    if effective_input.exists():
        effective_json = load_json(str(effective_input), {}) or {}
        effective_layer = effective_json.get("input_layer") if isinstance(effective_json, dict) else {}
        if isinstance(effective_layer, dict) and effective_layer.get("used_effective_notes"):
            return effective_json, str(effective_input.resolve()), "effective_input_auto_selected"
    return ai_input_json, str(primary_path), "effective_notes_exist_but_effective_ai_input_missing"


def load_json(path: str, default: Any = None) -> Any:
    if not path:
        return default
    file_path = Path(path)
    if not file_path.exists():
        return default
    return json.loads(file_path.read_text(encoding="utf-8"))


def write_json(path: str | Path, data: Any) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: str | Path, text: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


def trim_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def first_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        raise ValueError("Gemini response did not contain a JSON object")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise ValueError("Gemini response JSON object was incomplete")


def extract_json(text: str) -> dict[str, Any]:
    clean = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", clean, re.DOTALL | re.IGNORECASE)
    if fence:
        clean = fence.group(1).strip()
    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        data = json.loads(first_json_object(clean))
    if not isinstance(data, dict):
        raise ValueError("Gemini response JSON must be an object")
    return data


def note_ids_from_value(value: Any) -> list[str]:
    ids: list[str] = []
    if isinstance(value, str):
        ids.extend(NOTE_ID_RE.findall(value))
    elif isinstance(value, dict):
        for key in ("note_id", "note_ids", "evidence_note_ids", "source_note_ids", "source_post_refs", "evidence"):
            ids.extend(note_ids_from_value(value.get(key)))
    elif isinstance(value, list):
        for item in value:
            ids.extend(note_ids_from_value(item))
    seen: set[str] = set()
    unique: list[str] = []
    for item in ids:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def evidence_note_id_pool(analysis: dict[str, Any]) -> list[list[str]]:
    pool: list[list[str]] = []
    for key in ("evidence_map", "source_post_refs", "top_findings"):
        for item in analysis.get(key) or []:
            ids = note_ids_from_value(item)
            if ids:
                pool.append(ids)
    return pool


def normalize_top_insights_with_evidence(analysis: dict[str, Any]) -> None:
    raw_insights = analysis.get("top_insights") or []
    if not isinstance(raw_insights, list):
        raw_insights = []
    pool = evidence_note_id_pool(analysis)
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw_insights):
        if isinstance(item, dict):
            insight = dict(item)
            title = str(insight.get("title") or insight.get("summary") or f"趋势洞察 {index + 1}").strip()
            summary = str(insight.get("summary") or insight.get("one_line") or title).strip()
        else:
            title = str(item or f"趋势洞察 {index + 1}").strip()
            summary = title
            insight = {}
        ids = note_ids_from_value(insight.get("evidence_note_ids"))
        if not ids:
            ids = note_ids_from_value(insight.get("evidence"))
        if not ids and index < len(pool):
            ids = pool[index]
        if not ids and pool:
            ids = pool[0]
        normalized.append(
            {
                "title": title,
                "type": str(insight.get("type") or "trend").strip(),
                "confidence": insight.get("confidence") or "medium",
                "evidence_note_ids": ids,
                "summary": summary,
                "why_it_matters": str(insight.get("why_it_matters") or "").strip(),
                "suggested_action": str(insight.get("suggested_action") or "").strip(),
            }
        )
    analysis["top_insights"] = normalized


def empty_analysis(model: str, source_input: str, status: str = "not_run", errors: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    analysis = {
        "meta": {
            "analysis_provider": "gemini",
            "model": model,
            "generated_at": now_iso(),
            "source_input": str(Path(source_input).resolve()) if source_input else "",
            "ai_status": status,
            "error_count": len(errors or []),
        }
    }
    for key in ANALYSIS_KEYS:
        analysis[key] = "" if key in SCALAR_ANALYSIS_KEYS else []
    if errors:
        analysis["meta"]["last_error"] = errors[-1].get("message", "")
    return analysis


def normalize_analysis(
    data: dict[str, Any],
    model: str,
    source_input: str,
    status: str = "ok",
    extra_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = empty_analysis(model, source_input, status=status)
    normalized["meta"].update(extra_meta or {})
    for key in ANALYSIS_KEYS:
        value = data.get(key, "" if key in SCALAR_ANALYSIS_KEYS else [])
        if key in SCALAR_ANALYSIS_KEYS:
            normalized[key] = str(value or "")
        else:
            normalized[key] = value if isinstance(value, list) else []
    normalize_top_insights_with_evidence(normalized)
    if normalized.get("top_insights") and not normalized.get("top_findings"):
        normalized["top_findings"] = [
            {
                "title": item.get("title", "") if isinstance(item, dict) else str(item),
                "one_line": item.get("summary", "") if isinstance(item, dict) else str(item),
                "why_valuable": item.get("why_it_matters", "") if isinstance(item, dict) else "",
                "evidence": item.get("evidence_note_ids", []) if isinstance(item, dict) else [],
                "note_id": ",".join(item.get("evidence_note_ids", [])) if isinstance(item, dict) else "",
                "confidence": item.get("confidence", "") if isinstance(item, dict) else "",
            }
            for item in normalized["top_insights"]
        ]
    if normalized.get("recommended_tracking_keywords") and not normalized.get("next_search_keywords"):
        normalized["next_search_keywords"] = normalized["recommended_tracking_keywords"]
    if normalized.get("risk_warnings") and not normalized.get("risk_notes"):
        normalized["risk_notes"] = normalized["risk_warnings"]
    return normalized


def response_text(response: dict[str, Any]) -> str:
    candidates = response.get("candidates") or []
    if not candidates:
        raise ValueError("Gemini response has no candidates")
    parts = candidates[0].get("content", {}).get("parts") or []
    text = "\n".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()
    if not text:
        raise ValueError("Gemini response candidate has no text")
    return text


def is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout, GeminiTimeoutError)):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        return isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason).lower()
    return "timed out" in str(exc).lower()


def call_gemini_once(api_key: str, model: str, prompt: str, timeout: int) -> dict[str, Any]:
    model_name = model.removeprefix("models/")
    encoded_model = urllib.parse.quote(model_name, safe="")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{encoded_model}:generateContent?key={urllib.parse.quote(api_key)}"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.25,
            "topP": 0.9,
            "responseMimeType": "application/json",
        },
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini API HTTP {exc.code}: {body[:800]}") from exc
    except urllib.error.URLError as exc:
        if is_timeout_error(exc):
            raise GeminiTimeoutError(f"Gemini read timed out after {timeout}s") from exc
        raise RuntimeError(f"Gemini API failed: {exc}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise GeminiTimeoutError(f"Gemini read timed out after {timeout}s") from exc


def call_gemini_with_retry(
    api_key: str,
    model: str,
    prompt: str,
    timeout: int,
    retries: int,
    retry_wait_seconds: int,
    errors: list[dict[str, Any]],
    label: str,
) -> dict[str, Any] | None:
    for attempt in range(retries + 1):
        try:
            return call_gemini_once(api_key, model, prompt, timeout)
        except Exception as exc:  # noqa: BLE001 - all Gemini errors should become artifacts, not crashes.
            status = "timeout" if is_timeout_error(exc) else "failed"
            errors.append(
                {
                    "label": label,
                    "attempt": attempt + 1,
                    "ai_status": status,
                    "message": str(exc),
                    "timestamp": now_iso(),
                }
            )
            if attempt < retries:
                time.sleep(retry_wait_seconds)
            else:
                return None
    return None


def compact_comment(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return {
            "comment": trim_text(item.get("comment", ""), 220),
            "value_score": item.get("value_score", item.get("score", "")),
            "reason": trim_text(item.get("reason") or item.get("why_high_value", ""), 160),
            "tags": item.get("tags", []),
        }
    return {"comment": trim_text(item, 220)}


def compact_note(note: dict[str, Any]) -> dict[str, Any]:
    valuable = note.get("valuable_comments") or []
    comments = note.get("comments") or []
    selected_comments = [compact_comment(item) for item in valuable[:5]]
    if len(selected_comments) < 5:
        selected_comments.extend(compact_comment(item) for item in comments[: 5 - len(selected_comments)])
    effective_text = note.get("effective_text") or note.get("content") or note.get("content_summary", "")
    return {
        "keyword": note.get("keyword", ""),
        "note_id": note.get("note_id", ""),
        "title": trim_text(note.get("title", ""), 120),
        "content_summary": trim_text(effective_text, 900),
        "effective_text": trim_text(effective_text, 1500),
        "effective_text_length": note.get("effective_text_length", ""),
        "text_sources": note.get("text_sources", []),
        "dom_text_length": note.get("dom_text_length", ""),
        "media_ocr_text_length": note.get("media_ocr_text_length", ""),
        "comment_text_length": note.get("comment_text_length", ""),
        "ai_usable_score": note.get("ai_usable_score", ""),
        "analysis_layer": note.get("analysis_layer", ""),
        "author": note.get("author", ""),
        "publish_time": note.get("publish_time", ""),
        "like_count": note.get("liked_count", note.get("like_count", "")),
        "collect_count": note.get("collected_count", note.get("collect_count", "")),
        "comment_count": note.get("comment_count", ""),
        "high_value_comments": selected_comments[:5],
        "url": note.get("note_url", note.get("url", "")),
        "access_status": note.get("access_status", ""),
        "extraction_method": note.get("extraction_method", ""),
    }


def compact_package(ai_input_json: dict[str, Any]) -> dict[str, Any]:
    deep_notes = ai_input_json.get("deep_analysis_notes", []) if isinstance(ai_input_json, dict) else []
    evidence_notes = ai_input_json.get("evidence_only_notes", []) if isinstance(ai_input_json, dict) else []
    metadata_notes = ai_input_json.get("metadata_only_notes", []) if isinstance(ai_input_json, dict) else []
    if deep_notes or evidence_notes:
        notes = list(deep_notes[:20]) + list(evidence_notes[:20])
    else:
        notes = ai_input_json.get("notes", []) if isinstance(ai_input_json, dict) else []
    observations = ai_input_json.get("observations", []) if isinstance(ai_input_json, dict) else []
    high_value_comments = ai_input_json.get("high_value_comments", []) if isinstance(ai_input_json, dict) else []
    compact = {
        "meta": ai_input_json.get("meta", {}) if isinstance(ai_input_json, dict) else {},
        "input_layer": ai_input_json.get("input_layer", {}) if isinstance(ai_input_json, dict) else {},
        "quality": ai_input_json.get("quality", {}) if isinstance(ai_input_json, dict) else {},
        "ai_input_quality_summary": ai_input_json.get("ai_input_quality_summary", {}) if isinstance(ai_input_json, dict) else {},
        "themes": ai_input_json.get("themes", []) if isinstance(ai_input_json, dict) else [],
        "notes": [compact_note(note) for note in notes],
        "deep_analysis_note_count": len(deep_notes),
        "evidence_only_note_count": len(evidence_notes),
        "metadata_only_notes": metadata_notes[:20],
        "observations": [
            {
                "phenomenon": trim_text(item.get("phenomenon", ""), 120),
                "theme": item.get("theme", ""),
                "research_score": item.get("research_score", ""),
                "surface_appearance": trim_text(item.get("surface_appearance", ""), 220),
                "underlying_logic": trim_text(item.get("underlying_logic", ""), 260),
                "why_recent": trim_text(item.get("why_recent", ""), 180),
                "trend_reflected": trim_text(item.get("trend_reflected", ""), 180),
                "possibly_overstated": trim_text(item.get("possibly_overstated", ""), 160),
                "source": item.get("source", {}),
            }
            for item in observations[:40]
        ],
        "high_value_comments": [compact_comment(item) for item in high_value_comments[:80]],
    }
    compact["meta"]["compact_note_count"] = len(compact["notes"])
    compact["meta"]["deep_analysis_note_count"] = len(deep_notes)
    compact["meta"]["evidence_only_note_count"] = len(evidence_notes)
    return compact


def chunks(items: list[Any], size: int) -> list[list[Any]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def build_part_prompt(compact: dict[str, Any], notes: list[dict[str, Any]], part_number: int, part_count: int) -> str:
    payload = {
        "meta": compact.get("meta", {}),
        "input_layer": compact.get("input_layer", {}),
        "quality": compact.get("quality", {}),
        "ai_input_quality_summary": compact.get("ai_input_quality_summary", {}),
        "themes": compact.get("themes", []),
        "notes": notes,
        "metadata_only_notes": compact.get("metadata_only_notes", []),
    }
    return f"""你是“小红书信息差发现仪表盘”的分析器。
只基于下面压缩后的公开帖子、评论和 OCR 后的 effective_text 做分析，不要编造外部数据。
这是第 {part_number}/{part_count} 块。`effective_text` 是主依据；`text_sources` 包含 ocr 时代表来自图片 OCR，可能有少量误差。

输出严格 JSON，不要 Markdown，不要代码块。卡片字段尽量短，适合网页 3-5 行展示。
{{
  "one_sentence_summary": "一句话总结，必须基于多条 note",
  "trend_level": "high/medium/low/insufficient",
  "top_insights": [{{"title":"短标题","type":"trend/pain_point/opportunity/risk/counterintuitive","confidence":"high/medium/low","evidence_note_ids":["note_id"],"summary":"具体结论","why_it_matters":"为什么重要","suggested_action":"建议动作"}}],
  "emerging_topics": ["新出现的具体主题"],
  "frequent_pain_points": ["反复出现的具体痛点"],
  "recommended_tracking_keywords": ["下一步追踪关键词"],
  "risk_warnings": ["样本偏差/OCR噪声/低置信风险"],
  "evidence_map": [{{"note_id":"note id","claim":"该 note 支撑的判断","text_sources":["dom","ocr","comments"],"confidence":"high/medium/low"}}],
  "top_findings": [{{"title":"短标题","one_line":"一句话结论","why_valuable":"为什么值得看","evidence":["帖子或评论证据"],"note_id":"来源 note_id","confidence":"high/medium/low"}}],
  "high_value_comments": [{{"comment":"评论原文短摘录","why_high_value":"它暴露的现实约束/反例/经验","source_title":"帖子标题","note_id":"来源 note_id","score":0}}],
  "hidden_comment_insights": [{{"title":"评论区隐藏信息","one_line":"从评论区看出的结论","evidence":["评论证据"],"confidence":"high/medium/low"}}],
  "info_gap_rankings": [{{"title":"信息差标题","surface":"表面看起来是什么","real_logic":"背后真实逻辑","why_now":"为什么最近出现","trend":"反映趋势","overstated_risk":"是否可能夸大","dig_deeper":"值不值得继续深挖","evidence":["证据"]}}],
  "cognitive_upgrades": [{{"title":"认知升级点","one_line":"一句话升级","from_to":"从什么理解转向什么理解","evidence":["证据"]}}],
  "trend_clusters": [{{"cluster":"趋势聚类名","one_line":"这个簇说明什么","signals":["信号词"],"sample_count":0,"confidence":"high/medium/low"}}],
  "controversies": [{{"title":"争议点","sides":["观点A","观点B"],"why_it_matters":"为什么重要","evidence":["证据"]}}],
  "counterintuitive_findings": [{{"title":"反常识发现","one_line":"反常识结论","why_counterintuitive":"为什么反常识","evidence":["证据"],"confidence":"high/medium/low"}}],
  "next_search_keywords": ["下一步关键词"],
  "source_post_refs": [{{"note_id":"note id","title":"帖子标题","why_referenced":"为什么被引用"}}],
  "risk_notes": ["样本偏差或需核验处"]
}}

要求：
- 每个判断必须引用 note_id 或评论证据。
- 趋势判断必须来自多条 note 的共同信号；单条样本只能写为个案。
- 不要写“信息差、真实逻辑、认知偏差”等空泛词，除非附具体证据。
- 优先输出具体应用场景、工具、行业、痛点、人群、商业动作。
- OCR 噪声较多时标记 low confidence。
- 证据不足就写“样本不足，暂不判断”。

压缩输入：
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""


def build_summary_prompt(compact: dict[str, Any], part_analyses: list[dict[str, Any]]) -> str:
    payload = {
        "meta": compact.get("meta", {}),
        "quality": compact.get("quality", {}),
        "input_layer": compact.get("input_layer", {}),
        "ai_input_quality_summary": compact.get("ai_input_quality_summary", {}),
        "themes": compact.get("themes", []),
        "parts": [
            {key: analysis.get(key, []) for key in ANALYSIS_KEYS}
            for analysis in part_analyses
            if analysis.get("meta", {}).get("ai_status") == "ok"
        ],
    }
    return f"""你是“小红书信息差发现仪表盘”的总汇总器。
下面是多个 Gemini 分块分析结果。请去重、合并、保留最强证据，输出最终严格 JSON。
不要写长文章，字段内容要适合 P1-P6 仪表盘短卡片。
必须保留 note_id 证据，不要把单条样本扩大为整体趋势。

输出字段与分块完全一致：
{{
  "one_sentence_summary": "",
  "trend_level": "",
  "top_insights": [],
  "emerging_topics": [],
  "frequent_pain_points": [],
  "recommended_tracking_keywords": [],
  "risk_warnings": [],
  "evidence_map": [],
  "top_findings": [],
  "high_value_comments": [],
  "hidden_comment_insights": [],
  "info_gap_rankings": [],
  "cognitive_upgrades": [],
  "trend_clusters": [],
  "controversies": [],
  "counterintuitive_findings": [],
  "next_search_keywords": [],
  "source_post_refs": [],
  "risk_notes": []
}}

汇总输入：
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""


def stringify_item(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        parts = []
        for key in [
            "title",
            "one_line",
            "comment",
            "why_valuable",
            "why_high_value",
            "source_title",
            "surface",
            "real_logic",
            "trend",
            "cluster",
            "reason",
            "evidence",
            "why_now",
            "next_search_keywords",
        ]:
            value = item.get(key)
            if isinstance(value, list):
                value = "；".join(str(part) for part in value if part)
            if value:
                parts.append(str(value))
        return "；".join(parts) if parts else json.dumps(item, ensure_ascii=False)
    return str(item)


def build_markdown(analysis: dict[str, Any], title: str = "Gemini 分析结果") -> str:
    meta = analysis.get("meta", {})
    lines = [
        f"# {title}",
        "",
        f"- 分析器：{meta.get('analysis_provider', 'gemini')}",
        f"- 模型：{meta.get('model', '')}",
        f"- 状态：{meta.get('ai_status', '')}",
        f"- 生成时间：{meta.get('generated_at', '')}",
    ]
    if meta.get("last_error"):
        lines.append(f"- 错误：{meta.get('last_error')}")
    for section, key in [
        ("P1 今日最有价值发现", "top_findings"),
        ("高价值评论", "high_value_comments"),
        ("评论区隐藏信息", "hidden_comment_insights"),
        ("P3 信息差排行榜", "info_gap_rankings"),
        ("认知升级点", "cognitive_upgrades"),
        ("P4 趋势聚类", "trend_clusters"),
        ("争议话题", "controversies"),
        ("P5 反常识发现", "counterintuitive_findings"),
        ("下一步搜索关键词", "next_search_keywords"),
        ("来源帖子索引", "source_post_refs"),
        ("风险与夸大可能", "risk_notes"),
    ]:
        lines.extend(["", f"## {section}"])
        values = analysis.get(key, []) or []
        if not values:
            lines.append("- 暂无。")
        for item in values[:12]:
            lines.append(f"- {stringify_item(item)}")
    return "\n".join(lines).strip() + "\n"


def write_compact_markdown(path: Path, compact: dict[str, Any]) -> None:
    lines = [
        "# Gemini 压缩输入",
        "",
        f"- 样本数：{len(compact.get('notes', []))}",
        f"- deep_analysis_notes：{compact.get('deep_analysis_note_count', 0)}",
        f"- evidence_only_notes：{compact.get('evidence_only_note_count', 0)}",
        f"- 输入来源：{compact.get('input_layer', {}).get('source', '')}",
        f"- analysis_mode：{compact.get('ai_input_quality_summary', {}).get('analysis_mode', '')}",
        f"- 查询：{compact.get('meta', {}).get('query', '')}",
        "",
    ]
    for index, note in enumerate(compact.get("notes", []), start=1):
        lines.extend(
            [
                f"## {index}. {note.get('title', '未命名帖子')}",
                f"- note_id：{note.get('note_id', '')}",
                f"- 分层：{note.get('analysis_layer', '')}",
                f"- 文本来源：{', '.join(note.get('text_sources', []) or [])}",
                f"- 字数：effective {note.get('effective_text_length', '')} / OCR {note.get('media_ocr_text_length', '')} / 评论 {note.get('comment_text_length', '')}",
                f"- 互动：赞 {note.get('like_count', '')} / 藏 {note.get('collect_count', '')} / 评 {note.get('comment_count', '')}",
                f"- 链接：{note.get('url', '')}",
                f"- effective_text：{note.get('effective_text') or note.get('content_summary', '') or '暂无'}",
                "- 高价值评论：",
            ]
        )
        comments = note.get("high_value_comments", []) or []
        if comments:
            for item in comments[:5]:
                lines.append(f"  - {item.get('comment', '')}")
        else:
            lines.append("  - 暂无")
        lines.append("")
    write_text(path, "\n".join(lines).strip() + "\n")


def numeric_count(value: Any) -> int:
    raw = str(value or "").strip().lower()
    if not raw:
        return 0
    multiplier = 1
    if raw.endswith("万"):
        multiplier = 10000
        raw = raw[:-1]
    elif raw.endswith("k"):
        multiplier = 1000
        raw = raw[:-1]
    raw = raw.replace(",", "")
    try:
        return int(float(raw) * multiplier)
    except ValueError:
        return 0


def merge_local_fallback(compact: dict[str, Any], model: str, source_input: str, errors: list[dict[str, Any]]) -> dict[str, Any]:
    notes = compact.get("notes", []) or []
    sorted_notes = sorted(
        notes,
        key=lambda note: numeric_count(note.get("like_count")) + numeric_count(note.get("collect_count")) * 2 + numeric_count(note.get("comment_count")) * 3,
        reverse=True,
    )
    high_comments = []
    for note in notes:
        for item in note.get("high_value_comments", []) or []:
            if item.get("comment"):
                high_comments.append(
                    {
                        "comment": item.get("comment", ""),
                        "why_high_value": item.get("reason") or "本地统计：来自评论区的高价值候选。",
                        "source_title": note.get("title", ""),
                        "note_id": note.get("note_id", ""),
                        "score": item.get("value_score", 0),
                    }
                )
    analysis = empty_analysis(model, source_input, status=errors[-1].get("ai_status", "failed") if errors else "failed", errors=errors)
    analysis["top_findings"] = [
        {
            "title": note.get("title", ""),
            "one_line": "本地降级：按点赞、收藏、评论加权排序出的高互动样本。",
            "why_valuable": "Gemini 未完成时，可先用互动强度作为弱线索，不代表真实价值。",
            "note_id": note.get("note_id", ""),
            "evidence": [f"赞 {note.get('like_count', '')} / 藏 {note.get('collect_count', '')} / 评 {note.get('comment_count', '')}"],
            "confidence": "low",
        }
        for note in sorted_notes[:5]
    ]
    analysis["one_sentence_summary"] = "Gemini 未完成；当前仅保留本地高互动与 effective_text 分层线索。"
    analysis["trend_level"] = "insufficient"
    analysis["top_insights"] = [
        {
            "title": item.get("title", ""),
            "type": "risk",
            "confidence": "low",
            "evidence_note_ids": [item.get("note_id", "")] if item.get("note_id") else [],
            "summary": item.get("one_line", ""),
            "why_it_matters": item.get("why_valuable", ""),
            "suggested_action": "等待 Gemini 成功后再做趋势判断。",
        }
        for item in analysis["top_findings"]
    ]
    analysis["emerging_topics"] = [item.get("theme", "") for item in compact.get("themes", [])[:10] if item.get("theme")]
    analysis["frequent_pain_points"] = []
    analysis["recommended_tracking_keywords"] = analysis["emerging_topics"]
    analysis["evidence_map"] = [
        {
            "note_id": note.get("note_id", ""),
            "claim": "本地高互动候选，不能替代 AI 趋势判断。",
            "text_sources": note.get("text_sources", []),
            "confidence": "low",
        }
        for note in sorted_notes[:12]
    ]
    analysis["high_value_comments"] = high_comments[:12]
    analysis["hidden_comment_insights"] = [
        {
            "title": row.get("source_title", "评论区线索"),
            "one_line": row.get("why_high_value", "评论区候选线索。"),
            "evidence": [row.get("comment", "")],
            "confidence": "low",
        }
        for row in high_comments[:8]
    ]
    analysis["info_gap_rankings"] = [
        {
            "title": item.get("phenomenon", "本地信息差候选"),
            "surface": item.get("surface_appearance", ""),
            "real_logic": item.get("underlying_logic", ""),
            "why_now": item.get("why_recent", ""),
            "trend": item.get("trend_reflected", ""),
            "overstated_risk": item.get("possibly_overstated", ""),
            "dig_deeper": "Gemini 未完成，请结合原帖核验。",
            "evidence": [],
        }
        for item in (compact.get("observations", []) or [])[:8]
    ]
    analysis["trend_clusters"] = [
        {
            "cluster": item.get("theme", ""),
            "one_line": f"本地统计：该主题出现 {item.get('count', 0)} 条。",
            "signals": item.get("top_signals", []) or [],
            "sample_count": item.get("count", 0),
            "confidence": "low",
        }
        for item in compact.get("themes", [])[:10]
        if item.get("theme")
    ]
    analysis["next_search_keywords"] = [item.get("theme", "") for item in compact.get("themes", [])[:10] if item.get("theme")]
    analysis["source_post_refs"] = [
        {"note_id": note.get("note_id", ""), "title": note.get("title", ""), "why_referenced": "本地高互动候选"}
        for note in sorted_notes[:12]
    ]
    analysis["risk_notes"] = [errors[-1].get("message", "Gemini 分析失败") if errors else "Gemini 分析失败"]
    analysis["risk_warnings"] = analysis["risk_notes"]
    return analysis


def derive_paths(out_json: str, out_md: str) -> dict[str, Path]:
    out_json_path = Path(out_json)
    out_md_path = Path(out_md)
    base = out_json_path.parent
    return {
        "out_json": out_json_path,
        "out_md": out_md_path,
        "compact_md": base / "ai_input_compact.md",
        "summary_json": base / "ai_output_summary.json",
        "summary_md": base / "ai_output_summary.md",
        "errors": base / "ai_errors.json",
    }


def run_analysis(args: argparse.Namespace) -> int:
    paths = derive_paths(args.out_json, args.out_md)
    env_meta = load_env_file(args.env_file)
    errors: list[dict[str, Any]] = []
    api_key, key_meta = get_gemini_api_key(env_meta)
    ai_input_json = load_json(args.input_json, {}) or {}
    ai_input_json, input_json_used, input_selection_status = ensure_effective_ai_input(args.input_json, ai_input_json)
    compact = compact_package(ai_input_json)
    write_compact_markdown(paths["compact_md"], compact)
    notes = compact.get("notes", []) or []
    note_chunks = chunks(notes, max(1, args.chunk_size))
    if not note_chunks:
        note_chunks = [[]]

    part_analyses: list[dict[str, Any]] = []
    if not api_key:
        errors.append(
            {
                "label": "gemini",
                "attempt": 0,
                "ai_status": "missing_api_key",
                "message": missing_api_key_message(key_meta),
                "timestamp": now_iso(),
                "env_file": key_meta.get("env_file", ""),
                "env_file_exists": key_meta.get("env_file_exists", False),
                "checked_env_vars": key_meta.get("checked_env_vars", []),
                "loaded_env_vars": key_meta.get("loaded_env_vars", []),
            }
        )
        for index, note_chunk in enumerate(note_chunks, start=1):
            part_label = f"part_{index:03d}"
            part_json = paths["out_json"].parent / f"ai_output_part_{index:03d}.json"
            part_md = paths["out_md"].parent / f"ai_output_part_{index:03d}.md"
            analysis = empty_analysis(args.model, args.input_md, status="missing_api_key", errors=[errors[-1]])
            analysis["meta"].update(
                {
                    "part_number": index,
                    "part_count": len(note_chunks),
                    "chunk_size": len(note_chunk),
                    "env_file": key_meta.get("env_file", ""),
                    "env_file_exists": key_meta.get("env_file_exists", False),
                    "checked_env_vars": key_meta.get("checked_env_vars", []),
                    "api_key_present": False,
                    "input_json_used": input_json_used,
                    "input_selection_status": input_selection_status,
                    "used_effective_input": bool(compact.get("input_layer", {}).get("used_effective_notes")),
                }
            )
            write_json(part_json, analysis)
            write_text(part_md, build_markdown(analysis, title=f"Gemini 分块分析 {index:03d}"))
            part_analyses.append(analysis)
    else:
        for index, note_chunk in enumerate(note_chunks, start=1):
            part_label = f"part_{index:03d}"
            part_json = paths["out_json"].parent / f"ai_output_part_{index:03d}.json"
            part_md = paths["out_md"].parent / f"ai_output_part_{index:03d}.md"
            prompt = build_part_prompt(compact, note_chunk, index, len(note_chunks))
            response = call_gemini_with_retry(api_key, args.model, prompt, args.timeout, args.retries, args.retry_wait_seconds, errors, part_label)
            if response is None:
                status = errors[-1].get("ai_status", "failed") if errors else "failed"
                analysis = empty_analysis(args.model, args.input_md, status=status, errors=[err for err in errors if err.get("label") == part_label])
                analysis["meta"].update(
                    {
                        "part_number": index,
                        "part_count": len(note_chunks),
                        "chunk_size": len(note_chunk),
                        "api_key_source": key_meta.get("api_key_source", ""),
                        "api_key_present": True,
                        "input_json_used": input_json_used,
                        "input_selection_status": input_selection_status,
                        "used_effective_input": bool(compact.get("input_layer", {}).get("used_effective_notes")),
                    }
                )
            else:
                try:
                    analysis = normalize_analysis(
                        extract_json(response_text(response)),
                        args.model,
                        args.input_md,
                        status="ok",
                        extra_meta={
                            "part_number": index,
                            "part_count": len(note_chunks),
                            "chunk_size": len(note_chunk),
                            "api_key_source": key_meta.get("api_key_source", ""),
                            "api_key_present": True,
                            "input_json_used": input_json_used,
                            "input_selection_status": input_selection_status,
                            "used_effective_input": bool(compact.get("input_layer", {}).get("used_effective_notes")),
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append({"label": part_label, "attempt": 1, "ai_status": "failed", "message": str(exc), "timestamp": now_iso()})
                    analysis = empty_analysis(args.model, args.input_md, status="failed", errors=[errors[-1]])
            write_json(part_json, analysis)
            write_text(part_md, build_markdown(analysis, title=f"Gemini 分块分析 {index:03d}"))
            part_analyses.append(analysis)

    successful_parts = [item for item in part_analyses if item.get("meta", {}).get("ai_status") == "ok"]
    summary: dict[str, Any]
    if api_key and successful_parts:
        summary_prompt = build_summary_prompt(compact, successful_parts)
        response = call_gemini_with_retry(api_key, args.model, summary_prompt, args.timeout, args.retries, args.retry_wait_seconds, errors, "summary")
        if response is not None:
            try:
                summary = normalize_analysis(
                    extract_json(response_text(response)),
                    args.model,
                    args.input_md,
                    status="ok" if not errors else "partial",
                    extra_meta={
                        "chunked": len(note_chunks) > 1,
                        "part_count": len(note_chunks),
                        "successful_part_count": len(successful_parts),
                        "compact_input": str(paths["compact_md"].resolve()),
                        "api_key_source": key_meta.get("api_key_source", ""),
                        "api_key_present": True,
                        "input_json_used": input_json_used,
                        "input_selection_status": input_selection_status,
                        "used_effective_input": bool(compact.get("input_layer", {}).get("used_effective_notes")),
                    },
                )
            except Exception as exc:  # noqa: BLE001
                errors.append({"label": "summary", "attempt": 1, "ai_status": "failed", "message": str(exc), "timestamp": now_iso()})
                summary = merge_local_fallback(compact, args.model, args.input_md, errors)
        else:
            summary = merge_local_fallback(compact, args.model, args.input_md, errors)
    else:
        summary = merge_local_fallback(compact, args.model, args.input_md, errors)

    summary["meta"].update(
        {
            "timeout_seconds": args.timeout,
            "retry_count": args.retries,
            "chunk_size": args.chunk_size,
            "part_count": len(note_chunks),
            "successful_part_count": len(successful_parts),
            "compact_input": str(paths["compact_md"].resolve()),
            "errors_path": str(paths["errors"].resolve()),
            "env_file": key_meta.get("env_file", ""),
            "env_file_exists": key_meta.get("env_file_exists", False),
            "checked_env_vars": key_meta.get("checked_env_vars", []),
            "api_key_source": key_meta.get("api_key_source", ""),
            "api_key_present": bool(api_key),
            "input_json_used": input_json_used,
            "input_selection_status": input_selection_status,
            "used_effective_input": bool(compact.get("input_layer", {}).get("used_effective_notes")),
        }
    )
    write_json(paths["summary_json"], summary)
    write_text(paths["summary_md"], build_markdown(summary, title="Gemini 汇总分析"))
    write_json(paths["out_json"], summary)
    write_text(paths["out_md"], build_markdown(summary, title="Gemini 汇总分析"))
    write_json(paths["errors"], {"meta": {"generated_at": now_iso(), "error_count": len(errors)}, "errors": errors})

    print(f"Compact AI input written: {paths['compact_md']}")
    print(f"Gemini summary JSON written: {paths['summary_json']}")
    print(f"Gemini summary Markdown written: {paths['summary_md']}")
    print(f"Gemini analysis JSON written: {paths['out_json']}")
    print(f"Gemini analysis Markdown written: {paths['out_md']}")
    print(f"AI errors JSON written: {paths['errors']}")
    print(f"Gemini env file: {key_meta.get('env_file') or 'not specified'}")
    print(f"Gemini env file exists: {key_meta.get('env_file_exists')}")
    print(f"Gemini checked env vars: {', '.join(key_meta.get('checked_env_vars') or [])}")
    print(f"Gemini API key source: {key_meta.get('api_key_source') or 'missing'}")
    print(f"AI input JSON used: {input_json_used or args.input_json}")
    print(f"AI input selection: {input_selection_status or 'default'}")
    print(f"used_effective_input={bool(compact.get('input_layer', {}).get('used_effective_notes'))}")
    print(f"ai_status={summary.get('meta', {}).get('ai_status')}")
    print(f"gemini_parts={len(note_chunks)}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze compact XHS input with Gemini using chunking and graceful fallback.")
    parser.add_argument("--input-md", required=True)
    parser.add_argument("--input-json", default="")
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", required=True)
    parser.add_argument("--env-file", default="")
    parser.add_argument("--model", default=os.environ.get("GEMINI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--retry-wait-seconds", type=int, default=5)
    parser.add_argument("--chunk-size", type=int, default=8)
    parser.add_argument("--max-input-chars", type=int, default=120000)  # Kept for CLI compatibility.
    raise_on_unhandled = os.environ.get("XHS_GEMINI_RAISE", "") == "1"
    args = parser.parse_args()
    try:
        raise SystemExit(run_analysis(args))
    except Exception as exc:  # noqa: BLE001
        if raise_on_unhandled:
            raise
        paths = derive_paths(args.out_json, args.out_md)
        errors = [{"label": "unhandled", "attempt": 1, "ai_status": "failed", "message": str(exc), "timestamp": now_iso()}]
        fallback = empty_analysis(args.model, args.input_md, status="failed", errors=errors)
        write_json(paths["out_json"], fallback)
        write_text(paths["out_md"], build_markdown(fallback, title="Gemini 分析失败"))
        write_json(paths["summary_json"], fallback)
        write_text(paths["summary_md"], build_markdown(fallback, title="Gemini 分析失败"))
        write_json(paths["errors"], {"meta": {"generated_at": now_iso(), "error_count": len(errors)}, "errors": errors})
        print(f"Gemini analysis failed gracefully: {exc}")
        print(f"AI errors JSON written: {paths['errors']}")
        raise SystemExit(0)


if __name__ == "__main__":
    main()
