"""Minimal Jira client that works with Jira Cloud and Jira Data Center."""
from __future__ import annotations

import requests

from .planner import PlannerItem


class JiraClient:
    def __init__(self, base_url: str, deployment: str, *, email: str = "", token: str,
                 session: requests.Session | None = None):
        self.base = base_url.rstrip("/")
        self.cloud = deployment == "cloud"
        self.s = session or requests.Session()
        self.s.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
        if self.cloud:
            self.s.auth = (email, token)  # Atlassian account email + API token
        else:
            self.s.headers["Authorization"] = f"Bearer {token}"  # Data Center PAT

    def existing_keys(self, project: str, label: str) -> list[str]:
        jql = f'project = "{project}" AND labels = "{label}"'
        if self.cloud:
            r = self.s.post(f"{self.base}/rest/api/3/search/jql",
                            json={"jql": jql, "fields": ["key"], "maxResults": 5}, timeout=30)
        else:
            r = self.s.get(f"{self.base}/rest/api/2/search",
                           params={"jql": jql, "fields": "key", "maxResults": 5}, timeout=30)
        r.raise_for_status()
        return [i["key"] for i in r.json().get("issues", [])]

    def create_issue(self, project: str, issue_type: str, summary: str, description: str,
                     labels: list[str]) -> str:
        # API v2 takes a plain/wiki-markup description on both Cloud and Data Center.
        r = self.s.post(f"{self.base}/rest/api/2/issue", json={"fields": {
            "project": {"key": project},
            "issuetype": {"name": issue_type},
            "summary": summary[:250],
            "description": description,
            "labels": labels,
        }}, timeout=30)
        if not r.ok:
            raise RuntimeError(f"Jira create failed ({r.status_code}): {r.text[:500]}")
        return r.json()["key"]


def dedup_label(item: PlannerItem) -> str:
    return "mc-" + item.dedup_key


def build_description(item: PlannerItem, reason: str) -> str:
    links = "\n".join(f"* {u}" for u in item.links) or "* (none)"
    return (
        f"Imported automatically from the M365 Message Center Planner plan.\n\n"
        f"*Message Center ID:* {item.mc_id or 'n/a'}\n"
        f"*Planner bucket:* {item.bucket or 'n/a'}\n"
        f"*Planner labels:* {', '.join(item.labels) or 'none'}\n"
        f"*Why it was picked up:* {reason}\n\n"
        f"*Links:*\n{links}\n\n----\n{item.description[:30000]}"
    )
