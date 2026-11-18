"""Decide whether a Message Center item matters to Client Engineering."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .planner import PlannerItem


@dataclass
class Verdict:
    relevant: bool
    reason: str
    source: str  # "rules" or "llm"


def _matches(terms: list[str], text: str) -> list[str]:
    return [t for t in terms if re.search(rf"(?<!\w){re.escape(t)}(?!\w)", text, re.IGNORECASE)]


def rule_verdict(item: PlannerItem, include: list[str], exclude: list[str]) -> Verdict | None:
    """Returns a verdict when the rules are sure, or None when it's a judgement call."""
    text = item.searchable_text
    hits = _matches(include, text)
    if hits:
        return Verdict(True, "matches " + ", ".join(hits[:5]), "rules")
    misses = _matches(exclude, text)
    if misses:
        return Verdict(False, "out of scope: " + ", ".join(misses[:5]), "rules")
    return None


_SCHEMA = {
    "type": "object",
    "properties": {
        "relevant": {"type": "boolean"},
        "reason": {"type": "string", "description": "One sentence, why or why not."},
    },
    "required": ["relevant", "reason"],
    "additionalProperties": False,
}


class LlmClassifier:
    def __init__(self, model: str, team_description: str):
        import anthropic  # only needed in rules+llm mode

        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self.model = model
        self.system = (
            "You triage Microsoft 365 Message Center posts for an IT team.\n"
            f"Team scope: {team_description.strip()}\n"
            "Decide whether the team needs to know about or act on the post. "
            "Mark it relevant if it changes anything on end-user devices or client apps, "
            "or needs a client-side policy, package or communication."
        )

    def classify(self, item: PlannerItem) -> Verdict:
        post = f"Title: {item.title}\nBucket: {item.bucket}\nLabels: {', '.join(item.labels)}\n\n{item.description[:20000]}"
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=1024,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": _SCHEMA}},
            system=self.system,
            messages=[{"role": "user", "content": post}],
        )
        if response.stop_reason == "refusal":
            # Don't silently drop it; let a human look.
            return Verdict(True, "classifier declined; needs a human look", "llm")
        text = next(b.text for b in response.content if b.type == "text")
        data = json.loads(text)
        return Verdict(bool(data["relevant"]), data["reason"], "llm")


def decide(item: PlannerItem, cfg: dict, llm: LlmClassifier | None) -> Verdict:
    v = rule_verdict(item, cfg.get("include", []), cfg.get("exclude", []))
    if v:
        return v
    if llm:
        return llm.classify(item)
    return Verdict(False, "no client engineering keywords", "rules")
