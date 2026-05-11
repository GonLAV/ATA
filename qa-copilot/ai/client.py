import json
import logging
from typing import Any
import anthropic
from config import settings
from ai.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_client: anthropic.AsyncAnthropic | None = None


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client


async def reason(
    prompt: str,
    *,
    system: str = SYSTEM_PROMPT,
    max_tokens: int = 2048,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Send a prompt and parse the JSON response."""
    client = get_client()

    system_blocks: list[dict] = [{"type": "text", "text": system}]
    if use_cache:
        system_blocks[0]["cache_control"] = {"type": "ephemeral"}

    try:
        response = await client.messages.create(
            model=settings.model,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("LLM returned non-JSON: %s", exc)
        return {}
    except anthropic.APIError as exc:
        logger.error("Anthropic API error: %s", exc)
        raise


async def stream_reason(
    prompt: str,
    *,
    system: str = SYSTEM_PROMPT,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """Stream response for longer analyses."""
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

    raw = "".join(chunks).strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Stream returned non-JSON")
        return {}
