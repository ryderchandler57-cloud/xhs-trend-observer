from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


CN_TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def parse_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip().lstrip("\ufeff")] = value.strip().strip('"').strip("'")
    return values


def write_env(path: Path, updates: dict[str, str]) -> None:
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    lines: list[str] = []
    for line in existing:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip().lstrip("\ufeff")
        if key in updates:
            if key in seen:
                continue
            lines.append(f'{key}="{updates[key]}"')
            seen.add(key)
        else:
            if key in seen:
                continue
            lines.append(line)
            seen.add(key)
    for key, value in updates.items():
        if key not in seen:
            lines.append(f'{key}="{value}"')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def parse_cookie_string(cookie_string: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in re.split(r";\s*", cookie_string or ""):
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if key:
            result[key] = value.strip()
    return result


def cookie_string_from_browser(cookies: list[dict[str, Any]]) -> str:
    keep: dict[str, str] = {}
    for cookie in cookies:
        domain = str(cookie.get("domain", ""))
        name = str(cookie.get("name", ""))
        value = str(cookie.get("value", ""))
        if not name or "xiaohongshu.com" not in domain:
            continue
        keep[name] = value
    priority = ["a1", "web_session", "webId", "gid", "websectiga", "sec_poison_id", "xsecappid"]
    ordered = [(key, keep.pop(key)) for key in priority if key in keep]
    ordered.extend(sorted(keep.items()))
    return "; ".join(f"{key}={value}" for key, value in ordered)


async def seed_cookies(context: Any, cookie_string: str) -> int:
    cookies = parse_cookie_string(cookie_string)
    if not cookies:
        return 0
    browser_cookies = []
    for name, value in cookies.items():
        browser_cookies.append({"name": name, "value": value, "domain": ".xiaohongshu.com", "path": "/"})
    await context.add_cookies(browser_cookies)
    return len(browser_cookies)


async def health_check(page: Any) -> dict[str, Any]:
    try:
        if "xiaohongshu.com" not in page.url:
            await page.goto("https://www.xiaohongshu.com/explore", wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(900)
    except Exception as exc:
        return {"ok": False, "status": "navigation_failed", "message": str(exc)}

    try:
        body = await page.locator("body").inner_text(timeout=5000)
    except Exception:
        body = ""

    api_result = await page.evaluate(
        """
        async () => {
          try {
            const response = await fetch('/api/sns/web/v2/user/me', {
              credentials: 'include',
              headers: { 'accept': 'application/json, text/plain, */*' }
            });
            const text = await response.text();
            let data = null;
            try { data = JSON.parse(text); } catch (_) {}
            return { ok: response.ok, status: response.status, data, text: text.slice(0, 400) };
          } catch (error) {
            return { ok: false, status: 0, data: null, text: String(error) };
          }
        }
        """
    )

    login_hint = any(token in body for token in ["登录", "扫码", "验证码", "安全验证"])
    data = api_result.get("data") if isinstance(api_result, dict) else None
    api_success = bool(isinstance(data, dict) and (data.get("success") or data.get("data")))
    if api_success and not login_hint:
        return {"ok": True, "status": "ok", "message": "cookie health check passed", "api": api_result}
    if login_hint:
        return {"ok": False, "status": "login_required", "message": "XHS page asks for login or verification", "api": api_result}
    if isinstance(api_result, dict) and api_result.get("status") in {401, 403}:
        return {"ok": False, "status": "cookie_invalid", "message": "XHS API rejected cookie", "api": api_result}
    return {"ok": False, "status": "unknown", "message": "cookie health check inconclusive", "api": api_result}


async def run(args: argparse.Namespace) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    env_path = Path(args.env_file)
    env = parse_env(env_path)
    cookie_string = "" if args.refresh else env.get("COOKIES", "")
    result: dict[str, Any] = {
        "env_file": str(env_path),
        "loaded_cookie": bool(cookie_string),
        "seeded_cookie_count": 0,
        "saved_cookie": False,
        "health": {},
        "saved_at": "",
    }

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(args.cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[-1] if context.pages else await context.new_page()
        if cookie_string:
            result["seeded_cookie_count"] = await seed_cookies(context, cookie_string)
        health = await health_check(page)
        cookies = await context.cookies()
        names = {cookie.get("name") for cookie in cookies if "xiaohongshu.com" in str(cookie.get("domain", ""))}
        if not health.get("ok") and health.get("status") == "unknown" and "web_session" in names:
            health = {
                **health,
                "ok": True,
                "status": "cookie_present_api_inconclusive",
                "message": "web_session cookie exists and no explicit login page was detected",
            }
        result["health"] = health

        if not health.get("ok") and args.interactive:
            print("Cookie is missing or invalid. Log in or finish verification in the opened XHS browser window.")
            try:
                input("Press Enter here after Xiaohongshu is logged in and usable...")
            except EOFError:
                result["health"] = {
                    **health,
                    "message": f"{health.get('message', 'cookie check failed')}; non-interactive terminal cannot wait for login",
                }
            else:
                health = await health_check(page)
                cookies = await context.cookies()
                names = {cookie.get("name") for cookie in cookies if "xiaohongshu.com" in str(cookie.get("domain", ""))}
                if not health.get("ok") and health.get("status") == "unknown" and "web_session" in names:
                    health = {
                        **health,
                        "ok": True,
                        "status": "cookie_present_api_inconclusive",
                        "message": "web_session cookie exists and no explicit login page was detected",
                    }
                result["health"] = health

        cookies = await context.cookies()
        latest = cookie_string_from_browser(cookies)
        if latest:
            write_env(
                env_path,
                {
                    "COOKIES": latest,
                    "XHS_COOKIE_SAVED_AT": now_iso(),
                    "XHS_COOKIE_HEALTH": "ok" if health.get("ok") else str(health.get("status", "unknown")),
                },
            )
            result["saved_cookie"] = True
            result["saved_at"] = now_iso()
        # Do not close a CDP-attached visible browser here; the collector reuses it.
        # Let the Python process drop the connection naturally.

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Load, check, and save Xiaohongshu cookies through a local CDP browser.")
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    result = asyncio.run(run(args))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("health", {}).get("ok"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
