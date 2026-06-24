"""
Persona Cognitive Engine — QA Copilot's groundbreaking feature.

Instead of a single anonymous test session, the engine runs three behaviorally
distinct user personas in parallel. Each persona has its own psychology, risk
appetite, and interaction style, revealing class-specific UX failures invisible
to uniform testing.

Personas:
  Explorer   — Meticulous, reads everything, clicks every option, patient
  Skeptic    — Adversarial, enters bad data, skips steps, probes security
  Rusher     — Impatient, skips tutorials, fast-clicks, mobile mindset

The engine also maintains a Cognitive Navigation Graph (networkx DiGraph) per
persona, recording which page→page transitions were discovered and which were
blocked. Cross-persona diff analysis flags divergent behavior as high-severity
UX anomalies.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import networkx as nx

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Persona definitions
# ------------------------------------------------------------------ #

PERSONAS: list[dict[str, Any]] = [
    {
        "name": "Explorer",
        "description": (
            "A thorough, methodical user who reads all labels, "
            "tries every option, and follows the happy path carefully."
        ),
        "style": "Patient, detail-oriented, reads all copy, tries all UI states.",
        "test_focus": (
            "navigation completeness, help text accuracy, "
            "all form fields, success states, empty states"
        ),
        "exploration_priority": (
            "Visit every link. Prefer unvisited pages. "
            "Fill all optional fields. Test success flows."
        ),
        "interaction_delay_ms": 800,
        "input_strategy": {
            "text": "valid_realistic",
            "email": "user@example.com",
            "password": "SecurePass123!",
            "number": "42",
            "url": "https://example.com",
        },
        "skip_optional_fields": False,
        "try_edge_inputs": False,
    },
    {
        "name": "Skeptic",
        "description": (
            "An adversarial power user who probes for weaknesses, "
            "enters bad data, and tries to break the app."
        ),
        "style": "Adversarial, enters invalid/malicious input, skips steps, tests error paths.",
        "test_focus": (
            "error messages, validation feedback, SQL-injection probes, "
            "XSS payloads, empty submissions, boundary values"
        ),
        "exploration_priority": (
            "Prioritize forms and auth flows. "
            "Try submitting empty forms. Enter max-length strings. "
            "Try special characters. Test error recovery paths."
        ),
        "interaction_delay_ms": 200,
        "input_strategy": {
            "text": "edge_case",
            "email": "not-an-email",
            "password": "",
            "number": "-999999",
            "url": "javascript:alert(1)",
        },
        "skip_optional_fields": True,
        "try_edge_inputs": True,
        "edge_inputs": [
            "",
            " ",
            "a" * 256,
            "<script>alert('xss')</script>",
            "'; DROP TABLE users; --",
            "null",
            "undefined",
            "0",
            "-1",
        ],
    },
    {
        "name": "Rusher",
        "description": (
            "An impatient mobile user who skips tutorials, double-clicks, "
            "abandons flows mid-way, and expects instant feedback."
        ),
        "style": "Fast, impatient, skips instructions, partial form fills, quick navigation.",
        "test_focus": (
            "loading performance, error recovery after abandonment, "
            "back-button behavior, double-submission prevention, "
            "partial form submission handling"
        ),
        "exploration_priority": (
            "Follow the most prominent CTA on each page. "
            "Skip secondary links. Test back-navigation. "
            "Try submitting forms without all required fields."
        ),
        "interaction_delay_ms": 50,
        "input_strategy": {
            "text": "minimal",
            "email": "a@b.co",
            "password": "pass",
            "number": "1",
            "url": "http://x.co",
        },
        "skip_optional_fields": True,
        "try_edge_inputs": False,
    },
]


# ------------------------------------------------------------------ #
# Navigation graph
# ------------------------------------------------------------------ #

@dataclass
class CognitiveNavigationGraph:
    """Records page transitions discovered by a persona."""

    persona_name: str
    graph: nx.DiGraph = field(default_factory=nx.DiGraph)

    def record_visit(self, url: str, title: str = "", page_type: str = "") -> None:
        if url not in self.graph:
            self.graph.add_node(url, title=title, page_type=page_type, visits=0)
        self.graph.nodes[url]["visits"] += 1

    def record_transition(self, from_url: str, to_url: str, action: str = "") -> None:
        self.graph.add_edge(from_url, to_url, action=action)

    def record_blocked(self, from_url: str, target: str, reason: str) -> None:
        self.graph.add_edge(
            from_url,
            f"BLOCKED:{target[:60]}",
            action=f"blocked:{reason}",
        )

    def visited_urls(self) -> list[str]:
        return [n for n in self.graph.nodes if not n.startswith("BLOCKED:")]

    def blocked_transitions(self) -> list[dict]:
        return [
            {"from": u, "to": v, "reason": d.get("action", "")}
            for u, v, d in self.graph.edges(data=True)
            if v.startswith("BLOCKED:")
        ]

    def summary(self) -> dict:
        return {
            "pages_reachable": len(self.visited_urls()),
            "transitions": self.graph.number_of_edges(),
            "blocked": len(self.blocked_transitions()),
            "most_visited": sorted(
                self.visited_urls(),
                key=lambda n: self.graph.nodes[n].get("visits", 0),
                reverse=True,
            )[:5],
        }


# ------------------------------------------------------------------ #
# Cross-persona divergence analysis
# ------------------------------------------------------------------ #

@dataclass
class PersonaDivergence:
    """A page/flow reachable by some personas but not others."""

    url: str
    reachable_by: list[str]
    unreachable_by: list[str]
    severity: str  # medium | high
    implication: str


def analyze_divergence(
    graphs: dict[str, CognitiveNavigationGraph],
) -> list[PersonaDivergence]:
    """
    Compare navigation graphs across personas.
    Pages reachable by Explorer but not Rusher → potential flow gate.
    Pages reachable by Skeptic but not Explorer → possible security exposure.
    """
    all_urls: set[str] = set()
    for g in graphs.values():
        all_urls.update(g.visited_urls())

    divergences: list[PersonaDivergence] = []

    for url in all_urls:
        reachable = [name for name, g in graphs.items() if url in g.visited_urls()]
        unreachable = [name for name, g in graphs.items() if url not in g.visited_urls()]

        if not unreachable:
            continue

        # Skeptic reached a page others didn't → possible unguarded route
        if "Skeptic" in reachable and len(unreachable) >= 2:
            divergences.append(
                PersonaDivergence(
                    url=url,
                    reachable_by=reachable,
                    unreachable_by=unreachable,
                    severity="high",
                    implication=(
                        "Page only reachable via adversarial inputs — "
                        "possible unintended route or missing access control."
                    ),
                )
            )
        # Explorer reached a page Rusher couldn't → potential UX gate / missing CTA
        elif "Explorer" in reachable and "Rusher" in unreachable:
            divergences.append(
                PersonaDivergence(
                    url=url,
                    reachable_by=reachable,
                    unreachable_by=unreachable,
                    severity="medium",
                    implication=(
                        "Impatient users cannot reach this page — "
                        "CTA may be hidden, labeled poorly, or buried too deep."
                    ),
                )
            )

    return divergences


# ------------------------------------------------------------------ #
# Persona manager
# ------------------------------------------------------------------ #

class PersonaEngine:
    def __init__(self) -> None:
        self.personas = PERSONAS
        self.graphs: dict[str, CognitiveNavigationGraph] = {
            p["name"]: CognitiveNavigationGraph(persona_name=p["name"])
            for p in PERSONAS
        }

    def get_persona(self, name: str) -> dict[str, Any]:
        for p in self.personas:
            if p["name"] == name:
                return p
        raise ValueError(f"Unknown persona: {name}")

    def get_graph(self, persona_name: str) -> CognitiveNavigationGraph:
        return self.graphs[persona_name]

    def get_input_value(self, persona: dict, input_type: str) -> str:
        strategy = persona.get("input_strategy", {})
        if persona.get("try_edge_inputs") and input_type == "text":
            edges = persona.get("edge_inputs", [""])
            import random
            return random.choice(edges)
        return strategy.get(input_type, strategy.get("text", "test"))

    def cross_persona_report(self) -> dict:
        divergences = analyze_divergence(self.graphs)
        return {
            "persona_graphs": {
                name: g.summary() for name, g in self.graphs.items()
            },
            "divergences": [
                {
                    "url": d.url,
                    "reachable_by": d.reachable_by,
                    "unreachable_by": d.unreachable_by,
                    "severity": d.severity,
                    "implication": d.implication,
                }
                for d in divergences
            ],
            "total_divergences": len(divergences),
            "high_severity_divergences": sum(
                1 for d in divergences if d.severity == "high"
            ),
        }
