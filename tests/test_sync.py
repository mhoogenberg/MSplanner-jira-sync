from datetime import datetime, timedelta, timezone

import yaml

from sync.jira import dedup_label
from sync.main import run
from sync.planner import PlannerClient, PlannerItem, _parse_dt
from sync.relevance import rule_verdict

CFG = yaml.safe_load(open("config.yaml"))
REL = CFG["relevance"]


def item(title, desc="", bucket="", labels=(), links=(), days_old=1, task_id="t1"):
    return PlannerItem(task_id, title, datetime.now(timezone.utc) - timedelta(days=days_old),
                       bucket, list(labels), desc, list(links))


def test_mc_id_from_title_link_or_fallback():
    assert item("MC912345 - New Intune setting").mc_id == "MC912345"
    link = "https://admin.microsoft.com/AdminPortal/home#/MessageCenter/:/messages/MC887766"
    assert item("Something", links=[link]).mc_id == "MC887766"
    assert dedup_label(item("No id here", task_id="abc")) == "mc-planner-abc"


def test_rules():
    assert rule_verdict(item("Intune: new compliance setting"), REL["include"], REL["exclude"]).relevant
    assert rule_verdict(item("Update", desc="Changes to the new Outlook for Windows"), REL["include"], REL["exclude"]).relevant
    v = rule_verdict(item("Power BI report sharing changes"), REL["include"], REL["exclude"])
    assert v is not None and not v.relevant
    assert rule_verdict(item("Retiring a SharePoint API"), REL["include"], REL["exclude"]) is None
    # whole-word match: "iOS" must not match inside "radios"
    assert rule_verdict(item("New radios in forms"), REL["include"], []) is None


def test_parse_graph_datetime():
    assert _parse_dt("2026-11-18T09:12:44.1234567Z").year == 2026


class FakePlanner:
    def __init__(self, items):
        self.items = items

    def fetch_items(self, *a):
        return self.items


class FakeJira:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.created = []

    def existing_keys(self, project, label):
        return ["ITCE-1"] if label in self.existing else []

    def create_issue(self, project, issue_type, summary, description, labels):
        self.created.append((project, summary, labels))
        return f"ITCE-{100 + len(self.created)}"


def test_run_dry_run_creates_nothing():
    jira = FakeJira()
    stats = run(CFG, False, FakePlanner([item("MC1000001 Intune change")]), jira, None, out=lambda s: None)
    assert stats["relevant"] == 1 and stats["created"] == 0 and jira.created == []


def test_run_apply_dedups_and_filters():
    jira = FakeJira(existing={"mc-MC1000002"})
    items = [
        item("MC1000001 Intune change", task_id="a"),
        item("MC1000002 Microsoft Edge change", task_id="b"),
        item("MC1000003 Power BI change", task_id="c"),
    ]
    stats = run(CFG, True, FakePlanner(items), jira, None, out=lambda s: None)
    assert stats == {"seen": 3, "relevant": 2, "created": 1, "existing": 1, "skipped": 1}
    project, summary, labels = jira.created[0]
    assert project == "ITCE" and summary == "MC1000001 Intune change"
    assert "mc-MC1000001" in labels and "m365-message-center" in labels


class FakeResp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class FakeSession:
    headers: dict = {}

    def __init__(self, routes):
        self.routes = routes
        self.headers = {}

    def get(self, url, timeout=None):
        for suffix, data in self.routes.items():
            if url.endswith(suffix):
                return FakeResp(data)
        raise AssertionError(url)


def test_planner_fetch_filters_and_paginates():
    now = datetime.now(timezone.utc)
    new = now.strftime("%Y-%m-%dT%H:%M:%S.1234567Z")
    old = (now - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    G = "https://graph.microsoft.com/v1.0/planner"
    routes = {
        "/plans/P/buckets": {"value": [{"id": "b1", "name": "Intune"}]},
        "/plans/P/details": {"categoryDescriptions": {"category1": "Major change"}},
        "/plans/P/tasks": {"value": [
            {"id": "t1", "title": "MC1 new", "createdDateTime": new, "bucketId": "b1",
             "appliedCategories": {"category1": True}, "percentComplete": 0},
            {"id": "t2", "title": "old", "createdDateTime": old, "percentComplete": 0},
        ], "@odata.nextLink": f"{G}/page2"},
        "/page2": {"value": [{"id": "t3", "title": "done", "createdDateTime": new, "percentComplete": 100}]},
        "/tasks/t1/details": {"description": "body", "references": {
            "https%3A//admin%2Emicrosoft%2Ecom/#/MessageCenter/:/messages/MC1234567": {}}},
    }
    pc = PlannerClient("tok", session=FakeSession(routes))
    items = pc.fetch_items("P", 14, True)
    assert [i.task_id for i in items] == ["t1"]
    assert items[0].bucket == "Intune" and items[0].labels == ["Major change"]
    assert items[0].mc_id == "MC1234567"
