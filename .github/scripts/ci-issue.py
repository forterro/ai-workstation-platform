#!/usr/bin/env python3
"""Open, update or close the GitHub issue that tracks a failing workflow, so a red run nobody
watches (Renovate PRs, scheduled runs) still notifies someone.

    ci-issue.py failure --key KEY --title TITLE [--context TEXT]
    ci-issue.py success --key KEY

One open issue per KEY (hidden marker in its body, label `ci-failure`). A failure creates it, or
comments on it, with the error lines of the failed jobs of the current run; a success comments and
closes every open issue of the key. Assignees: CI_ISSUE_ASSIGNEES (comma-separated).
Env: GH_TOKEN (issues: write, actions: read), GITHUB_REPOSITORY, GITHUB_SERVER_URL, GITHUB_RUN_ID.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

LABEL = "ci-failure"
ERROR_LINE = re.compile(
    r"##\[error\]|::error|\[ERROR\]|^\s*- at '|Error:|Traceback|FAILED|failed|^E\s{3}",
    re.IGNORECASE,
)
NOISE = re.compile(r"Process completed with exit code|^\d+ chart\(s\) linted, 0 chart\(s\) failed")
REPO = os.environ["GITHUB_REPOSITORY"]
RUN_ID = os.environ.get("GITHUB_RUN_ID", "")
RUN_URL = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{REPO}/actions/runs/{RUN_ID}"


def api(method, path, body=None):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = resp.read()
            return resp.status, json.loads(data) if data else {}
    except urllib.error.HTTPError as e:
        return e.code, {"message": e.read().decode(errors="replace")[:300]}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def job_log(job_id):
    """The API answers 302 to a signed blob URL that rejects the GitHub Authorization header."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/actions/jobs/{job_id}/logs",
        headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}"},
    )
    try:
        urllib.request.build_opener(_NoRedirect).open(req)
    except urllib.error.HTTPError as e:
        location = e.headers.get("Location") if e.code in (301, 302, 307) else None
        if location:
            with urllib.request.urlopen(location) as resp:
                return resp.read().decode(errors="replace")
    return ""


def find_issues(marker):
    """Open issues of this key, oldest first (the listing lags a few seconds behind a creation)."""
    _, issues = api("GET", f"/issues?state=open&labels={LABEL}&per_page=100&direction=asc")
    if not isinstance(issues, list):
        return []
    return [i for i in issues if marker in (i.get("body") or "")]


def failed_jobs_excerpt():
    """Distinct error lines of the failed jobs of this run (timestamps stripped), 25 per job."""
    _, payload = api("GET", f"/actions/runs/{RUN_ID}/jobs?per_page=100")
    parts = []
    for job in payload.get("jobs") or []:
        if job.get("conclusion") != "failure":
            continue
        seen, lines = set(), []
        for line in job_log(job["id"]).splitlines():
            line = re.sub(r"^\S+Z\s", "", line.rstrip())[:300]
            if ERROR_LINE.search(line) and not NOISE.search(line) and line not in seen:
                seen.add(line)
                lines.append(line)
        excerpt = "\n".join(lines[-25:] or ["(no error line)"])
        parts.append(f"**{job['name']}**\n\n```text\n{excerpt}\n```")
    return "\n\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("result", choices=["failure", "success"])
    ap.add_argument("--key", required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("--context", default="", help="Extra markdown line (e.g. the PR link).")
    args = ap.parse_args()
    marker = f"<!-- ci-failure:{args.key} -->"
    issues = find_issues(marker)

    if args.result == "success":
        for issue in issues:
            num = issue["number"]
            api("POST", f"/issues/{num}/comments", {"body": f"Fixed: [green run]({RUN_URL})."})
            api("PATCH", f"/issues/{num}", {"state": "closed", "state_reason": "completed"})
            print(f"issue #{num} closed")
        return 0

    details = f"[Failed run]({RUN_URL})\n\n{failed_jobs_excerpt()}\n"
    if issues:
        num = issues[0]["number"]
        api("POST", f"/issues/{num}/comments", {"body": "Still failing. " + details})
        print(f"issue #{num} updated")
        return 0
    api("POST", "/labels", {"name": LABEL, "color": "d73a4a", "description": "A CI run fails"})
    assignees = [a.strip() for a in os.environ.get("CI_ISSUE_ASSIGNEES", "").split(",")]
    assignees = [a for a in assignees if a]
    body = (
        f"{marker}\nA CI run failed and nobody may be watching it "
        "(Renovate PR or scheduled run).\n\n"
        + (f"{args.context}\n\n" if args.context else "")
        + details
        + "\nCommented on every new failure, closed automatically by the next green run."
    )
    title = args.title or f"CI failure: {args.key}"
    status, created = api(
        "POST", "/issues", {"title": title, "body": body, "labels": [LABEL], "assignees": assignees}
    )
    if status >= 400:
        print(f"::warning::cannot open the CI issue: HTTP {status} {created.get('message')}")
        return 0
    print(f"issue #{created['number']} opened")
    return 0


if __name__ == "__main__":
    sys.exit(main())
