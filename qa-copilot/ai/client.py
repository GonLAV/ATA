"""
AI client — wraps Anthropic SDK with:
  • JSON parse with fence-stripping
  • Prompt caching (ephemeral system block)
  • Token usage tracking per session
  • Retry on transient API errors (429, 529)
  • stream_reason for long analyses
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import Any

import anthropic

from config import settings
from ai.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_client: anthropic.AsyncAnthropic | None = None

# session_id → {"input": int, "output": int}
_token_usage: dict[str, dict[str, int]] = defaultdict(lambda: {"input": 0, "output": 0})


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


def get_token_usage(session_id: str) -> dict[str, int]:
    return dict(_token_usage[session_id])


def reset_token_usage(session_id: str) -> None:
    _token_usage.pop(session_id, None)


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


async def reason(
    prompt: str,
    *,
    system: str = SYSTEM_PROMPT,
    max_tokens: int = 2048,
    use_cache: bool = True,
    session_id: str | None = None,
    retries: int = 3,
) -> dict[str, Any]:
    """Call the model and return parsed JSON. Retries on rate-limit."""
    client = get_client()
    system_blocks: list[dict] = [{"type": "text", "text": system}]
    if use_cache:
        system_blocks[0]["cache_control"] = {"type": "ephemeral"}

    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            response = await client.messages.create(
                model=settings.model,
                max_tokens=max_tokens,
                system=system_blocks,
                messages=[{"role": "user", "content": prompt}],
            )
        except (anthropic.RateLimitError, anthropic.InternalServerError) as exc:
            wait = 2 ** attempt
            logger.warning("API transient error (attempt %d/%d), retrying in %ds: %s", attempt + 1, retries, wait, exc)
            await asyncio.sleep(wait)
            last_exc = exc
            continue
        except anthropic.APIError as exc:
            logger.error("API error: %s", exc)
            raise

        # Track tokens
        if session_id and hasattr(response, "usage"):
            u = response.usage
            _token_usage[session_id]["input"]  += getattr(u, "input_tokens", 0)
            _token_usage[session_id]["output"] += getattr(u, "output_tokens", 0)

        raw = response.content[0].text
        try:
            return json.loads(_strip_fences(raw))
        except json.JSONDecodeError:
            logger.warning("Non-JSON response (attempt %d): %.120s", attempt + 1, raw)
            if attempt < retries - 1:
                continue
            return {}

    if last_exc:
        raise last_exc
    return {}


async def stream_reason(
    prompt: str,
    *,
    system: str = SYSTEM_PROMPT,
    max_tokens: int = 4096,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Stream the response — better for long reports."""
    client = get_client()
    chunks: list[str] = []

    async with client.messages.stream(
        model=settings.model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        async for text in stream.text_stream:
            chunks.append(text)

        # Token usage from final message
        if session_id:
            try:
                final = await stream.get_final_message()
                if hasattr(final, "usage"):
                    u = final.usage
                    _token_usage[session_id]["input"]  += getattr(u, "input_tokens", 0)
                    _token_usage[session_id]["output"] += getattr(u, "output_tokens", 0)
            except Exception:
                pass

    raw = "".join(chunks)
    try:
        return json.loads(_strip_fences(raw))
    except json.JSONDecodeError:
        logger.error("stream_reason: non-JSON response")
        return {}
