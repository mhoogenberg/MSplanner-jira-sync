# Planner → Jira sync (Client Engineering)

Reads the Microsoft 365 Message Center posts that the admin center syncs into
our Planner plan, keeps only the ones that matter to Client Engineering, and
creates a Jira issue for each one in **ITCE**. It runs on GitHub Actions on
weekdays at 06:00 UTC.

```
Planner plan ──Graph API──► relevance filter ──► Jira ITCE
 (Message Center sync)      (keywords, optional     (one issue per MC ID,
                             Claude check)           never duplicated)
```

## How it works

1. **Fetch**: reads every task in the plan through Microsoft Graph (with the
   bucket, Planner labels, full description and links), limited to tasks from
   the last `lookback_days` and skipping completed ones.
2. **Filter**: `relevance.include` keywords mean the post is relevant, and
   `relevance.exclude` keywords mean it isn't. If neither matches, the post is
   skipped. In `rules+llm` mode, Claude reads it and decides using
   `team_description` instead.
3. **Create**: each relevant post becomes an ITCE issue with the label
   `mc-<MC ID>`. Before creating an issue, the job searches Jira for that
   label, so a post is never created twice, even if Planner changes.
4. **Report**: every run writes a table (created / already exists / skipped,
   with the reason) to the GitHub Actions run summary.

**Dry run by default.** Nothing is written to Jira until you run with
`--apply`. In Actions, either tick "apply" on a manual run, or set the
repository variable `SYNC_APPLY=true` for scheduled runs.

## One-time setup

### 1. Entra app registration (Graph access)
1. In Entra ID, go to App registrations, create a new registration (e.g.
   "Planner Jira sync"), and choose single tenant.
2. Under API permissions, add the Microsoft Graph **application** permission
   `Tasks.Read.All`, then **grant admin consent**.
3. Under Certificates & secrets, create a new client secret and note its value.
4. Note the Application (client) ID.

### 2. Jira credentials
- **Jira Cloud**: create an API token for a service account at
  https://id.atlassian.com/manage-profile/security/api-tokens. You'll also
  need that account's email address.
- **Jira Data Center**: set `jira.deployment: datacenter` in `config.yaml` and
  create a Personal Access Token from your Jira profile. `JIRA_EMAIL` isn't
  needed in that case.

The account needs "Create issues" and "Browse projects" on ITCE.

### 3. GitHub secrets (Settings → Secrets and variables → Actions)
| Secret | Value |
|---|---|
| `AZURE_CLIENT_ID` | App registration client ID |
| `AZURE_CLIENT_SECRET` | Client secret value |
| `JIRA_EMAIL` | Service account email (Cloud only) |
| `JIRA_API_TOKEN` | API token (Cloud) or PAT (Data Center) |
| `ANTHROPIC_API_KEY` | Only if `relevance.mode: rules+llm` |

Variable `SYNC_APPLY` = `true` once you're happy with the dry runs.

> **Network note:** GitHub-hosted runners reach Jira over the internet. If
> `jira.biogen.com` is only reachable on the corporate network, use a
> self-hosted runner (change `runs-on`) or allowlist GitHub's runner IPs.

## Running locally

```bash
pip install -r requirements.txt pytest
python -m pytest -q
export AZURE_CLIENT_ID=... AZURE_CLIENT_SECRET=... JIRA_EMAIL=... JIRA_API_TOKEN=...
python -m sync.main            # dry run, prints what it would create
python -m sync.main --apply    # creates issues
```

## Tuning
All settings are in `config.yaml`: the keyword lists, issue type, labels,
look-back window, and the team description that Claude uses. After the first
dry runs, adjust the keyword lists using the "skip" and "would create" rows in
the summary.

## Not yet handled
- Updates to a post that already has an issue are not added to that issue.
  Only new posts are synced.
- Custom Jira fields, components and assignees aren't set yet. Add them in
  `sync/jira.py` `create_issue` when we know which ones you want.
