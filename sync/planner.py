"""Read Message Center tasks from a Microsoft Planner plan via Microsoft Graph."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

import requests

GRAPH = "https://graph.microsoft.com/v1.0"
MC_ID = re.compile(r"\bMC\d{5,8}\b")


@dataclass
class PlannerItem:
    task_id: str
    title: str
    created: datetime
    bucket: str
    labels: list[str]
    description: str
    links: list[str] = field(default_factory=list)
    percent_complete: int = 0

    @property
    def mc_id(self) -> str | None:
        """The Message Center ID (e.g. MC123456) if the sync put one anywhere."""
        for text in [self.title, *self.links, self.description]:
            m = MC_ID.search(text or "")
            if m:
                return m.group(0)
        return None

    @property
    def dedup_key(self) -> str:
        return self.mc_id or f"planner-{self.task_id}"

    @property
    def searchable_text(self) -> str:
        return "\n".join([self.title, self.bucket, " ".join(self.labels), self.description])


def get_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    """App-only token (client credentials). Needs Tasks.Read.All application permission."""
    r = requests.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


class PlannerClient:
    def __init__(self, token: str, session: requests.Session | None = None):
        self.s = session or requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"

    def _get(self, url: str) -> dict:
        r = self.s.get(url, timeout=30)
        r.raise_for_status()
        return r.json()

    def _get_all(self, url: str) -> list[dict]:
        items: list[dict] = []
        while url:
            page = self._get(url)
            items.extend(page.get("value", []))
            url = page.get("@odata.nextLink")
        return items

    def fetch_items(self, plan_id: str, lookback_days: int, skip_completed: bool) -> list[PlannerItem]:
        buckets = {b["id"]: b["name"] for b in self._get_all(f"{GRAPH}/planner/plans/{plan_id}/buckets")}
        cat_names = self._get(f"{GRAPH}/planner/plans/{plan_id}/details").get("categoryDescriptions") or {}
        tasks = self._get_all(f"{GRAPH}/planner/plans/{plan_id}/tasks")

        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days) if lookback_days else None
        items = []
        for t in tasks:
            created = _parse_dt(t["createdDateTime"])
            if cutoff and created < cutoff:
                continue
            if skip_completed and t.get("percentComplete", 0) == 100:
                continue
            details = self._get(f"{GRAPH}/planner/tasks/{t['id']}/details")
            items.append(
                PlannerItem(
                    task_id=t["id"],
                    title=t.get("title", ""),
                    created=created,
                    bucket=buckets.get(t.get("bucketId"), ""),
                    labels=[cat_names.get(k) or k for k, on in (t.get("appliedCategories") or {}).items() if on],
                    description=details.get("description") or "",
                    links=[unquote(k) for k in (details.get("references") or {})],
                    percent_complete=t.get("percentComplete", 0),
                )
            )
        return items


def _parse_dt(s: str) -> datetime:
    # Graph returns e.g. 2026-11-18T09:12:44.1234567Z; Python wants <= 6 fraction digits.
    s = re.sub(r"(\.\d{6})\d+", r"\1", s.replace("Z", "+00:00"))
    return datetime.fromisoformat(s)
