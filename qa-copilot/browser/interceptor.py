"""Captures console messages and network failures from a Playwright page."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from playwright.async_api import Page, ConsoleMessage, Request, Response

logger = logging.getLogger(__name__)


@dataclass
class CapturedConsoleError:
    error_type: str
    message: str
    stack_trace: str | None
    url: str
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class CapturedNetworkFailure:
    request_url: str
    method: str
    status_code: int | None
    error_text: str | None
    timestamp: datetime = field(default_factory=datetime.utcnow)


class PageInterceptor:
    """Attaches to a Playwright page and records errors + network failures."""

    def __init__(self) -> None:
        self.console_errors: list[CapturedConsoleError] = []
        self.network_failures: list[CapturedNetworkFailure] = []
        self._page: Page | None = None

    def attach(self, page: Page) -> None:
        self._page = page
        page.on("console", self._on_console)
        page.on("requestfailed", self._on_request_failed)
        page.on("response", self._on_response)

    def _on_console(self, msg: ConsoleMessage) -> None:
        if msg.type in ("error", "warning"):
            self.console_errors.append(
                CapturedConsoleError(
                    error_type=msg.type,
                    message=msg.text,
                    stack_trace=None,
                    url=self._page.url if self._page else "",
                )
            )
            logger.debug("Console %s: %s", msg.type, msg.text[:120])

    def _on_request_failed(self, request: Request) -> None:
        self.network_failures.append(
            CapturedNetworkFailure(
                request_url=request.url,
                method=request.method,
                status_code=None,
                error_text=request.failure,
            )
        )
        logger.debug("Request failed: %s %s", request.method, request.url)

    async def _on_response(self, response: Response) -> None:
        if response.status >= 400:
            # Only record API/XHR failures, skip asset failures to reduce noise
            content_type = response.headers.get("content-type", "")
            if "html" not in content_type:
                self.network_failures.append(
                    CapturedNetworkFailure(
                        request_url=response.url,
                        method=response.request.method,
                        status_code=response.status,
                        error_text=f"HTTP {response.status}",
                    )
                )

    def flush(self) -> tuple[list[CapturedConsoleError], list[CapturedNetworkFailure]]:
        """Return and clear captured events."""
        errors = self.console_errors.copy()
        failures = self.network_failures.copy()
        self.console_errors.clear()
        self.network_failures.clear()
        return errors, failures
