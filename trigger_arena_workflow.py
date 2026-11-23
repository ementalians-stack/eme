#!/usr/bin/env python3
"""Ask GitHub Actions to run the Arena monitor immediately.

Used by the external Render cron service. The GitHub token is read only from
the GITHUB_ACTIONS_TOKEN environment variable and is never printed.
"""
from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OWNER = "ementalians-stack"
REPOSITORY = "eme"
WORKFLOW = "arena-monitor.yml"
BRANCH = "main"


def main() -> int:
    token = os.environ.get("GITHUB_ACTIONS_TOKEN", "").strip()
    if not token:
        print("Missing GITHUB_ACTIONS_TOKEN environment variable.", file=sys.stderr)
        return 2

    url = (
        f"https://api.github.com/repos/{OWNER}/{REPOSITORY}"
        f"/actions/workflows/{WORKFLOW}/dispatches"
    )
    payload = json.dumps({"ref": BRANCH}).encode("utf-8")
    request = Request(
        url,
        data=payload,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "Arena-Text-Render-Scheduler",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=20) as response:
            if response.status != 204:
                print(
                    f"GitHub returned unexpected HTTP status {response.status}.",
                    file=sys.stderr,
                )
                return 1
    except HTTPError as exc:
        print(f"GitHub rejected the workflow trigger (HTTP {exc.code}).", file=sys.stderr)
        return 1
    except (URLError, TimeoutError, OSError):
        print("Could not reach GitHub to trigger the workflow.", file=sys.stderr)
        return 1

    print("Successfully triggered the Arena monitoring workflow.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
