"""Entry point: python -m sync.main [--config config.yaml] [--apply]

Runs as a dry run unless --apply is passed (or DRY_RUN=false), so nothing is
created in Jira until you've checked what it would do.
"""
from __future__ import annotations

import argparse
import os
import sys

import yaml

from .jira import JiraClient, build_description, dedup_label
from .planner import PlannerClient, get_token
from .relevance import LlmClassifier, decide


def env(name: str, required: bool = True) -> str:
    v = os.environ.get(name, "")
    if required and not v:
        sys.exit(f"Missing environment variable {name}")
    return v


def run(cfg: dict, apply: bool, planner: PlannerClient, jira: JiraClient | None,
        llm: LlmClassifier | None, out=print) -> dict:
    p, j, rel = cfg["planner"], cfg["jira"], cfg["relevance"]
    items = planner.fetch_items(p["plan_id"], p.get("lookback_days", 14), p.get("skip_completed", True))
    stats = {"seen": len(items), "relevant": 0, "created": 0, "existing": 0, "skipped": 0}
    rows = []
    for item in sorted(items, key=lambda i: i.created):
        verdict = decide(item, rel, llm)
        if not verdict.relevant:
            stats["skipped"] += 1
            rows.append(("skip", item, verdict.reason, ""))
            continue
        stats["relevant"] += 1
        label = dedup_label(item)
        found = jira.existing_keys(j["project_key"], label) if jira else []
        if found:
            stats["existing"] += 1
            rows.append(("exists", item, verdict.reason, found[0]))
            continue
        key = ""
        if apply:
            key = jira.create_issue(
                j["project_key"], j.get("issue_type", "Task"),
                f"[{item.mc_id}] {item.title}" if item.mc_id and item.mc_id not in item.title else item.title,
                build_description(item, verdict.reason),
                [*j.get("labels", []), label],
            )
            stats["created"] += 1
        rows.append(("create" if apply else "would create", item, verdict.reason, key))

    out(f"## Planner -> Jira ({'applied' if apply else 'dry run'})\n")
    out("| Action | MC ID | Title | Why | Jira |\n|---|---|---|---|---|")
    for action, item, reason, key in rows:
        title = item.title.replace("|", "/")[:90]
        out(f"| {action} | {item.mc_id or '-'} | {title} | {reason.replace('|', '/')} | {key} |")
    out(f"\n{stats}")
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--apply", action="store_true", help="actually create Jira issues")
    args = ap.parse_args()
    apply = args.apply or os.environ.get("DRY_RUN", "true").lower() == "false"

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    p, j, rel = cfg["planner"], cfg["jira"], cfg["relevance"]
    token = get_token(p["tenant_id"], env("AZURE_CLIENT_ID"), env("AZURE_CLIENT_SECRET"))
    planner = PlannerClient(token)
    jira = JiraClient(
        j["base_url"], j.get("deployment", "cloud"),
        email=env("JIRA_EMAIL", required=j.get("deployment", "cloud") == "cloud"),
        token=env("JIRA_API_TOKEN"),
    )
    llm = LlmClassifier(rel.get("llm_model", "claude-opus-5-5"), rel.get("team_description", "")) \
        if rel.get("mode") == "rules+llm" else None

    lines: list[str] = []
    run(cfg, apply, planner, jira, llm, out=lambda s: (print(s), lines.append(s)))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
