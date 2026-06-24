"""
Claude Vision screenshot analyser.

Sends a full-page screenshot to claude-sonnet-4-6's vision capability
and asks it to identify visual bugs that pure DOM analysis misses:

  • Overlapping / clipped UI elements
  • Blank / white-flash sections
  • Broken image placeholders
  • Misaligned layouts (e.g. text overflowing buttons)
  • Empty data tables or lists that should have content
  • Colour contrast failures (obvious ones)
  • Spinner/loader left stuck on screen
  • Modal z-index issues (content hidden behind overlay)

Returns a list of VisionIssue objects that get converted to bug candidates
by the explorer.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from pathlib import Path

import anthropic

from config import settings

logger = logging.getLogger(__name__)

_VISION_SYSTEM = """You are a visual QA engineer reviewing a browser screenshot.
Identify only real, visible UI problems — not missing features or content choices.
Focus on rendering bugs, layout failures, and visual glitches.
Be specific: describe exactly where on the screen the problem is.
Output ONLY valid JSON."""

_VISION_PROMPT = """Analyse this screenshot for visual UI bugs.

Look specifically for:
- Overlapping, clipped, or obscured elements
- Buttons or inputs with broken/missing labels
- Images that failed to load (broken placeholder icons)
- Layout sections that are blank or unexpectedly empty
- Text overflowing its container
- Spinner or loading indicators stuck on screen
- Modal or overlay hiding content incorrectly
- Obvious colour contrast failures (white text on white background, etc.)
- Misaligned navigation or footer elements
- Console/error banners visible on the page

Return JSON:
{
  "issues": [
    {
      "title": "<short bug title>",
      "severity": "<low|medium|high|critical>",
      "description": "<precise description with screen location>",
      "element_description": "<what element is affected>",
      "expected": "<what it should look like>",
      "actual": "<what you see>"
    }
  ],
  "overall_visual_quality": "<good|degraded|broken>",
  "summary": "<one sentence>"
}

If there are no visual issues, return {"issues": [], "overall_visual_quality": "good", "summary": "No visual issues detected."}"""


@dataclass
class VisionIssue:
    title: str
    severity: str
    description: str
    element_description: str
    expected: str
    actual: str


async def analyse_screenshot(
    screenshot_path: str,
    page_url: str,
    persona_name: str,
) -> list[VisionIssue]:
    """
    Send a screenshot to Claude Vision and return detected visual issues.
    Returns [] if vision is disabled, the file doesn't exist, or the call fails.
    """
    if not settings.vision_enabled:
        return []

    path = Path(screenshot_path)
    if not path.exists() or path.stat().st_size == 0:
        return []

    try:
        image_data = base64.standard_b64encode(path.read_bytes()).decode()
        media_type = "image/png"
    except Exception as exc:
        logger.warning("Vision: cannot read screenshot %s: %s", screenshot_path, exc)
        return []

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        response = await client.messages.create(
            model=settings.vision_model,
            max_tokens=1024,
            system=_VISION_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_data,
                            },
                        },
                        {"type": "text", "text": _VISION_PROMPT},
                    ],
                }
            ],
        )
    except anthropic.APIError as exc:
        logger.error("Vision API error on %s: %s", page_url, exc)
        return []

    import json
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Vision returned non-JSON for %s", page_url)
        return []

    issues: list[VisionIssue] = []
    for item in result.get("issues", []):
        if item.get("severity") in ("high", "critical", "medium"):
            issues.append(
                VisionIssue(
                    title=item.get("title", "Visual UI bug"),
                    severity=item.get("severity", "medium"),
                    description=item.get("description", ""),
                    element_description=item.get("element_description", ""),
                    expected=item.get("expected", ""),
                    actual=item.get("actual", ""),
                )
            )

    if issues:
        logger.info(
            "[%s] Vision found %d visual issue(s) on %s",
            persona_name, len(issues), page_url
        )

    return issues
