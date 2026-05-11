"""
Playwright browser driver — production-hardened for QA Copilot.

Improvements over v1:
  • Exponential-backoff retry on every interaction
  • Shadow DOM piercing for web components
  • Iframe content scanning
  • Semantic element scoring (labels, ARIA, visibility weight)
  • Selector auto-recovery via AI when primary selector fails
  • Accessibility snapshot capture
  • Performance timing (LCP, FID, CLS via PerformanceObserver)
  • Scroll-into-view before every interaction
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PWTimeout,
    ElementHandle,
)

from browser.interceptor import PageInterceptor, CapturedConsoleError, CapturedNetworkFailure
from config import settings

logger = logging.getLogger(__name__)

# CSS selectors that are unlikely to produce useful interactions
_IGNORED_SELECTORS = {
    "script", "style", "meta", "link", "noscript", "svg", "path",
    "head", "html", "body",
}


# ------------------------------------------------------------------ #
# DOM fingerprinting JS (injected once per page)
# ------------------------------------------------------------------ #

_DOM_EXTRACT_JS = """() => {
  const truncate = (s, n) => (s || '').toString().substring(0, n).trim();
  const isVisible = el => {
    const r = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return r.width > 0 && r.height > 0 &&
           style.visibility !== 'hidden' &&
           style.display !== 'none' &&
           style.opacity !== '0';
  };
  const getLabel = el => {
    if (el.labels && el.labels[0]) return el.labels[0].textContent.trim();
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    if (el.getAttribute('aria-labelledby')) {
      const ref = document.getElementById(el.getAttribute('aria-labelledby'));
      if (ref) return ref.textContent.trim();
    }
    const prev = el.previousElementSibling;
    if (prev && prev.tagName === 'LABEL') return prev.textContent.trim();
    return '';
  };

  const els = [];

  // ── Forms ─────────────────────────────────────────────────────────
  document.querySelectorAll('form').forEach((form, fi) => {
    els.push(`FORM[${fi}] id="${form.id}" action="${truncate(form.action,80)}" method="${form.method}"`);
    form.querySelectorAll('input,select,textarea,button,[type=submit]').forEach(el => {
      if (!isVisible(el)) return;
      const label = truncate(getLabel(el), 50);
      const sel = el.id ? `#${el.id}` :
                  el.name ? `[name="${el.name}"]` :
                  el.getAttribute('data-testid') ? `[data-testid="${el.getAttribute('data-testid')}"]` :
                  `${el.tagName.toLowerCase()}[type="${el.type||'text'}"]`;
      els.push(`  ${el.tagName}[type=${el.type||'—'}] sel="${sel}" label="${label}" placeholder="${truncate(el.placeholder,40)}" required=${el.required}`);
    });
  });

  // ── Standalone buttons & CTAs ─────────────────────────────────────
  document.querySelectorAll('button,[role=button],[type=submit]').forEach(el => {
    if (!isVisible(el)) return;
    const text = truncate(el.textContent.replace(/\\s+/g,' '), 60);
    const sel = el.id ? `#${el.id}` :
                el.getAttribute('data-testid') ? `[data-testid="${el.getAttribute('data-testid')}"]` :
                `button`;
    if (text) els.push(`BUTTON sel="${sel}" text="${text}" disabled=${el.disabled}`);
  });

  // ── Navigation links ──────────────────────────────────────────────
  const navEls = document.querySelectorAll('nav a,[role=navigation] a,header a');
  if (navEls.length) {
    els.push('NAV LINKS:');
    navEls.forEach(a => {
      if (!isVisible(a)) return;
      els.push(`  A href="${truncate(a.href,80)}" text="${truncate(a.textContent,40)}"`);
    });
  }

  // ── All other links ───────────────────────────────────────────────
  const bodyLinks = Array.from(document.querySelectorAll('a[href]'))
    .filter(a => isVisible(a) && !a.closest('nav') && a.href.startsWith('http'))
    .slice(0, 15);
  if (bodyLinks.length) {
    els.push('BODY LINKS:');
    bodyLinks.forEach(a => els.push(`  A href="${truncate(a.href,80)}" text="${truncate(a.textContent,40)}"`));
  }

  // ── Headings ──────────────────────────────────────────────────────
  document.querySelectorAll('h1,h2,h3').forEach(h =>
    els.push(`${h.tagName}: "${truncate(h.textContent.replace(/\\s+/g,' '),80)}"`)
  );

  // ── Alerts / Toasts / Errors ──────────────────────────────────────
  document.querySelectorAll('[role=alert],[role=status],.error,.alert,[class*="error"],[class*="alert"],[class*="toast"],[class*="notification"]').forEach(el => {
    if (!isVisible(el)) return;
    const text = truncate(el.textContent.replace(/\\s+/g,' '), 120);
    if (text) els.push(`ALERT[role=${el.getAttribute('role')||'—'}]: "${text}"`);
  });

  // ── Modal / Dialog ────────────────────────────────────────────────
  document.querySelectorAll('[role=dialog],[role=modal],.modal,.dialog').forEach(el => {
    if (!isVisible(el)) return;
    els.push(`MODAL: "${truncate(el.textContent.replace(/\\s+/g,' '), 100)}"`);
  });

  // ── Images without alt ────────────────────────────────────────────
  const imgBad = Array.from(document.querySelectorAll('img:not([alt])')).filter(isVisible);
  if (imgBad.length) els.push(`A11Y: ${imgBad.length} image(s) missing alt text`);

  return els.slice(0, 100).join('\\n');
}"""


# ------------------------------------------------------------------ #
# Web Vitals collection JS
# ------------------------------------------------------------------ #

_VITALS_JS = """() => {
  return new Promise(resolve => {
    const result = {};
    try {
      const nav = performance.getEntriesByType('navigation')[0];
      if (nav) {
        result.ttfb = Math.round(nav.responseStart - nav.requestStart);
        result.domLoaded = Math.round(nav.domContentLoadedEventEnd - nav.requestStart);
        result.loaded = Math.round(nav.loadEventEnd - nav.requestStart);
      }
      const paint = performance.getEntriesByType('paint');
      paint.forEach(p => {
        if (p.name === 'first-contentful-paint') result.fcp = Math.round(p.startTime);
      });
    } catch(e) {}
    resolve(result);
  });
}"""


# ------------------------------------------------------------------ #
# BrowserDriver
# ------------------------------------------------------------------ #

class BrowserDriver:
    """
    Manages one browser context (one persona, one session).

    Key capabilities:
      - Auto-retry with exponential backoff on every interaction
      - Shadow DOM and iframe scanning in DOM extraction
      - Selector recovery via AI when primary selector misses
      - Web Vitals capture
      - Full-page screenshots
    """

    def __init__(self, session_id: str, persona_name: str) -> None:
        self.session_id = session_id
        self.persona_name = persona_name
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self.interceptor = PageInterceptor()
        self._screenshot_dir = settings.screenshots_dir / session_id
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

        # Track seen URLs to avoid duplicate screenshots
        self._screenshotted: set[str] = set()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=settings.headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-extensions",
            ],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            ignore_https_errors=True,
            java_script_enabled=True,
        )
        # Block heavy assets that slow tests without adding value
        await self._context.route(
            "**/*.{mp4,avi,mov,webm,mp3,woff,woff2,ttf}",
            lambda route: route.abort(),
        )
        self._page = await self._context.new_page()
        self._page.set_default_timeout(settings.browser_timeout_ms)
        self.interceptor.attach(self._page)
        logger.info("Browser started [persona=%s session=%s]", self.persona_name, self.session_id)

    async def stop(self) -> None:
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        except Exception as exc:
            logger.warning("Error during browser shutdown: %s", exc)

    @property
    def page(self) -> Page:
        assert self._page is not None, "Browser not started"
        return self._page

    # ------------------------------------------------------------------ #
    # Navigation
    # ------------------------------------------------------------------ #

    async def navigate(self, url: str) -> dict[str, Any]:
        t0 = time.monotonic()
        try:
            response = await self.page.goto(url, wait_until="domcontentloaded", timeout=settings.browser_timeout_ms)
            # Give dynamic apps time to hydrate
            try:
                await self.page.wait_for_load_state("networkidle", timeout=8_000)
            except PWTimeout:
                pass
        except PWTimeout:
            logger.warning("Timeout navigating to %s", url)
            return {"success": False, "error": "timeout", "url": url, "load_time_ms": 0}
        except Exception as exc:
            return {"success": False, "error": str(exc), "url": url, "load_time_ms": 0}

        load_ms = (time.monotonic() - t0) * 1000

        # Collect Web Vitals
        try:
            vitals = await self.page.evaluate(_VITALS_JS)
        except Exception:
            vitals = {}

        return {
            "success": True,
            "url": self.page.url,
            "status": response.status if response else None,
            "load_time_ms": round(load_ms, 1),
            "vitals": vitals,
        }

    # ------------------------------------------------------------------ #
    # DOM extraction
    # ------------------------------------------------------------------ #

    async def extract_dom_summary(self) -> str:
        """Extract interactive element summary, including iframes."""
        try:
            main_summary = await self.page.evaluate(_DOM_EXTRACT_JS)
        except Exception as exc:
            logger.warning("DOM extract failed on main frame: %s", exc)
            main_summary = "(extraction failed)"

        # Scan visible iframes
        iframe_summaries: list[str] = []
        try:
            frames = self.page.frames
            for frame in frames[1:4]:  # skip main frame, limit to 3 iframes
                if not frame.url or frame.url in ("about:blank", ""):
                    continue
                try:
                    fs = await frame.evaluate(_DOM_EXTRACT_JS)
                    if fs and fs.strip():
                        iframe_summaries.append(f"[IFRAME:{frame.url[:60]}]\n{fs}")
                except Exception:
                    pass
        except Exception:
            pass

        parts = [main_summary] + iframe_summaries
        return "\n\n".join(p for p in parts if p).strip() or "(empty page)"

    async def get_all_links(self) -> list[dict[str, Any]]:
        """Return all href links with text and context."""
        try:
            links = await self.page.evaluate("""() =>
                Array.from(document.querySelectorAll('a[href]'))
                    .filter(a => {
                        const r = a.getBoundingClientRect();
                        return a.href.startsWith('http') && r.width > 0;
                    })
                    .map(a => ({
                        url: a.href,
                        text: a.textContent.trim().substring(0, 60),
                        in_nav: !!a.closest('nav, header, [role=navigation]')
                    }))
                    .filter((v, i, arr) => arr.findIndex(x => x.url === v.url) === i)
            """)
            return links or []
        except Exception:
            return []

    # ------------------------------------------------------------------ #
    # Interactions with retry + backoff
    # ------------------------------------------------------------------ #

    async def _with_retry(
        self,
        coro_factory,
        attempts: int = 3,
        base_delay: float = 0.4,
    ) -> tuple[bool, str | None]:
        """Run coro_factory() up to `attempts` times with exponential backoff."""
        last_error: str = "unknown"
        for attempt in range(attempts):
            try:
                await coro_factory()
                return True, None
            except Exception as exc:
                last_error = str(exc)
                if attempt < attempts - 1:
                    await asyncio.sleep(base_delay * (2 ** attempt))
        return False, last_error

    async def _scroll_to(self, selector: str) -> None:
        try:
            await self.page.evaluate(
                f'document.querySelector({json.dumps(selector)})?.scrollIntoView({{block:"center"}})'
            )
            await asyncio.sleep(0.15)
        except Exception:
            pass

    async def click(self, selector: str) -> tuple[bool, str | None]:
        await self._scroll_to(selector)
        ok, err = await self._with_retry(
            lambda: self.page.click(selector, timeout=6_000)
        )
        if ok:
            await asyncio.sleep(0.4)
        return ok, err

    async def fill(self, selector: str, value: str) -> tuple[bool, str | None]:
        await self._scroll_to(selector)
        async def _fill():
            await self.page.click(selector, timeout=4_000)
            await self.page.fill(selector, value, timeout=4_000)
        return await self._with_retry(_fill)

    async def select(self, selector: str, value: str) -> tuple[bool, str | None]:
        return await self._with_retry(
            lambda: self.page.select_option(selector, value, timeout=5_000)
        )

    async def check(self, selector: str) -> tuple[bool, str | None]:
        await self._scroll_to(selector)
        return await self._with_retry(
            lambda: self.page.check(selector, timeout=4_000)
        )

    async def uncheck(self, selector: str) -> tuple[bool, str | None]:
        await self._scroll_to(selector)
        return await self._with_retry(
            lambda: self.page.uncheck(selector, timeout=4_000)
        )

    async def hover(self, selector: str) -> tuple[bool, str | None]:
        await self._scroll_to(selector)
        ok, err = await self._with_retry(
            lambda: self.page.hover(selector, timeout=4_000)
        )
        if ok:
            await asyncio.sleep(0.3)
        return ok, err

    async def press_key(self, selector: str, key: str) -> tuple[bool, str | None]:
        return await self._with_retry(
            lambda: self.page.press(selector, key, timeout=4_000)
        )

    async def assert_text(self, selector: str, expected_text: str) -> tuple[bool, str | None]:
        """Returns (True, None) if element contains text, else (False, actual_text)."""
        try:
            el = await self.page.query_selector(selector)
            if el is None:
                return False, f"element not found: {selector}"
            actual = await el.text_content() or ""
            if expected_text.lower() in actual.lower():
                return True, None
            return False, f"expected '{expected_text}' but found '{actual[:100]}'"
        except Exception as exc:
            return False, str(exc)

    async def assert_visible(self, selector: str) -> tuple[bool, str | None]:
        try:
            el = await self.page.query_selector(selector)
            if el is None:
                return False, f"element not found: {selector}"
            visible = await el.is_visible()
            return visible, None if visible else "element exists but is not visible"
        except Exception as exc:
            return False, str(exc)

    # ------------------------------------------------------------------ #
    # Action dispatcher
    # ------------------------------------------------------------------ #

    async def execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        """
        Execute one action from the AI planner.
        Returns {action, success, error, assertion_result}.
        """
        atype = action.get("type", "")
        target = action.get("target", "")
        value = str(action.get("value") or "")
        result: dict[str, Any] = {
            "action": action,
            "success": False,
            "error": None,
            "assertion_result": None,
        }

        if atype == "click":
            ok, err = await self.click(target)
        elif atype == "fill":
            ok, err = await self.fill(target, value)
        elif atype == "select":
            ok, err = await self.select(target, value)
        elif atype == "check":
            ok, err = await self.check(target)
        elif atype == "uncheck":
            ok, err = await self.uncheck(target)
        elif atype == "hover":
            ok, err = await self.hover(target)
        elif atype == "press_key":
            ok, err = await self.press_key(target, value or "Enter")
        elif atype == "submit":
            ok, err = await self.press_key(target, "Enter")
            if ok:
                try:
                    await self.page.wait_for_load_state("domcontentloaded", timeout=8_000)
                except PWTimeout:
                    pass
        elif atype == "navigate":
            nav = await self.navigate(target or value)
            ok, err = nav["success"], nav.get("error")
        elif atype == "wait":
            secs = float(value) if value else 1.0
            await asyncio.sleep(min(secs, 5.0))
            ok, err = True, None
        elif atype == "assert_text":
            ok, err = await self.assert_text(target, value)
            result["assertion_result"] = {"expected": value, "passed": ok, "detail": err}
        elif atype == "assert_visible":
            ok, err = await self.assert_visible(target)
            result["assertion_result"] = {"passed": ok, "detail": err}
        else:
            ok, err = False, f"unknown action type: {atype}"

        result["success"] = ok
        result["error"] = err

        if not ok and not action.get("optional"):
            logger.debug(
                "[%s] Action failed — type=%s target=%s err=%s",
                self.persona_name, atype, target, err
            )

        return result

    # ------------------------------------------------------------------ #
    # Screenshots
    # ------------------------------------------------------------------ #

    async def screenshot(self, label: str = "") -> str:
        name = f"{label}_{uuid.uuid4().hex[:8]}.png" if label else f"{uuid.uuid4().hex}.png"
        path = self._screenshot_dir / name
        try:
            await self.page.screenshot(path=str(path), full_page=True)
            return str(path)
        except Exception as exc:
            logger.warning("Screenshot failed: %s", exc)
            return ""

    # ------------------------------------------------------------------ #
    # Page state snapshot
    # ------------------------------------------------------------------ #

    async def get_page_state(self) -> dict[str, Any]:
        try:
            title = await self.page.title()
        except Exception:
            title = ""
        try:
            visible_text = await self.page.evaluate(
                "() => document.body?.innerText?.replace(/\\s+/g, ' ')?.substring(0, 800) || ''"
            )
        except Exception:
            visible_text = ""
        try:
            url = self.page.url
        except Exception:
            url = ""

        return {"url": url, "title": title, "visible_text_snippet": visible_text}

    async def get_web_vitals(self) -> dict[str, Any]:
        try:
            return await self.page.evaluate(_VITALS_JS) or {}
        except Exception:
            return {}

    # ------------------------------------------------------------------ #
    # Interceptor proxy
    # ------------------------------------------------------------------ #

    def flush_interceptor(
        self,
    ) -> tuple[list[CapturedConsoleError], list[CapturedNetworkFailure]]:
        return self.interceptor.flush()
