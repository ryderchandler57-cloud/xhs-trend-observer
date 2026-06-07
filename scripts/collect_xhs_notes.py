from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse

from parsers.xhs_network_parser import parse_network_raw_dir


CN_TZ = timezone(timedelta(hours=8))
COLLECT_SCRIPT_PATH = str(Path(__file__).resolve())
PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
DETAIL_OPEN_STATUSES = {
    "success",
    "skipped",
    "failed_click",
    "failed_navigation",
    "timeout",
    "login_required",
    "blocked",
    "empty_detail",
    "unknown",
}


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def load_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str, data: dict[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_id_set(path: str) -> set[str]:
    if not path:
        return set()
    file_path = Path(path)
    if not file_path.exists():
        return set()
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if isinstance(data, list):
        return {str(item) for item in data if item}
    if isinstance(data, dict):
        values = data.get("note_ids") or data.get("ids") or []
        if isinstance(values, list):
            return {str(item) for item in values if item}
    return set()


def save_id_set(path: str, ids: set[str]) -> None:
    if not path:
        return
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"updated_at": now_iso(), "note_ids": sorted(ids)}, ensure_ascii=False, indent=2), encoding="utf-8")


def search_sort_tag(sort_name: str) -> str:
    mapping = {
        "general": "general",
        "latest": "time_descending",
        "popular": "popularity_descending",
        "comments": "comment_descending",
        "collects": "collect_descending",
    }
    return mapping.get(sort_name, "general")


def generate_search_id(root: str | None = None) -> str:
    import random
    import time

    if root:
        return root
    value = (int(time.time() * 1000) << 64) + random.randint(1, 0x7FFFFFFE)
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while value:
        value, rem = divmod(value, 36)
        out = chars[rem] + out
    return out or "0"


class Diagnostics:
    def __init__(self, directory: str | None, limit: int = 8) -> None:
        self.directory = Path(directory) if directory else None
        self.limit = max(0, limit)
        self.events: list[dict[str, Any]] = []
        self.screenshots: list[str] = []
        if self.directory:
            self.directory.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self.directory is not None

    def add(self, kind: str, data: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self.events.append({"kind": kind, "at": now_iso(), **data})

    async def screenshot(self, page: Any, name: str) -> str:
        if not self.enabled or len(self.screenshots) >= self.limit:
            return ""
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name).strip("-") or "capture"
        path = self.directory / f"{len(self.screenshots) + 1:02d}-{safe}.png"
        try:
            await page.screenshot(path=str(path), full_page=True)
        except Exception:
            return ""
        self.screenshots.append(str(path))
        return str(path)

    def write(self) -> str:
        if not self.enabled:
            return ""
        path = self.directory / "diagnostics.json"
        write_json(
            str(path),
            {
                "generated_at": now_iso(),
                "events": self.events,
                "screenshots": self.screenshots,
            },
        )
        return str(path)


def note_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    match = re.search(r"^/(?:explore|discovery/item)/([^/?#]+)", parsed.path)
    if match:
        return match.group(1)
    parts = [part for part in parsed.path.split("/") if part]
    if parts:
        return parts[-1].split("?")[0]
    return url


def is_bad_xhs_route(url: str) -> bool:
    if not url:
        return True
    parsed = urlparse(url)
    return parsed.path.startswith("/404") or "redirectPath=" in parsed.query or "/404" in parsed.path


def is_valid_xhs_note_url(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url.split("#")[0])
    host = parsed.netloc.lower()
    if not host.endswith("xiaohongshu.com"):
        return False
    if is_bad_xhs_route(url):
        return False
    return bool(re.match(r"^/(?:explore|discovery/item|search_result)/[^/?#]+", parsed.path))


def xhs_url_params(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    return {key: values[0] for key, values in query.items() if values}


def classify_access_status(url: str = "", body: str = "", errors: list[str] | None = None, api_ok: bool = False) -> str:
    text = " ".join([url or "", body or "", " ".join(errors or [])])
    if api_ok:
        if is_bad_xhs_route(url) or any(token in text for token in ["无法浏览", "页面不见了", "当前笔记暂时无法浏览", "请打开小红书App扫码查看"]):
            return "page_blocked_api_ok"
        return "ok"
    if "登录" in text or "扫码" in text or "cookie" in text.lower() or "401" in text or "403" in text:
        return "cookie_invalid_or_login_required"
    if "安全验证" in text or "验证码" in text or "访问异常" in text or "风控" in text:
        return "verification_or_rate_limited"
    if "/404" in text or "redirectPath=" in text or "你访问的页面不见了" in text or "页面不见了" in text:
        return "route_404"
    if "当前笔记暂时无法浏览" in text or "请打开小红书App扫码查看" in text or "无法浏览" in text:
        return "note_unavailable_or_app_only"
    if "timed out" in text.lower() or "timeout" in text.lower():
        return "timeout"
    return "unknown"


def canonical_url(url: str) -> str:
    if url:
        return url.split("#")[0]
    note_id = note_id_from_url(url)
    if note_id and len(note_id) >= 12:
        return f"https://www.xiaohongshu.com/explore/{note_id}"
    return url.split("#")[0]


def parse_count(text: str, label_patterns: list[str]) -> int | None:
    if not text:
        return None
    labels = "|".join(label_patterns)
    patterns = [
        rf"([0-9]+(?:\.[0-9]+)?\s*[万千kKwW]?)\s*(?:{labels})",
        rf"(?:{labels})\s*([0-9]+(?:\.[0-9]+)?\s*[万千kKwW]?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            raw = match.group(1).strip().lower()
            multiplier = 1
            if raw.endswith("万") or raw.endswith("w"):
                multiplier = 10000
                raw = raw[:-1]
            elif raw.endswith("千") or raw.endswith("k"):
                multiplier = 1000
                raw = raw[:-1]
            try:
                return int(float(raw.strip()) * multiplier)
            except ValueError:
                return None
    return None


def compact_text(text: str, limit: int = 5000) -> str:
    clean = re.sub(r"\s+", "\n", text or "").strip()
    if len(clean) > limit:
        return clean[:limit] + "..."
    return clean


def one_line(text: str, limit: int = 280) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if len(clean) > limit:
        return clean[: limit - 1].rstrip() + "…"
    return clean


def dedupe_texts(items: list[str], limit: int = 80) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        clean = one_line(item, 260)
        if not clean or clean in seen:
            continue
        if len(clean) < 4:
            continue
        seen.add(clean)
        out.append(clean)
        if len(out) >= limit:
            break
    return out


def dedupe_urls(items: list[str], limit: int = 20) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        clean = str(item or "").strip()
        if not clean.startswith("http") or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
        if len(out) >= limit:
            break
    return out


def fallback_record(card: dict[str, str], query: str, error: str) -> dict[str, Any]:
    url = canonical_url(card.get("url", ""))
    return {
        "id": note_id_from_url(url),
        "note_id": note_id_from_url(url),
        "url": url,
        "canonical_url": card.get("canonical_url", url.split("?")[0]),
        "query": query,
        "keyword": query,
        "title": card.get("title", ""),
        "card_text": card.get("card_text", ""),
        "content": "",
        "publish_time": "",
        "images": [card["image"]] if card.get("image") else [],
        "video": "",
        "comments": [],
        "access_status": classify_access_status(url, "", [error]),
        "like_count": normalize_count(card.get("like_count")),
        "collect_count": normalize_count(card.get("collect_count")),
        "comment_count": normalize_count(card.get("comment_count")),
        "extraction_method": "search-card fallback",
        "detail_attempted": False,
        "detail_opened": False,
        "detail_open_status": "skipped",
        "detail_skip_reason": error,
        "detail_started_at": "",
        "detail_finished_at": "",
        "search_rank": card.get("search_rank", 0),
        "query_rank": card.get("query_rank", 0),
        "card_href": card.get("card_href") or card.get("url", ""),
        "open_strategy": "",
        "api_payload_count": 0,
        "api_fetch_attempted": False,
        "api_detail_fetch_count": 0,
        "api_comment_count": 0,
        "dom_comment_count": 0,
        "comment_source": "none",
        "source": card.get("source", ""),
        "errors": [error],
    }


def skipped_detail_record(card: dict[str, str], query: str, reason: str) -> dict[str, Any]:
    record = fallback_record(card, query, reason)
    record["detail_attempted"] = False
    record["detail_open_status"] = "skipped"
    record["detail_skip_reason"] = reason
    record["detail_finished_at"] = now_iso()
    return record


def timeout_detail_record(card: dict[str, str], query: str, seconds: int) -> dict[str, Any]:
    message = f"detail timed out after {seconds}s"
    record = fallback_record(card, query, message)
    record["detail_attempted"] = True
    record["detail_open_status"] = "timeout"
    record["detail_skip_reason"] = ""
    record["open_strategy"] = "timeout"
    record["detail_started_at"] = now_iso()
    record["detail_finished_at"] = now_iso()
    return record


def normalize_count(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    if not text:
        return None
    multiplier = 1
    if text.endswith("万") or text.endswith("w"):
        multiplier = 10000
        text = text[:-1]
    elif text.endswith("千") or text.endswith("k"):
        multiplier = 1000
        text = text[:-1]
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return None


def xhs_url_from_api_item(item: dict[str, Any]) -> str:
    note_id = (
        item.get("note_id")
        or item.get("id")
        or item.get("noteId")
        or item.get("note_card", {}).get("note_id")
        or item.get("note_card", {}).get("id")
    )
    if not note_id:
        return ""
    params: list[str] = []
    xsec_token = item.get("xsec_token") or item.get("xsecToken") or item.get("note_card", {}).get("xsec_token")
    xsec_source = item.get("xsec_source") or item.get("xsecSource") or item.get("note_card", {}).get("xsec_source")
    if xsec_token:
        params.append(f"xsec_token={quote(str(xsec_token))}")
    if xsec_source:
        params.append(f"xsec_source={quote(str(xsec_source))}")
    suffix = ("?" + "&".join(params)) if params else ""
    return f"https://www.xiaohongshu.com/explore/{note_id}{suffix}"


def extract_cards_from_api_payloads(payloads: list[dict[str, Any]]) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []

    def image_from_item(item: dict[str, Any]) -> str:
        for key in ["cover", "image", "image_info", "url_default"]:
            value = item.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
            if isinstance(value, dict):
                for inner_key in ["url", "url_default", "url_pre", "trace_id"]:
                    inner = value.get(inner_key)
                    if isinstance(inner, str) and inner.startswith("http"):
                        return inner
            if isinstance(value, list):
                for part in value:
                    if isinstance(part, str) and part.startswith("http"):
                        return part
                    if isinstance(part, dict):
                        url = part.get("url") or part.get("url_default")
                        if isinstance(url, str) and url.startswith("http"):
                            return url
        return ""

    def maybe_card(item: dict[str, Any]) -> dict[str, str] | None:
        card = item.get("note_card") if isinstance(item.get("note_card"), dict) else item
        note_id = card.get("note_id") or card.get("id") or item.get("note_id") or item.get("id")
        title = card.get("display_title") or card.get("title") or item.get("display_title") or item.get("title")
        desc = card.get("desc") or card.get("description") or item.get("desc") or ""
        url = xhs_url_from_api_item(item) or xhs_url_from_api_item(card)
        if not note_id or not title or not url:
            return None
        user = card.get("user") or card.get("user_info") or item.get("user") or item.get("user_info") or {}
        interact = card.get("interact_info") or item.get("interact_info") or {}
        text_parts = [str(title)]
        if desc:
            text_parts.append(str(desc))
        nickname = user.get("nickname") if isinstance(user, dict) else ""
        if nickname:
            text_parts.append(f"作者 {nickname}")
        result = {
            "url": url,
            "canonical_url": url.split("?")[0],
            "title": one_line(str(title), 160),
            "card_text": one_line(" ".join(text_parts), 600),
            "image": image_from_item(card) or image_from_item(item),
            "source": "search-api",
        }
        for raw_key, out_key in [
            ("liked_count", "like_count"),
            ("collected_count", "collect_count"),
            ("comment_count", "comment_count"),
        ]:
            value = interact.get(raw_key) if isinstance(interact, dict) else None
            count = normalize_count(value)
            if count is not None:
                result[out_key] = str(count)
        return result

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            found = maybe_card(obj)
            if found:
                cards.append(found)
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    for payload in payloads:
        walk(payload)

    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for card in cards:
        key = note_id_from_url(card.get("url", "")) or card.get("canonical_url", "") or card.get("title", "")
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(card)
    return deduped[:80]


async def fetch_search_api_cards(page: Any, query: str, per_query: int, sort: str) -> tuple[list[dict[str, str]], list[str], int]:
    body = {
        "keyword": query,
        "page": 1,
        "page_size": max(20, per_query),
        "search_id": generate_search_id(),
        "sort": "general",
        "note_type": 0,
        "ext_flags": [],
        "filters": [
            {"tags": [search_sort_tag(sort)], "type": "sort_type"},
            {"tags": ["不限"], "type": "filter_note_type"},
            {"tags": ["不限"], "type": "filter_note_time"},
            {"tags": ["不限"], "type": "filter_note_range"},
            {"tags": ["不限"], "type": "filter_pos_distance"},
        ],
        "geo": "",
        "image_formats": ["jpg", "webp", "avif"],
    }
    result = await fetch_json_in_page(page, "/api/sns/web/v1/search/notes", "POST", body)
    data = result.get("data")
    if isinstance(data, dict):
        payload = data.get("data") if isinstance(data.get("data"), dict) else data
        cards = extract_cards_from_api_payloads([payload])
        return cards[:per_query], [], 1
    error = ""
    if result.get("status"):
        error = f"search api returned {result.get('status')}: {one_line(result.get('text', ''), 160)}"
    return [], [error] if error else ["search api returned no JSON data"], 0


def extract_from_api_payloads(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    titles: list[str] = []
    descs: list[str] = []
    comments: list[str] = []
    authors: list[str] = []
    images: list[str] = []
    videos: list[str] = []

    def walk(obj: Any, parent_key: str = "") -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                key_l = str(key).lower()
                if isinstance(value, str) and value.startswith("http"):
                    if any(token in key_l for token in ["image", "img", "cover", "url_default", "url_pre"]):
                        images.append(value)
                    if any(token in key_l for token in ["video", "stream", "master", "h264", "h265"]):
                        videos.append(value)
            content = obj.get("content")
            if isinstance(content, str) and (
                "comment" in parent_key.lower()
                or "sub_comment" in parent_key.lower()
                or "user_info" in obj
                or "like_count" in obj
            ):
                comments.append(content)
            for key in ["desc", "description", "note_desc"]:
                value = obj.get(key)
                if isinstance(value, str) and len(value.strip()) > 12:
                    descs.append(value)
            value = obj.get("title")
            if isinstance(value, str) and len(value.strip()) > 3:
                titles.append(value)
            user = obj.get("user_info") or obj.get("user")
            if isinstance(user, dict):
                nickname = user.get("nickname") or user.get("name")
                if isinstance(nickname, str):
                    authors.append(nickname)
            for key, value in obj.items():
                walk(value, key)
        elif isinstance(obj, list):
            for item in obj:
                walk(item, parent_key)

    for payload in payloads:
        walk(payload)

    return {
        "title": dedupe_texts(titles, 3)[0] if dedupe_texts(titles, 3) else "",
        "author": dedupe_texts(authors, 3)[0] if dedupe_texts(authors, 3) else "",
        "text": "\n".join(dedupe_texts(descs, 8)),
        "comments": dedupe_texts(comments, 80),
        "images": dedupe_urls(images, 20),
        "video": dedupe_urls(videos, 1)[0] if dedupe_urls(videos, 1) else "",
    }


UNAVAILABLE_HINTS = [
    "当前笔记暂时无法浏览",
    "你访问的页面不见了",
    "页面不见了",
    "请打开小红书App扫码查看",
    "扫码查看",
    "安全验证",
    "访问异常",
    "无法浏览",
    "404",
]


async def safe_goto(
    page: Any,
    url: str,
    wait_until: str = "domcontentloaded",
    retries: int = 2,
    timeout_ms: int = 25000,
) -> None:
    last_error: Exception | None = None
    for _ in range(max(1, retries)):
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            return
        except Exception as exc:  # Playwright changes exception classes across versions.
            last_error = exc
            await page.wait_for_timeout(600)
    if last_error:
        raise last_error


async def extract_cards(page: Any) -> list[dict[str, str]]:
    return await page.evaluate(
        """
        () => {
          const parseNoteHref = (raw) => {
            let url;
            try {
              url = new URL(raw, location.href);
            } catch (_) {
              return null;
            }
            if (!url.hostname.endsWith('xiaohongshu.com')) return null;
            if (url.pathname.startsWith('/404') || url.search.includes('redirectPath=')) return null;
            const match = url.pathname.match(/^\\/(?:explore|discovery\\/item|search_result)\\/([^/?#]+)/);
            if (!match) return null;
            return { href: url.href.split('#')[0], id: match[1], path: url.pathname };
          };
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return !!el.getClientRects().length &&
              rect.width > 0 &&
              rect.height > 0 &&
              style.visibility !== 'hidden' &&
              style.display !== 'none' &&
              Number(style.opacity || '1') !== 0 &&
              style.pointerEvents !== 'none';
          };
          const bestById = new Map();
          const orderedIds = [];
          for (const a of Array.from(document.querySelectorAll('a[href]'))) {
            const parsed = parseNoteHref(a.href || a.getAttribute('href') || '');
            if (!parsed) continue;
            const parent = a.closest('section, article, div') || a;
            const text = (parent.innerText || a.innerText || '').replace(/\\s+/g, ' ').trim();
            const title = (a.innerText || text || '').replace(/\\s+/g, ' ').trim();
            const img = parent.querySelector('img');
            const image = img ? (img.currentSrc || img.src || '') : '';
            const isVisible = visible(a);
            const isSearchResult = parsed.path.startsWith('/search_result/');
            const score = (isVisible ? 1000 : 0) + (isSearchResult ? 100 : 0) + Math.min(text.length, 80);
            const item = {
              url: parsed.href,
              canonical_url: parsed.href.split('?')[0],
              title,
              card_text: text,
              image,
              source: 'search-dom',
              anchor_visible: isVisible,
              href_path: parsed.path,
              score
            };
            if (!bestById.has(parsed.id)) {
              orderedIds.push(parsed.id);
              bestById.set(parsed.id, item);
            } else if (score > (bestById.get(parsed.id).score || 0)) {
              bestById.set(parsed.id, item);
            }
          }
          return orderedIds.map(id => {
            const item = bestById.get(id);
            delete item.score;
            return item;
          }).slice(0, 80);
        }
        """
    )


def dedupe_cards(cards: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for card in cards:
        if not is_valid_xhs_note_url(card.get("url", "")):
            continue
        key = note_id_from_url(card.get("url", "")) or card.get("canonical_url", "") or card.get("title", "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(card)
    return out


def is_dom_search_card(card: dict[str, str]) -> bool:
    return card.get("source") == "search-dom"


def is_relevant_xhs_json_url(url: str) -> bool:
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    if not host.endswith("xiaohongshu.com"):
        return False
    path = parsed.path.lower().strip("/")
    return any(
        token in path
        for token in [
            "api/sns/web/v1/feed",
            "api/sns/web/v2/comment",
            "api/sns/web/v1/comment",
            "api/sns/web/v1/note/",
            "api/sns/web/v2/note/",
            "detail",
        ]
    )


def safe_network_name(url: str) -> str:
    parsed = urlparse(url or "")
    name = parsed.path.strip("/").replace("/", "-") or "response"
    name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name).strip("-")
    return name[:80] or "response"


class NetworkCapture:
    def __init__(self, directory: str | Path, note_id: str, attempt: int) -> None:
        self.directory = Path(directory)
        self.note_id = note_id
        self.attempt = attempt
        self.tasks: list[asyncio.Task[Any]] = []
        self.matched_api_responses: list[dict[str, Any]] = []
        self.parser_errors: list[str] = []
        self.enabled = bool(directory)
        if self.enabled:
            self.directory.mkdir(parents=True, exist_ok=True)

    def schedule(self, response: Any, bucket: list[dict[str, Any]]) -> None:
        if not self.enabled:
            return
        task = asyncio.create_task(capture_xhs_response(response, bucket, self))
        self.tasks.append(task)

    async def drain(self) -> None:
        if not self.tasks:
            return
        tasks = list(self.tasks)
        self.tasks.clear()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                self.parser_errors.append(one_line(str(result), 240))

    def save_payload(self, item: dict[str, Any]) -> str:
        if not self.enabled:
            return ""
        index = len(self.matched_api_responses) + 1
        file_name = f"{self.attempt:02d}-{index:03d}-{safe_network_name(item.get('url', ''))}.json"
        path = self.directory / file_name
        wrapper = {
            "captured_at": now_iso(),
            "attempt": self.attempt,
            "note_id": self.note_id,
            "url": item.get("url", ""),
            "method": item.get("method", ""),
            "status": item.get("status"),
            "resource_type": item.get("resource_type", ""),
            "file": str(path),
            "data": item.get("data"),
        }
        write_json(str(path), wrapper)
        self.matched_api_responses.append(
            {
                "url": wrapper["url"],
                "method": wrapper["method"],
                "status": wrapper["status"],
                "resource_type": wrapper["resource_type"],
                "file": str(path),
            }
        )
        return str(path)


async def capture_xhs_response(
    response: Any,
    bucket: list[dict[str, Any]],
    network_capture: NetworkCapture | None = None,
) -> None:
    url = response.url
    if not is_relevant_xhs_json_url(url):
        return
    try:
        data = await response.json()
    except Exception:
        return
    if isinstance(data, (dict, list)):
        item = {
            "url": response.url,
            "method": getattr(response.request, "method", ""),
            "status": getattr(response, "status", None),
            "resource_type": getattr(response.request, "resource_type", ""),
            "data": data,
        }
        if network_capture:
            item["file"] = network_capture.save_payload(item)
        bucket.append(item)


async def fetch_json_in_page(page: Any, url: str, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        return await page.evaluate(
            """
            async ({ url, method, body }) => {
              try {
                const options = {
                  method,
                  credentials: 'include',
                  headers: {
                    'accept': 'application/json, text/plain, */*',
                    'content-type': 'application/json;charset=UTF-8',
                  },
                };
                if (body) options.body = JSON.stringify(body);
                const response = await fetch(url, options);
                const text = await response.text();
                let data = null;
                try { data = JSON.parse(text); } catch (_) {}
                return {
                  ok: response.ok,
                  status: response.status,
                  url: response.url,
                  data,
                  text: text.slice(0, 800),
                };
              } catch (error) {
                return { ok: false, status: 0, url, data: null, text: String(error) };
              }
            }
            """,
            {"url": url, "method": method, "body": body},
        )
    except Exception as exc:
        return {"ok": False, "status": 0, "url": url, "data": None, "text": str(exc)}


def comment_cursor_from_payload(data: dict[str, Any]) -> tuple[str, bool]:
    if not isinstance(data, dict):
        return "", False
    candidates: list[dict[str, Any]] = []
    if isinstance(data.get("data"), dict):
        candidates.append(data["data"])
    candidates.append(data)
    for item in candidates:
        cursor = item.get("cursor") or item.get("next_cursor") or item.get("nextCursor") or ""
        has_more = item.get("has_more")
        if has_more is None:
            has_more = item.get("hasMore")
        if cursor or has_more is not None:
            return str(cursor or ""), bool(has_more)
    return "", False


async def fetch_note_api_payloads(page: Any, note_url: str, max_comments: int) -> tuple[list[dict[str, Any]], list[str]]:
    note_id = note_id_from_url(note_url)
    if not note_id or not is_valid_xhs_note_url(note_url):
        return [], [f"api fallback skipped invalid note url: {note_url}"]

    params = xhs_url_params(note_url)
    xsec_source = params.get("xsec_source") or "pc_search"
    xsec_token = params.get("xsec_token") or ""
    payloads: list[dict[str, Any]] = []
    errors: list[str] = []

    feed_body = {
        "source_note_id": note_id,
        "image_scenes": ["CRD_WM_WEBP"],
        "extra": {"need_body_topic": "1"},
        "xsec_source": xsec_source,
    }
    if xsec_token:
        feed_body["xsec_token"] = xsec_token

    feed = await fetch_json_in_page(page, "/api/sns/web/v1/feed", "POST", feed_body)
    if isinstance(feed.get("data"), dict):
        payloads.append({"url": feed.get("url") or "/api/sns/web/v1/feed", "data": feed["data"]})
    elif feed.get("status"):
        errors.append(f"feed api returned {feed.get('status')}: {one_line(feed.get('text', ''), 160)}")

    cursor = ""
    pages = max(1, min(4, math.ceil(max_comments / 20)))
    for _ in range(pages):
        query = {
            "note_id": note_id,
            "cursor": cursor,
            "top_comment_id": "",
            "image_formats": "jpg,webp,avif",
        }
        comments_url = "/api/sns/web/v2/comment/page?" + urlencode(query)
        comments = await fetch_json_in_page(page, comments_url)
        data = comments.get("data")
        if isinstance(data, dict):
            payloads.append({"url": comments.get("url") or comments_url, "data": data})
            cursor, has_more = comment_cursor_from_payload(data)
            extracted = extract_from_api_payloads([data]).get("comments", [])
            if not has_more or not cursor or len(extracted) >= max_comments:
                break
        else:
            if comments.get("status"):
                errors.append(f"comment api returned {comments.get('status')}: {one_line(comments.get('text', ''), 160)}")
            break

    return payloads, errors


async def mark_valid_card_anchor(page: Any, note_id: str, target_url: str = "") -> dict[str, Any]:
    if not note_id and not target_url:
        return {
            "found": False,
            "href": "",
            "match": "missing-input",
            "candidate_count": 0,
            "raw_dom_href": "",
            "anchor_text": "",
            "anchor_box": {"x": 0, "y": 0, "width": 0, "height": 0},
            "anchor_visible": False,
            "anchor_enabled": False,
            "clickable": False,
        }
    return await page.evaluate(
        """
        ({ noteId, targetUrl }) => {
          for (const old of Array.from(document.querySelectorAll('[data-codex-xhs-target="1"]'))) {
            old.removeAttribute('data-codex-xhs-target');
          }
          const normalizedTarget = (() => {
            if (!targetUrl) return '';
            try { return new URL(targetUrl, location.href).href.split('#')[0]; } catch (_) { return ''; }
          })();
          const isValid = (raw) => {
            let url;
            try {
              url = new URL(raw, location.href);
            } catch (_) {
              return null;
            }
            if (!url.hostname.endsWith('xiaohongshu.com')) return null;
            if (url.pathname.startsWith('/404') || url.search.includes('redirectPath=')) return null;
            const notePathOk = noteId && (
              url.pathname === `/explore/${noteId}` ||
              url.pathname === `/discovery/item/${noteId}` ||
              url.pathname === `/search_result/${noteId}`
            );
            if (!notePathOk) return null;
            return url.href.split('#')[0];
          };
          const emptyBox = () => ({ x: 0, y: 0, width: 0, height: 0 });
          const describe = (a, normalized, match) => {
            const rect = a.getBoundingClientRect();
            const style = window.getComputedStyle(a);
            const box = {
              x: Math.round(rect.x * 100) / 100,
              y: Math.round(rect.y * 100) / 100,
              width: Math.round(rect.width * 100) / 100,
              height: Math.round(rect.height * 100) / 100,
            };
            const anchorVisible = !!a.getClientRects().length &&
              rect.width > 0 &&
              rect.height > 0 &&
              style.visibility !== 'hidden' &&
              style.display !== 'none' &&
              Number(style.opacity || '1') !== 0;
            const anchorEnabled = !a.hasAttribute('disabled') &&
              a.getAttribute('aria-disabled') !== 'true' &&
              style.pointerEvents !== 'none';
            const parentText = a.parentElement ? (a.parentElement.innerText || a.parentElement.textContent || '') : '';
            const anchorText = ((a.innerText || a.textContent || parentText || '')
              .replace(/\\s+/g, ' ')
              .trim())
              .slice(0, 500);
            return {
              found: true,
              href: normalized,
              match,
              raw_dom_href: a.getAttribute('href') || '',
              anchor_text: anchorText,
              anchor_box: box,
              anchor_visible: anchorVisible,
              anchor_enabled: anchorEnabled,
              clickable: anchorVisible && anchorEnabled,
            };
          };
          const anchors = Array.from(document.querySelectorAll('a[href]'));
          const candidates = [];
          for (const a of anchors) {
            const raw = a.href || a.getAttribute('href') || '';
            const normalized = isValid(raw);
            if (!normalized) continue;
            candidates.push({ a, normalized });
          }
          const candidateCount = candidates.length;
          const withCount = (info) => ({ candidate_count: candidateCount, ...info });
          const choose = (items, match) => {
            if (!items.length) return null;
            const described = items.map(item => describe(item.a, item.normalized, match));
            return described.find(item => item.clickable) || described[0];
          };
          if (normalizedTarget) {
            const exact = candidates.filter(item => item.normalized === normalizedTarget);
            const chosen = choose(exact, 'exact-href');
            if (chosen && chosen.clickable) {
              exact.find(item => item.normalized === chosen.href)?.a.setAttribute('data-codex-xhs-target', '1');
              return withCount(chosen);
            }
          }
          const chosen = choose(candidates, 'note-id');
          if (chosen) {
            if (chosen.clickable) candidates.find(item => item.normalized === chosen.href)?.a.setAttribute('data-codex-xhs-target', '1');
            return withCount(chosen);
          }
          return {
            found: false,
            href: '',
            match: 'not-found',
            candidate_count: candidateCount,
            raw_dom_href: '',
            anchor_text: '',
            anchor_box: emptyBox(),
            anchor_visible: false,
            anchor_enabled: false,
            clickable: false,
          };
        }
        """,
        {"noteId": note_id, "targetUrl": target_url},
    )


async def inspect_detail_state(page: Any, before_click_url: str, popup_opened: bool = False) -> dict[str, Any]:
    data = await page.evaluate(
        """
        () => {
          const visible = (el) => {
            if (!el) return false;
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return !!el.getClientRects().length &&
              rect.width > 0 &&
              rect.height > 0 &&
              style.visibility !== 'hidden' &&
              style.display !== 'none' &&
              Number(style.opacity || '1') !== 0;
          };
          const selectors = [
            '[role="dialog"]',
            '[aria-modal="true"]',
            '#detail-desc',
            '[class*="note-detail"]',
            '[id*="note-detail"]',
            '[class*="detail"] [class*="comment"]',
            '[class*="comments-container"]',
            '[class*="comment-list"]',
            '[class*="comment-item"]',
            '[class*="overlay"]',
            '[class*="modal"]',
            '[class*="mask"]'
          ];
          const matched = [];
          for (const sel of selectors) {
            for (const el of Array.from(document.querySelectorAll(sel)).slice(0, 8)) {
              if (!visible(el)) continue;
              const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
              if (!text && !/dialog|modal|overlay|mask|detail/i.test(sel)) continue;
              matched.push(sel);
              break;
            }
          }
          const bodyText = (document.body && document.body.innerText || '').replace(/\\s+/g, ' ').trim();
          const tokenHits = [];
          for (const token of ['点赞', '收藏', '评论', '发布于', '笔记', '展开更多']) {
            if (bodyText.includes(token)) tokenHits.push(`text:${token}`);
          }
          return {
            matched_detail_selectors: Array.from(new Set([...matched, ...tokenHits])),
            structural_match_count: matched.length,
            detail_token_count: tokenHits.length,
            body_text_first_3000: bodyText.slice(0, 3000),
          };
        }
        """
    )
    after_url = getattr(page, "url", "") or ""
    state = "unchanged"
    if popup_opened:
        state = "popup"
    else:
        before_path = urlparse(before_click_url or "").path
        after_path = urlparse(after_url or "").path
        url_is_detail = re.search(r"/(?:explore|discovery/item|search_result)/[^/?#]+", after_path or "") is not None
        if after_url != before_click_url and url_is_detail:
            state = "url_changed"
        elif int(data.get("structural_match_count") or 0) > 0 and int(data.get("detail_token_count") or 0) >= 1:
            state = "modal"
        elif before_path != after_path and url_is_detail:
            state = "url_changed"
    data["detail_state"] = state
    return data


async def extract_detail_dom(page: Any) -> dict[str, Any]:
    return await page.evaluate(
        """
        () => {
          const textOf = (sel) => {
            const el = document.querySelector(sel);
            return el ? (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim() : '';
          };
          const pickTexts = (selectors, minLen, maxLen) => {
            const out = [];
            for (const sel of selectors) {
              for (const el of Array.from(document.querySelectorAll(sel))) {
                const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                if (!text || text.length < minLen || text.length > maxLen) continue;
                if (!out.includes(text)) out.push(text);
              }
            }
            return out;
          };
          const visibleTextCandidates = [];
          for (const el of Array.from(document.querySelectorAll('article, section, main, div, p, span'))) {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            if (!el.getClientRects().length || rect.width <= 0 || rect.height <= 0) continue;
            if (style.visibility === 'hidden' || style.display === 'none') continue;
            const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
            if (text.length < 20 || text.length > 2000) continue;
            if (/首页|点点|RED|直播|发布|消息|关于我们/.test(text) && text.length < 80) continue;
            if (!visibleTextCandidates.includes(text)) visibleTextCandidates.push(text);
            if (visibleTextCandidates.length >= 30) break;
          }
          const meta = {};
          for (const m of Array.from(document.querySelectorAll('meta'))) {
            const key = m.getAttribute('property') || m.getAttribute('name');
            const value = m.getAttribute('content');
            if (key && value) meta[key] = value;
          }
          const title =
            textOf('#detail-title') ||
            textOf('[class*=title]') ||
            meta['og:title'] ||
            document.title ||
            '';
          const author =
            textOf('[class*=author]') ||
            textOf('[class*=user] [class*=name]') ||
            textOf('[class*=nickname]') ||
            '';
          const textChunks = pickTexts([
            '#detail-desc',
            '[class*=note-content]',
            '[class*=note-text]',
            '[class*=desc]',
            '[class*=content]'
          ], 20, 5000);
          if (!textChunks.length && visibleTextCandidates.length) {
            textChunks.push(...visibleTextCandidates.slice(0, 3));
          }
          const comments = pickTexts([
            '[class*=comment-item] [class*=content]',
            '[class*=comment] [class*=content]',
            '[class*=comment] p',
            '[class*=comment] span',
            '[class*=comments] span',
            '[class*=reply] span'
          ], 5, 260);
          if (!comments.length) {
            for (const text of visibleTextCandidates) {
              if (text.length >= 5 && text.length <= 260 && !comments.includes(text)) comments.push(text);
              if (comments.length >= 20) break;
            }
          }
          const images = [];
          for (const img of Array.from(document.querySelectorAll('img'))) {
            const src = img.currentSrc || img.src || '';
            if (src && !src.startsWith('data:') && !images.includes(src)) images.push(src);
          }
          let video = '';
          const videoEl = document.querySelector('video');
          if (videoEl) {
            video = videoEl.currentSrc || videoEl.src || '';
            if (!video) {
              const source = videoEl.querySelector('source[src]');
              video = source ? source.src : '';
            }
          }
          return {
            title,
            author,
            text: textChunks.join('\\n'),
            body_text: (document.body.innerText || '').slice(0, 16000),
            comments,
            visible_text_candidates: visibleTextCandidates.slice(0, 20),
            images: images.slice(0, 12),
            video
          };
        }
        """
    )


async def expand_and_scroll_comments(page: Any, rounds: int = 5) -> None:
    for _ in range(max(1, rounds)):
        try:
            await page.evaluate(
                """
                () => {
                  const textHit = /(展开|更多|查看|全部|回复|评论|show more|more replies)/i;
                  const clickable = Array.from(document.querySelectorAll('button, [role=button], a, span, div'))
                    .filter(el => {
                      const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                      if (!text || text.length > 28) return false;
                      if (!textHit.test(text)) return false;
                      const rect = el.getBoundingClientRect();
                      return rect.width > 0 && rect.height > 0;
                    })
                    .slice(0, 8);
                  for (const el of clickable) {
                    try { el.click(); } catch (_) {}
                  }
                  const scrollables = Array.from(document.querySelectorAll('div, section, main'))
                    .filter(el => {
                      const style = window.getComputedStyle(el);
                      const overflow = `${style.overflow}${style.overflowY}`.toLowerCase();
                      const text = (el.innerText || '').slice(0, 1000);
                      return el.scrollHeight > el.clientHeight + 120 &&
                        (overflow.includes('auto') || overflow.includes('scroll') || /评论|回复|comment/i.test(text));
                    })
                    .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight));
                  if (scrollables[0]) {
                    scrollables[0].scrollTop += Math.max(600, scrollables[0].clientHeight * 0.8);
                  }
                  window.scrollBy(0, 900);
                }
                """
            )
        except Exception:
            pass
        await page.wait_for_timeout(650)


async def search_query(
    page: Any,
    query: str,
    per_query: int,
    scroll_rounds: int,
    sort: str,
    diagnostics: Diagnostics | None = None,
) -> tuple[list[dict[str, str]], list[str]]:
    warnings: list[str] = []
    api_payloads: list[dict[str, Any]] = []
    listener = lambda response: asyncio.create_task(capture_xhs_response(response, api_payloads))
    search_url = f"https://www.xiaohongshu.com/search_result?keyword={quote(query)}&source=web_search_result_notes"
    page.on("response", listener)
    try:
        await safe_goto(page, search_url, retries=2, timeout_ms=30000)
        await page.wait_for_timeout(1800)
        for _ in range(max(1, scroll_rounds)):
            await page.mouse.wheel(0, 1400)
            await page.wait_for_timeout(550)
        dom_cards = await extract_cards(page)
        api_cards = extract_cards_from_api_payloads([item["data"] for item in api_payloads])
        direct_api_cards, direct_api_warnings, direct_api_payload_count = await fetch_search_api_cards(page, query, per_query, sort)
        warnings.extend(direct_api_warnings)
        # Prefer real DOM hrefs from the visible search page. API candidates are useful
        # for discovery, but their /explore/{id} URLs are reconstructed and can 404
        # without the browser's full routing context.
        cards = dedupe_cards(dom_cards + api_cards + direct_api_cards)
        if diagnostics and diagnostics.enabled:
            screenshot = await diagnostics.screenshot(page, f"search-{len(diagnostics.events) + 1}")
            diagnostics.add(
                "search_query",
                {
                    "query": query,
                    "sort": sort,
                    "search_url": search_url,
                    "dom_card_count": len(dom_cards),
                    "api_card_count": len(api_cards),
                    "direct_api_card_count": len(direct_api_cards),
                    "selected_card_count": len(cards[:per_query]),
                    "api_payload_count": len(api_payloads) + direct_api_payload_count,
                    "screenshot": screenshot,
                    "cards": [
                        {
                            "id": note_id_from_url(card.get("url", "")),
                            "title": one_line(card.get("title", ""), 120),
                            "url": card.get("url", ""),
                            "source": card.get("source", "search-dom"),
                        }
                        for card in cards[:per_query]
                    ],
                },
            )
        if not cards:
            body = await page.locator("body").inner_text(timeout=5000)
            if any(token in body for token in ["登录", "扫码", "验证", "安全验证"]):
                warnings.append("search page may require login or verification")
            warnings.append(f"no cards found for query: {query}")
        for card in cards:
            card["search_url"] = search_url
        if api_cards:
            warnings.append(f"query '{query}' used {len(api_cards)} API card candidates")
        return cards[:per_query], warnings
    finally:
        try:
            page.remove_listener("response", listener)
        except Exception:
            pass


async def extract_detail(
    page: Any,
    card: dict[str, str],
    query: str,
    max_comments: int,
    search_url: str,
    diagnostics: Diagnostics | None = None,
    attempt_number: int = 0,
    card_index: int = 0,
    network_raw_dir: str | Path | None = None,
) -> dict[str, Any]:
    url = canonical_url(card.get("url", ""))
    record = fallback_record(card, query, "")
    record["errors"] = []
    record["detail_attempted"] = True
    record["detail_started_at"] = now_iso()
    record["detail_finished_at"] = ""
    record["detail_open_status"] = "unknown"
    record["detail_skip_reason"] = ""
    record["open_strategy"] = "click-card"
    record["source"] = card.get("source", "")
    record["search_rank"] = card.get("search_rank", 0)
    record["query_rank"] = card.get("query_rank", 0)
    record["card_href"] = card.get("card_href") or card.get("url", "")
    record["requested_detail_url"] = url
    record["clicked_dom_href"] = ""
    record["anchor_match"] = ""
    record["ProjectRoot"] = PROJECT_ROOT
    record["collect_script_path"] = COLLECT_SCRIPT_PATH
    record["search_url_before_click"] = search_url
    record["candidate_count"] = 0
    record["card_index"] = card_index
    record["raw_dom_href"] = ""
    record["anchor_text"] = ""
    record["anchor_box"] = {"x": 0, "y": 0, "width": 0, "height": 0}
    record["anchor_visible"] = False
    record["anchor_enabled"] = False
    record["before_click_url"] = ""
    record["after_click_url"] = ""
    record["page_count_before"] = 0
    record["page_count_after"] = 0
    record["active_page_url"] = ""
    record["detail_state"] = ""
    record["body_text_first_3000"] = ""
    record["screenshot_path_after_click"] = ""
    record["matched_detail_selectors"] = []
    record["click_attempts"] = []
    record["click_result"] = ""
    record["network_capture_enabled"] = bool(network_raw_dir)
    record["matched_api_responses"] = []
    record["parsed_from_network"] = False
    record["parsed_from_dom"] = False
    record["parser_errors"] = []
    record["network_raw_dir"] = str(network_raw_dir or "")
    record["network_source_files"] = []
    api_payloads: list[dict[str, Any]] = []
    if not is_valid_xhs_note_url(url):
        record["errors"].append(f"invalid XHS note URL skipped: {url}")
        record["access_status"] = classify_access_status(url, "", record["errors"])
        record["detail_open_status"] = "failed_navigation"
        record["detail_finished_at"] = now_iso()
        return record

    note_id = note_id_from_url(url)
    network_capture = NetworkCapture(network_raw_dir or "", note_id, attempt_number)
    response_listener = lambda response: network_capture.schedule(response, api_payloads)
    page.context.on("response", response_listener)
    opened_by_click = False
    popup = None
    detail_page = page

    async def snapshot_after_click(active_page: Any, state: str = "") -> None:
        try:
            state_data = await inspect_detail_state(
                active_page,
                record.get("before_click_url", "") or getattr(page, "url", ""),
                popup_opened=state == "popup",
            )
        except Exception as exc:
            state_data = {
                "detail_state": state or "click_failed",
                "matched_detail_selectors": [],
                "body_text_first_3000": "",
            }
            record["errors"].append(f"detail state inspection failed: {exc}")
        if state:
            state_data["detail_state"] = state
        record["detail_state"] = state_data.get("detail_state", "")
        record["matched_detail_selectors"] = state_data.get("matched_detail_selectors", [])
        record["body_text_first_3000"] = state_data.get("body_text_first_3000", "")
        record["after_click_url"] = getattr(page, "url", "") or ""
        record["active_page_url"] = getattr(active_page, "url", "") or record["after_click_url"]
        try:
            record["page_count_after"] = len(page.context.pages)
        except Exception:
            record["page_count_after"] = 0
        if diagnostics and diagnostics.enabled and not record.get("screenshot_path_after_click"):
            record["screenshot_path_after_click"] = await diagnostics.screenshot(
                active_page,
                f"after-click-{attempt_number or len(diagnostics.events) + 1}",
            )

    try:
        if search_url:
            try:
                current_search_url = (getattr(page, "url", "") or "").split("#")[0]
                target_search_url = search_url.split("#")[0]
                if current_search_url != target_search_url:
                    await safe_goto(page, search_url, retries=1, timeout_ms=15000)
                    await page.wait_for_timeout(900)
                for _ in range(min(8, max(0, int(card_index) // 4))):
                    await page.mouse.wheel(0, 1200)
                    await page.wait_for_timeout(300)
            except Exception as exc:
                record["errors"].append(f"search page restore before detail click failed: {exc}")
        has_valid_anchor = False
        anchor_info: dict[str, Any] = {"found": False, "href": "", "match": ""}
        try:
            anchor_result = await mark_valid_card_anchor(page, note_id, url if is_dom_search_card(card) else "")
            if isinstance(anchor_result, dict):
                anchor_info = anchor_result
                has_valid_anchor = bool(anchor_info.get("found"))
            else:
                has_valid_anchor = bool(anchor_result)
            record["candidate_count"] = int(anchor_info.get("candidate_count") or 0)
            record["raw_dom_href"] = str(anchor_info.get("raw_dom_href") or "")
            record["anchor_text"] = str(anchor_info.get("anchor_text") or "")
            record["anchor_box"] = anchor_info.get("anchor_box") or record["anchor_box"]
            record["anchor_visible"] = bool(anchor_info.get("anchor_visible"))
            record["anchor_enabled"] = bool(anchor_info.get("anchor_enabled"))
        except Exception as exc:
            record["errors"].append(f"card anchor validation failed: {exc}")
        if has_valid_anchor:
            matched_href = str(anchor_info.get("href") or "")
            if matched_href:
                record["clicked_dom_href"] = matched_href
                record["anchor_match"] = str(anchor_info.get("match") or "")
                if is_valid_xhs_note_url(matched_href) and "/search_result/" not in urlparse(matched_href).path:
                    url = matched_href
                    record["url"] = url
                    record["canonical_url"] = url.split("?")[0]
            if not anchor_info.get("clickable"):
                record["open_strategy"] = "skipped-anchor-not-clickable"
                record["click_result"] = "skipped-anchor-not-clickable"
                record["detail_state"] = "click_failed"
                record["errors"].append("anchor exists but is not visible/enabled or has an empty box; skipped click")
                await snapshot_after_click(page, "click_failed")
                record["access_status"] = classify_access_status(url, record.get("body_text_first_3000", ""), record["errors"])
                return record
            locator = page.locator('[data-codex-xhs-target="1"]').first
            record["open_strategy"] = "click-card-exact-href" if record.get("anchor_match") == "exact-href" else "click-card-note-id"
            try:
                record["before_click_url"] = getattr(page, "url", "") or ""
                try:
                    record["page_count_before"] = len(page.context.pages)
                except Exception:
                    record["page_count_before"] = 0
                popup_task = asyncio.create_task(page.context.wait_for_event("page", timeout=2500))
                try:
                    await locator.scroll_into_view_if_needed(timeout=5000)
                    record["click_attempts"].append({"method": "scroll_into_view_if_needed", "ok": True})
                except Exception as exc:
                    record["click_attempts"].append(
                        {"method": "scroll_into_view_if_needed", "ok": False, "error": one_line(str(exc), 240)}
                    )
                    raise
                await page.wait_for_timeout(500)
                try:
                    refreshed_box = await locator.bounding_box()
                except Exception:
                    refreshed_box = None
                if refreshed_box:
                    record["anchor_box"] = {
                        "x": round(float(refreshed_box.get("x", 0)), 2),
                        "y": round(float(refreshed_box.get("y", 0)), 2),
                        "width": round(float(refreshed_box.get("width", 0)), 2),
                        "height": round(float(refreshed_box.get("height", 0)), 2),
                    }
                    record["anchor_visible"] = record["anchor_box"]["width"] > 0 and record["anchor_box"]["height"] > 0
                click_ok = False
                try:
                    await locator.click(timeout=7000, force=False)
                    click_ok = True
                    record["click_result"] = "locator.click"
                    record["click_attempts"].append({"method": "locator.click", "ok": True, "force": False})
                except Exception as click_exc:
                    record["click_attempts"].append(
                        {"method": "locator.click", "ok": False, "force": False, "error": one_line(str(click_exc), 240)}
                    )
                    box = record.get("anchor_box") or {}
                    width = float(box.get("width") or 0)
                    height = float(box.get("height") or 0)
                    if width > 0 and height > 0:
                        x = float(box.get("x") or 0) + width / 2
                        y = float(box.get("y") or 0) + height / 2
                        try:
                            await page.mouse.click(x, y)
                            click_ok = True
                            record["click_result"] = "mouse.click"
                            record["click_attempts"].append(
                                {"method": "mouse.click", "ok": True, "x": round(x, 2), "y": round(y, 2)}
                            )
                        except Exception as mouse_exc:
                            record["click_attempts"].append(
                                {
                                    "method": "mouse.click",
                                    "ok": False,
                                    "x": round(x, 2),
                                    "y": round(y, 2),
                                    "error": one_line(str(mouse_exc), 240),
                                }
                            )
                    else:
                        record["click_attempts"].append(
                            {"method": "mouse.click", "ok": False, "error": "anchor_box is empty"}
                        )
                if click_ok:
                    try:
                        popup = await popup_task
                    except Exception:
                        popup = None
                    detail_page = popup or page
                    if popup:
                        try:
                            await popup.wait_for_load_state("domcontentloaded", timeout=4000)
                        except Exception:
                            pass
                    await detail_page.wait_for_timeout(900)
                    await snapshot_after_click(detail_page, "popup" if popup else "")
                    opened_by_click = record.get("detail_state") in {"popup", "modal", "url_changed"}
                    if not opened_by_click and record.get("detail_state") == "unchanged":
                        record["open_strategy"] = "click-did-not-open-detail"
                        record["click_result"] = record.get("click_result") or "click-did-not-open-detail"
                        record["errors"].append("真实点击后页面未进入详情态，问题在点击目标/事件触发，不是 URL。")
                else:
                    try:
                        popup_task.cancel()
                    except Exception:
                        pass
                    record["open_strategy"] = "click-failed"
                    record["click_result"] = "click_failed"
                    await snapshot_after_click(page, "click_failed")
            except Exception as exc:
                record["open_strategy"] = "click-failed"
                record["click_result"] = "click_failed"
                record["detail_state"] = "click_failed"
                record["errors"].append(f"card click failed; will try original card_href fallback: {exc}")
                await snapshot_after_click(page, "click_failed")
            else:
                record["errors"].append("no valid visible card anchor found; using original card_href navigation fallback")
            record["open_strategy"] = "skipped-no-click"
            record["click_result"] = "no-valid-card-anchor"
            record["detail_state"] = "click_failed"
            await snapshot_after_click(page, "click_failed")

        detail_page = popup or page
        if not opened_by_click:
            card_href = card.get("card_href") or card.get("url", "")
            if is_dom_search_card(card) and is_valid_xhs_note_url(card_href):
                previous_strategy = record.get("open_strategy") or "click-card"
                record["open_strategy"] = f"{previous_strategy}+open-card-href"
                record["click_attempts"].append({"method": "open_card_href", "href": card_href, "ok": False})
                try:
                    record["before_click_url"] = record.get("before_click_url") or getattr(page, "url", "") or ""
                    await safe_goto(page, card_href, retries=1, timeout_ms=15000)
                    await page.wait_for_timeout(1200)
                    detail_page = page
                    await snapshot_after_click(detail_page, "url_changed")
                    opened_by_click = not is_bad_xhs_route(getattr(detail_page, "url", "") or "")
                    record["click_attempts"][-1]["ok"] = True
                    record["click_result"] = "open_card_href"
                except Exception as exc:
                    record["errors"].append(f"open original card_href failed: {exc}")
                    record["detail_open_status"] = "failed_navigation"
                    record["detail_opened"] = False
                    record["extraction_method"] = "search-card fallback"
                    record["access_status"] = classify_access_status(url, record.get("body_text_first_3000", ""), record["errors"])
                    return record
            else:
                if not is_dom_search_card(card):
                    record["errors"].append(
                        "skipped detail open: card came from API/search fallback and has no verified original DOM card_href"
                    )
                    record["detail_skip_reason"] = "no verified original DOM card_href"
                    record["detail_open_status"] = "skipped"
                else:
                    record["errors"].append("no valid original card_href found for navigation fallback")
                    record["detail_open_status"] = "failed_navigation"
                record["detail_opened"] = False
                record["extraction_method"] = "search-card fallback"
                record["access_status"] = classify_access_status(url, record.get("body_text_first_3000", ""), record["errors"])
                return record

        await detail_page.wait_for_timeout(1500)
        current_url = detail_page.url
        await network_capture.drain()
        record["api_fetch_attempted"] = False
        record["api_fetch_url"] = ""
        record["api_detail_fetch_count"] = 0
        route_blocked = False
        if is_bad_xhs_route(current_url):
            record["errors"].append(f"detail route returned 404/redirect page: {current_url}")
            record["detail_url"] = current_url
            route_blocked = True
        body_preview = ""
        dom_data: dict[str, Any] = {}
        if not route_blocked:
            body_preview = await detail_page.locator("body").inner_text(timeout=5000)
            if any(hint in body_preview for hint in UNAVAILABLE_HINTS):
                record["errors"].append("detail page unavailable or requires app verification; kept search-card fallback")
                record["detail_url"] = current_url
                route_blocked = True

        if not route_blocked:
            try:
                await detail_page.wait_for_selector(
                    '#detail-desc, [class*=comment], [class*=note], [class*=desc]',
                    timeout=6000,
                )
            except Exception:
                pass

            await expand_and_scroll_comments(detail_page, rounds=5)
            await detail_page.wait_for_timeout(700)
            await network_capture.drain()
            dom_data = await extract_detail_dom(detail_page)

        await network_capture.drain()
        network_data: dict[str, Any] = {}
        try:
            network_data = parse_network_raw_dir(network_capture.directory, note_id, max_comments) if network_capture.enabled else {}
        except Exception as exc:
            network_data = {"success": False, "parser_errors": [str(exc)]}
        parser_errors = list(network_data.get("parser_errors") or []) + list(network_capture.parser_errors)
        record["matched_api_responses"] = network_capture.matched_api_responses
        record["parsed_from_network"] = bool(network_data.get("success"))
        record["parser_errors"] = parser_errors
        record["network_source_files"] = network_data.get("source_files") or []
        api_data = network_data if network_data.get("success") else extract_from_api_payloads([item["data"] for item in api_payloads])
        body_text = dom_data.get("body_text") or ""
        if body_text and not record.get("body_text_first_3000"):
            record["body_text_first_3000"] = body_text[:3000]
        api_comments = api_data.get("comments") or []
        dom_comments = dom_data.get("comments") or []
        if network_data.get("success"):
            comments = dedupe_texts(api_comments or dom_comments, max_comments)
        else:
            comments = dedupe_texts(api_comments + dom_comments, max_comments)
        text = compact_text(api_data.get("desc") or api_data.get("text") or dom_data.get("text") or card.get("card_text", ""))
        has_api_or_dom_evidence = bool(api_data.get("desc") or api_data.get("text") or dom_data.get("text") or comments)
        extraction_method = "search-card fallback"
        detail_opened = False
        parsed_from_dom = bool(dom_data.get("text") or dom_comments)
        record["parsed_from_dom"] = parsed_from_dom
        if network_data.get("success"):
            extraction_method = "detail-page network parser"
            detail_opened = True
        elif route_blocked and (api_data.get("text") or api_comments):
            extraction_method = "detail-api fallback"
        elif not route_blocked:
            extraction_method = "detail-page click dom fallback" if opened_by_click else "detail-page original-url dom fallback"
            detail_opened = True
        record.update(
            {
                "title": api_data.get("title") or dom_data.get("title") or record["title"],
                "author": api_data.get("author") or dom_data.get("author") or "",
                "desc": api_data.get("desc") or dom_data.get("text") or "",
                "text": text,
                "content": text,
                "comments": comments,
                "images": api_data.get("images") or dom_data.get("images") or record["images"],
                "image_metadata": api_data.get("image_metadata") or [],
                "video": api_data.get("video") or dom_data.get("video") or record.get("video", ""),
                "video_metadata": api_data.get("video_metadata") or {},
                "like_count": parse_count(body_text, ["赞", "点赞", "喜欢"]),
                "collect_count": parse_count(body_text, ["收藏", "收蔵"]),
                "comment_count": parse_count(body_text, ["评论"]),
                "created_at": api_data.get("publish_time") or extract_display_date(body_text),
                "publish_time": api_data.get("publish_time") or extract_display_date(body_text),
                "location": "",
                "extraction_method": extraction_method,
                "detail_opened": detail_opened,
                "detail_url": current_url,
                "api_payload_count": len(api_payloads),
                "api_fetch_attempted": False,
                "api_detail_fetch_count": 0,
                "api_comment_count": len(api_comments),
                "dom_comment_count": len(dom_comments),
                "comment_source": "network" if network_data.get("success") and api_comments else "api+dom" if api_comments and dom_comments else "api" if api_comments else "dom" if dom_comments else "none",
                "visible_text_candidates": dom_data.get("visible_text_candidates") or [],
            }
        )
        for count_key in ["like_count", "collect_count", "comment_count"]:
            if api_data.get(count_key) is not None:
                record[count_key] = api_data.get(count_key)
        record["access_status"] = classify_access_status(current_url, body_preview, record.get("errors", []), api_ok=has_api_or_dom_evidence)
        if not has_api_or_dom_evidence:
            record["errors"].append("detail/API fallback produced no body/comments")
            record["access_status"] = classify_access_status(current_url, body_preview, record.get("errors", []), api_ok=False)
    except Exception as exc:
        record["errors"].append(str(exc))
    finally:
        record["detail_finished_at"] = record.get("detail_finished_at") or now_iso()
        record["detail_open_status"] = classify_detail_open_status(record)
        if diagnostics and diagnostics.enabled:
            try:
                active_page = popup or detail_page or page
                screenshot = await diagnostics.screenshot(active_page, f"detail-{attempt_number or len(diagnostics.events) + 1}")
                diagnostics.add(
                    "detail_attempt",
                    {
                        "attempt": attempt_number,
                        "ProjectRoot": PROJECT_ROOT,
                        "collect_script_path": COLLECT_SCRIPT_PATH,
                        "card_index": record.get("card_index", 0),
                        "id": record.get("id", ""),
                        "query": query,
                        "title": one_line(record.get("title", ""), 120),
                        "source_url": url,
                        "search_url_before_click": record.get("search_url_before_click", ""),
                        "requested_detail_url": record.get("requested_detail_url", ""),
                        "clicked_dom_href": record.get("clicked_dom_href", ""),
                        "anchor_match": record.get("anchor_match", ""),
                        "candidate_count": record.get("candidate_count", 0),
                        "raw_dom_href": record.get("raw_dom_href", ""),
                        "anchor_text": one_line(record.get("anchor_text", ""), 500),
                        "anchor_visible": bool(record.get("anchor_visible")),
                        "anchor_enabled": bool(record.get("anchor_enabled")),
                        "anchor_box": record.get("anchor_box", {}),
                        "before_click_url": record.get("before_click_url", ""),
                        "after_click_url": record.get("after_click_url", ""),
                        "page_count_before": record.get("page_count_before", 0),
                        "page_count_after": record.get("page_count_after", 0),
                        "active_page_url": record.get("active_page_url", ""),
                        "detail_state": record.get("detail_state", ""),
                        "body_text_first_3000": record.get("body_text_first_3000", ""),
                        "matched_detail_selectors": record.get("matched_detail_selectors", []),
                        "click_attempts": record.get("click_attempts", []),
                        "click_result": record.get("click_result", ""),
                        "screenshot_path_after_click": record.get("screenshot_path_after_click", ""),
                        "network_capture_enabled": bool(record.get("network_capture_enabled")),
                        "network_raw_dir": record.get("network_raw_dir", ""),
                        "matched_api_responses": record.get("matched_api_responses", []),
                        "parsed_from_network": bool(record.get("parsed_from_network")),
                        "parsed_from_dom": bool(record.get("parsed_from_dom")),
                        "parser_errors": record.get("parser_errors", []),
                        "network_source_files": record.get("network_source_files", []),
                        "final_url": getattr(active_page, "url", ""),
                        "open_strategy": record.get("open_strategy", ""),
                        "extraction_method": record.get("extraction_method", ""),
                        "detail_opened": bool(record.get("detail_opened")),
                        "api_payload_count": len(api_payloads),
                        "api_comment_count": record.get("api_comment_count", 0),
                        "dom_comment_count": record.get("dom_comment_count", 0),
                        "comment_count": len(record.get("comments", []) or []),
                        "errors": record.get("errors", []),
                        "screenshot": screenshot,
                    },
                )
            except Exception:
                pass
        try:
            page.context.remove_listener("response", response_listener)
        except Exception:
            pass
        if popup:
            try:
                await popup.close()
            except Exception:
                pass
        else:
            try:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(400)
                if "search_result" not in page.url:
                    await page.go_back(wait_until="domcontentloaded", timeout=8000)
                    await page.wait_for_timeout(700)
            except Exception:
                pass
    return record


def extract_display_date(text: str) -> str:
    patterns = [
        r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}",
        r"\d{1,2}[-/.]\d{1,2}",
        r"(?:今天|昨天|前天)",
        r"\d+\s*(?:小时前|分钟前|天前|周前|个月前)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "")
        if match:
            return match.group(0)
    return ""


def dedupe_records(records: list[dict[str, Any]], max_notes: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for record in records:
        key = record.get("id") or record.get("url") or re.sub(r"\s+", "", record.get("title", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(record)
        if len(out) >= max_notes:
            break
    return out


def detail_failed(record: dict[str, Any]) -> bool:
    errors = " | ".join(record.get("errors", []) or [])
    if record.get("extraction_method") == "detail-api fallback" and (record.get("text") or record.get("comments")):
        return False
    if record.get("extraction_method") == "search-card fallback" and errors:
        return True
    return any(
        token in errors
        for token in ["404", "invalid XHS note URL", "unavailable", "timed out", "requires app verification"]
    )


def text_length_for_record(record: dict[str, Any]) -> int:
    text = record.get("content") or record.get("text") or record.get("desc") or ""
    return len(str(text or "").strip())


def classify_detail_open_status(record: dict[str, Any]) -> str:
    explicit = str(record.get("detail_open_status") or "").strip()
    if explicit in DETAIL_OPEN_STATUSES and explicit != "unknown":
        return explicit
    errors = " | ".join(str(item) for item in (record.get("errors") or []))
    access_status = str(record.get("access_status") or "")
    detail_state = str(record.get("detail_state") or "")
    if not record.get("detail_attempted"):
        return "skipped"
    if "timed out" in errors.lower() or "timeout" in errors.lower():
        return "timeout"
    if "login" in access_status or "cookie_invalid" in access_status or "鐧诲綍" in errors:
        return "login_required"
    if "verification" in access_status or "rate_limited" in access_status or "page_blocked" in access_status:
        return "blocked"
    if record.get("detail_opened"):
        has_detail_evidence = bool(text_length_for_record(record) or record.get("comments") or record.get("images"))
        return "success" if has_detail_evidence else "empty_detail"
    if "404" in errors or "redirect" in errors:
        return "blocked"
    if "navigation" in errors.lower():
        return "failed_navigation"
    if detail_state == "click_failed" or "click failed" in errors.lower() or "no valid visible card anchor" in errors:
        return "failed_click"
    if record.get("extraction_method") == "search-card fallback":
        return "failed_click" if record.get("detail_attempted") else "skipped"
    return "unknown"


def detail_audit_entry(record: dict[str, Any], rank: int) -> dict[str, Any]:
    status = classify_detail_open_status(record)
    errors = record.get("errors") or []
    return {
        "search_rank": int(record.get("search_rank") or rank),
        "note_id": str(record.get("note_id") or record.get("id") or ""),
        "title": record.get("title", ""),
        "card_href": record.get("card_href") or record.get("requested_detail_url") or record.get("url", ""),
        "detail_attempted": bool(record.get("detail_attempted")),
        "detail_open_method": record.get("open_strategy") or record.get("click_result") or "",
        "detail_open_status": status,
        "detail_skip_reason": record.get("detail_skip_reason", "") if not record.get("detail_attempted") else "",
        "detail_error": " | ".join(str(item) for item in errors if item),
        "detail_url_final": record.get("detail_url") or record.get("active_page_url") or record.get("after_click_url") or "",
        "detail_text_length": text_length_for_record(record),
        "image_count": len(record.get("images") or []),
        "captured_comment_count": len(record.get("comments") or []),
        "detail_started_at": record.get("detail_started_at", ""),
        "detail_finished_at": record.get("detail_finished_at", ""),
    }


def build_detail_audit(records: list[dict[str, Any]], args: argparse.Namespace, warnings: list[str] | None = None) -> dict[str, Any]:
    entries = [detail_audit_entry(record, index + 1) for index, record in enumerate(records)]
    attempted_count = sum(1 for item in entries if item["detail_attempted"])
    success_count = sum(1 for item in entries if item["detail_open_status"] == "success")
    record_count = len(entries)
    success_rate = success_count / record_count if record_count else 0.0
    collection_incomplete = record_count > 0 and success_rate < 0.70
    status_counts = {status: 0 for status in sorted(DETAIL_OPEN_STATUSES)}
    for item in entries:
        status_counts[item["detail_open_status"]] = status_counts.get(item["detail_open_status"], 0) + 1
    return {
        "generated_at": now_iso(),
        "records": record_count,
        "detail_attempted": attempted_count,
        "detail_success": success_count,
        "detail_success_rate": round(success_rate, 4),
        "collection_incomplete": collection_incomplete,
        "detail_mode": "none" if getattr(args, "no_detail", False) else getattr(args, "detail_mode", "full"),
        "max_notes": int(getattr(args, "max_notes", 0) or 0),
        "max_detail_notes": int(getattr(args, "max_detail_notes", 0) or 0),
        "status_counts": status_counts,
        "warnings": warnings or [],
        "items": entries,
    }


def detail_audit_markdown(audit: dict[str, Any]) -> str:
    lines = [
        "# Detail Open Audit",
        "",
        f"- generated_at: {audit.get('generated_at', '')}",
        f"- records: {audit.get('records', 0)}",
        f"- detail_attempted: {audit.get('detail_attempted', 0)}",
        f"- detail_success: {audit.get('detail_success', 0)}",
        f"- detail_success_rate: {audit.get('detail_success_rate', 0)}",
        f"- collection_incomplete: {audit.get('collection_incomplete', False)}",
        f"- detail_mode: {audit.get('detail_mode', '')}",
        f"- max_detail_notes: {audit.get('max_detail_notes', 0)}",
        "",
        "## Status Counts",
        "",
    ]
    for status, count in (audit.get("status_counts") or {}).items():
        lines.append(f"- {status}: {count}")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "| Rank | Status | Attempted | Note ID | Title | Method | Text | Images | Comments | Skip/Error |",
            "| ---: | --- | --- | --- | --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for item in audit.get("items", []):
        reason = item.get("detail_skip_reason") or item.get("detail_error") or ""
        lines.append(
            "| {rank} | {status} | {attempted} | {note_id} | {title} | {method} | {text_len} | {images} | {comments} | {reason} |".format(
                rank=item.get("search_rank", ""),
                status=item.get("detail_open_status", ""),
                attempted="yes" if item.get("detail_attempted") else "no",
                note_id=one_line(item.get("note_id", ""), 80).replace("|", "\\|"),
                title=one_line(item.get("title", ""), 80).replace("|", "\\|"),
                method=one_line(item.get("detail_open_method", ""), 80).replace("|", "\\|"),
                text_len=item.get("detail_text_length", 0),
                images=item.get("image_count", 0),
                comments=item.get("captured_comment_count", 0),
                reason=one_line(reason, 140).replace("|", "\\|"),
            )
        )
    return "\n".join(lines) + "\n"


def write_detail_audit_files(out_path: str, audit: dict[str, Any]) -> tuple[str, str]:
    base_dir = Path(out_path).resolve().parent
    json_path = base_dir / "detail-open-audit.json"
    md_path = base_dir / "detail-open-audit.md"
    write_json(str(json_path), audit)
    md_path.write_text(detail_audit_markdown(audit), encoding="utf-8")
    return str(json_path), str(md_path)


async def collect(args: argparse.Namespace) -> dict[str, Any]:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        raise SystemExit("playwright is not installed. Run trend_xhs_observer.cmd -InstallDeps first.") from exc

    request = load_json(args.request)
    queries = request.get("expanded_queries") or [request.get("query")]
    queries = [query for query in queries if query]

    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    warnings: list[str] = []
    diagnostics = Diagnostics(args.diagnostics_dir, args.diagnostic_limit)
    network_raw_dir = Path(args.network_raw_dir) if args.network_raw_dir else Path(args.out).resolve().parent / "network_raw"
    network_raw_dir.mkdir(parents=True, exist_ok=True)
    if diagnostics.enabled:
        diagnostics.add(
            "runtime_context",
            {
                "ProjectRoot": PROJECT_ROOT,
                "collect_script_path": COLLECT_SCRIPT_PATH,
                "network_capture_enabled": True,
                "network_raw_dir": str(network_raw_dir),
            },
        )
    collected_ids = load_id_set(args.collected_index) if args.collected_index and not args.no_skip_collected else set()
    skipped_existing = 0

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(args.cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        context.set_default_timeout(12000)
        context.set_default_navigation_timeout(25000)
        worker = context.pages[-1] if context.pages else await context.new_page()
        worker.on("dialog", lambda dialog: asyncio.create_task(dialog.dismiss()))
        detail_mode = "none" if args.no_detail else args.detail_mode
        max_detail_notes = args.max_notes if args.max_detail_notes <= 0 else min(args.max_detail_notes, args.max_notes)
        consecutive_detail_failures = 0
        candidate_cards: list[dict[str, str]] = []
        seen_candidate_keys: set[str] = set()
        for query in queries:
            try:
                cards, query_warnings = await search_query(
                    worker,
                    query,
                    args.per_query,
                    args.scroll_rounds,
                    args.sort,
                    diagnostics,
                )
                warnings.extend(query_warnings)
                for card_index, card in enumerate(cards):
                    if len(candidate_cards) >= args.max_notes:
                        break
                    note_id = note_id_from_url(card.get("url", ""))
                    if note_id and note_id in collected_ids and not args.no_skip_collected:
                        skipped_existing += 1
                        continue
                    key = note_id or card.get("canonical_url", "") or card.get("url", "") or card.get("title", "")
                    if not key or key in seen_candidate_keys:
                        continue
                    seen_candidate_keys.add(key)
                    candidate = dict(card)
                    candidate["query"] = query
                    candidate["search_rank"] = len(candidate_cards) + 1
                    candidate["query_rank"] = card_index + 1
                    candidate["card_href"] = candidate.get("url", "")
                    candidate_cards.append(candidate)
                if len(candidate_cards) >= args.max_notes:
                    break
            except Exception as exc:
                errors.append({"query": query, "error": str(exc)})

        diagnostics.add(
            "detail_plan",
            {
                "candidate_count": len(candidate_cards),
                "detail_mode": detail_mode,
                "max_detail_notes": max_detail_notes,
                "max_notes": args.max_notes,
            },
        )

        for card in candidate_cards[: args.max_notes]:
            query = card.get("query", "")
            search_rank = int(card.get("search_rank") or (len(records) + 1))
            should_attempt_detail = detail_mode != "none" and len([r for r in records if r.get("detail_attempted")]) < max_detail_notes
            if detail_mode == "top" and not should_attempt_detail:
                reason = f"DetailMode=top only opens first {max_detail_notes} notes"
                record = skipped_detail_record(card, query, reason)
            elif detail_mode == "none":
                record = skipped_detail_record(card, query, "DetailMode=none; detail opening disabled")
            elif not should_attempt_detail:
                record = skipped_detail_record(card, query, f"MaxDetailNotes={max_detail_notes} reached")
            else:
                try:
                    record = await asyncio.wait_for(
                        extract_detail(
                            worker,
                            card,
                            query,
                            args.max_comments,
                            card.get("search_url", worker.url),
                            diagnostics,
                            search_rank,
                            max(0, int(card.get("query_rank") or 1) - 1),
                            network_raw_dir,
                        ),
                        timeout=args.detail_timeout,
                    )
                except asyncio.TimeoutError:
                    record = timeout_detail_record(card, query, args.detail_timeout)
                if detail_failed(record):
                    consecutive_detail_failures += 1
                else:
                    consecutive_detail_failures = 0
                if args.max_detail_failures and consecutive_detail_failures >= args.max_detail_failures:
                    warnings.append(
                        f"detail pages failed {consecutive_detail_failures} times in a row; "
                        "continuing because DetailMode requires explicit audit for every note"
                    )
            record["search_rank"] = record.get("search_rank") or search_rank
            record["card_href"] = record.get("card_href") or card.get("card_href") or card.get("url", "")
            record["detail_open_status"] = classify_detail_open_status(record)
            records.append(record)
            record_id = record.get("note_id") or record.get("id") or note_id_from_url(card.get("url", ""))
            if record_id:
                collected_ids.add(str(record_id))
            audit = build_detail_audit(records, args, warnings)
            write_detail_audit_files(args.out, audit)
            if record.get("detail_attempted") and len(records) < len(candidate_cards[: args.max_notes]):
                delay_ms = random.randint(900, 2200)
                record["post_detail_wait_ms"] = delay_ms
                diagnostics.add(
                    "detail_pacing",
                    {
                        "note_id": record_id or "",
                        "query": query,
                        "delay_ms": delay_ms,
                        "reason": "random wait between detail pages",
                    },
                )
                await worker.wait_for_timeout(delay_ms)
        await browser.close()

    if args.collected_index and not args.no_skip_collected:
        save_id_set(args.collected_index, collected_ids)

    final_records = dedupe_records(records, args.max_notes)
    final_detail_audit = build_detail_audit(final_records, args, warnings)
    if final_detail_audit.get("collection_incomplete"):
        incomplete_warning = (
            f"collection_incomplete: detail success {final_detail_audit.get('detail_success', 0)}/"
            f"{final_detail_audit.get('records', 0)} "
            f"({final_detail_audit.get('detail_success_rate', 0)}) is below 0.70"
        )
        if incomplete_warning not in warnings:
            warnings.append(incomplete_warning)
        final_detail_audit = build_detail_audit(final_records, args, warnings)
    detail_audit_json, detail_audit_md = write_detail_audit_files(args.out, final_detail_audit)
    detail_records = [record for record in final_records if str(record.get("extraction_method", "")).startswith("detail-page")]
    api_detail_records = [record for record in final_records if record.get("extraction_method") == "detail-api fallback"]
    evidence_records = [
        record
        for record in final_records
        if record.get("extraction_method") != "search-card fallback" and (record.get("text") or record.get("comments"))
    ]
    records_with_comments = [record for record in final_records if record.get("comments")]
    total_comments = sum(len(record.get("comments", []) or []) for record in final_records)
    diagnostics_file = ""
    if diagnostics.enabled:
        diagnostics.add(
            "summary",
            {
                "record_count": len(final_records),
                "ProjectRoot": PROJECT_ROOT,
                "collect_script_path": COLLECT_SCRIPT_PATH,
                "network_capture_enabled": True,
                "network_raw_dir": str(network_raw_dir),
                "detail_record_count": len(detail_records),
                "api_detail_record_count": len(api_detail_records),
                "evidence_record_count": len(evidence_records),
                "comment_record_count": len(records_with_comments),
                "total_comment_count": total_comments,
                "error_count": len(errors),
                "warning_count": len(warnings),
                "skipped_existing_count": skipped_existing,
            },
        )
        diagnostics_file = diagnostics.write()

    return {
        "meta": {
            "platform": "xiaohongshu",
            "queries": queries,
            "collected_at": now_iso(),
            "request_topic": request.get("meta", {}).get("topic", ""),
            "collection_mode": "browser-cdp",
            "sort": args.sort,
            "detail_mode": "none" if args.no_detail else args.detail_mode,
            "max_detail_notes": args.max_notes if args.max_detail_notes <= 0 else min(args.max_detail_notes, args.max_notes),
            "detail_disabled_during_run": args.no_detail or (args.detail_mode == "none"),
            "detail_attempted_count": final_detail_audit.get("detail_attempted", 0),
            "detail_success_count": final_detail_audit.get("detail_success", 0),
            "detail_success_rate": final_detail_audit.get("detail_success_rate", 0),
            "collection_incomplete": bool(final_detail_audit.get("collection_incomplete")),
            "collection_status": "collection_incomplete" if final_detail_audit.get("collection_incomplete") else "ok",
            "detail_open_audit_json": detail_audit_json,
            "detail_open_audit_md": detail_audit_md,
            "skipped_existing_count": skipped_existing,
            "collected_index": args.collected_index,
            "detail_record_count": len(detail_records),
            "api_detail_record_count": len(api_detail_records),
            "evidence_record_count": len(evidence_records),
            "comment_record_count": len(records_with_comments),
            "total_comment_count": total_comments,
            "fallback_record_count": len(final_records) - len(evidence_records),
            "diagnostics_file": diagnostics_file,
        },
        "records": final_records,
        "errors": errors,
        "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Xiaohongshu notes through a local CDP browser.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--max-notes", type=int, default=60)
    parser.add_argument("--per-query", type=int, default=10)
    parser.add_argument("--max-comments", type=int, default=30)
    parser.add_argument("--scroll-rounds", type=int, default=5)
    parser.add_argument("--detail-timeout", type=int, default=18)
    parser.add_argument("--detail-mode", choices=["full", "top", "none"], default="full")
    parser.add_argument("--max-detail-notes", type=int, default=0)
    parser.add_argument("--max-detail-failures", type=int, default=4)
    parser.add_argument("--sort", choices=["general", "latest", "popular", "comments", "collects"], default="general")
    parser.add_argument("--collected-index", default="")
    parser.add_argument("--no-skip-collected", action="store_true")
    parser.add_argument("--no-detail", action="store_true")
    parser.add_argument("--diagnostics-dir", default="")
    parser.add_argument("--diagnostic-limit", type=int, default=8)
    parser.add_argument("--network-raw-dir", default="")
    args = parser.parse_args()

    raw = asyncio.run(collect(args))
    write_json(args.out, raw)
    print(f"raw XHS written: {args.out}")
    print(
        f"records={len(raw.get('records', []))} "
        f"detail_records={raw.get('meta', {}).get('detail_record_count', 0)} "
        f"detail_attempted={raw.get('meta', {}).get('detail_attempted_count', 0)} "
        f"detail_success={raw.get('meta', {}).get('detail_success_count', 0)} "
        f"collection_status={raw.get('meta', {}).get('collection_status', '')} "
        f"comment_records={raw.get('meta', {}).get('comment_record_count', 0)} "
        f"comments={raw.get('meta', {}).get('total_comment_count', 0)} "
        f"errors={len(raw.get('errors', []))} "
        f"warnings={len(raw.get('warnings', []))}"
    )


if __name__ == "__main__":
    main()
