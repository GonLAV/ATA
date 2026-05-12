"""External bug-filing integrations: GitHub Issues, Jira, Linear (n8n: integrations)."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from db.models import Bug, Integration

logger = logging.getLogger(__name__)


async def file_bug_to_integration(
    integ: Integration, bug: Bug
) -> tuple[str, str]:
    """Returns (ticket_url, ticket_id)."""
    if integ.integ_type == "github":
        return await _file_github_issue(integ.config, bug)
    if integ.integ_type == "jira":
        return await _file_jira_ticket(integ.config, bug)
    if integ.integ_type == "linear":
        return await _file_linear_issue(integ.config, bug)
    raise ValueError(f"Unknown integration type: {integ.integ_type}")


async def test_integration_connection(integ: Integration) -> tuple[bool, str]:
    try:
        if integ.integ_type == "github":
            return await _test_github(integ.config)
        if integ.integ_type == "jira":
            return await _test_jira(integ.config)
        if integ.integ_type == "linear":
            return await _test_linear(integ.config)
        return False, "Unknown integration type"
    except Exception as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# GitHub Issues
# ---------------------------------------------------------------------------

async def _file_github_issue(config: dict, bug: Bug) -> tuple[str, str]:
    token = config["token"]
    repo  = config["repo"]          # "owner/repo"
    label = config.get("label", "qa-copilot")

    severity_label = f"severity:{bug.severity.value if hasattr(bug.severity,'value') else bug.severity}"
    steps = "\n".join(f"{i+1}. {s}" for i, s in enumerate(bug.reproduction_steps or []))

    body = f"""## Bug Report — QA Copilot

**Severity:** {(bug.severity.value if hasattr(bug.severity,'value') else bug.severity).upper()}
**URL:** {bug.url_at_error or 'N/A'}
**Persona:** {bug.persona_name or 'N/A'}
**Detected by Vision:** {'Yes' if bug.detected_by_vision else 'No'}

### Description
{bug.description}

### Reproduction Steps
{steps or 'No steps recorded'}

### Expected Behavior
{bug.expected_behavior}

### Actual Behavior
{bug.actual_behavior}

---
*Filed automatically by [QA Copilot](https://github.com/GonLAV/ATA)*
"""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"https://api.github.com/repos/{repo}/issues",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={
                "title": f"[QA] {bug.title}",
                "body": body,
                "labels": [label, severity_label],
            },
        )
        resp.raise_for_status()
        data = resp.json()
    return data["html_url"], str(data["number"])


async def _test_github(config: dict) -> tuple[bool, str]:
    token = config.get("token", "")
    repo  = config.get("repo", "")
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"https://api.github.com/repos/{repo}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
        if resp.status_code == 200:
            return True, f"Connected to {repo}"
        return False, f"GitHub returned {resp.status_code}: {resp.text[:200]}"


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------

async def _file_jira_ticket(config: dict, bug: Bug) -> tuple[str, str]:
    base_url  = config["base_url"].rstrip("/")
    email     = config["email"]
    api_token = config["api_token"]
    project   = config["project_key"]

    sev = (bug.severity.value if hasattr(bug.severity, "value") else bug.severity).upper()
    steps = "\n".join(f"# {i+1}. {s}" for i, s in enumerate(bug.reproduction_steps or []))

    description = {
        "type": "doc", "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": bug.description}]}],
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{base_url}/rest/api/3/issue",
            auth=(email, api_token),
            json={
                "fields": {
                    "project":     {"key": project},
                    "summary":     f"[QA-{sev}] {bug.title}",
                    "description": description,
                    "issuetype":   {"name": "Bug"},
                    "priority":    {"name": {"critical": "Highest", "high": "High", "medium": "Medium", "low": "Low"}.get(
                        (bug.severity.value if hasattr(bug.severity,"value") else str(bug.severity)).lower(), "Medium"
                    )},
                }
            },
        )
        resp.raise_for_status()
        data = resp.json()

    issue_key = data["key"]
    ticket_url = f"{base_url}/browse/{issue_key}"
    return ticket_url, issue_key


async def _test_jira(config: dict) -> tuple[bool, str]:
    base_url  = config.get("base_url", "").rstrip("/")
    email     = config.get("email", "")
    api_token = config.get("api_token", "")
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{base_url}/rest/api/3/myself", auth=(email, api_token))
        if resp.status_code == 200:
            name = resp.json().get("displayName", "Unknown")
            return True, f"Connected as {name}"
        return False, f"Jira returned {resp.status_code}"


# ---------------------------------------------------------------------------
# Linear
# ---------------------------------------------------------------------------

async def _file_linear_issue(config: dict, bug: Bug) -> tuple[str, str]:
    api_key    = config["api_key"]
    team_id    = config["team_id"]

    sev = (bug.severity.value if hasattr(bug.severity, "value") else bug.severity).lower()
    priority_map = {"critical": 1, "high": 2, "medium": 3, "low": 4}

    query = """
    mutation CreateIssue($title: String!, $description: String!, $teamId: String!, $priority: Int) {
      issueCreate(input: {title: $title, description: $description, teamId: $teamId, priority: $priority}) {
        success issue { id url }
      }
    }
    """
    variables = {
        "title":       f"[QA] {bug.title}",
        "description": f"{bug.description}\n\n**URL:** {bug.url_at_error or 'N/A'}\n**Persona:** {bug.persona_name or 'N/A'}",
        "teamId":      team_id,
        "priority":    priority_map.get(sev, 3),
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://api.linear.app/graphql",
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            json={"query": query, "variables": variables},
        )
        resp.raise_for_status()
        data = resp.json()

    issue = data["data"]["issueCreate"]["issue"]
    return issue["url"], issue["id"]


async def _test_linear(config: dict) -> tuple[bool, str]:
    api_key = config.get("api_key", "")
    query   = "{ viewer { id name } }"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://api.linear.app/graphql",
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            json={"query": query},
        )
        if resp.status_code == 200:
            name = resp.json().get("data", {}).get("viewer", {}).get("name", "Unknown")
            return True, f"Connected as {name}"
        return False, f"Linear returned {resp.status_code}"
