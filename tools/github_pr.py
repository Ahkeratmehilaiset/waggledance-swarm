#!/usr/bin/env python3
"""Clean GitHub PR creation/update via Python — no bash quoting artifacts.

Uses git credential manager for PAT authentication.
All payloads are built as Python dicts and serialized via json.dumps.

Usage:
    python tools/github_pr.py create --title "..." --body-file body.md --head branch --base main
    python tools/github_pr.py update 39 --body-file body.md
    python tools/github_pr.py status 39
"""

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request

REPO = "Ahkeratmehilaiset/waggledance-swarm"
API_BASE = f"https://api.github.com/repos/{REPO}"


def get_pat():
    """Retrieve PAT from git credential manager."""
    cred_input = b"protocol=https\nhost=github.com\n"
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input=cred_input, capture_output=True,
    )
    for line in proc.stdout.decode().splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    raise RuntimeError("No PAT found in git credential manager")


def api_request(method, path, data=None, pat=None):
    """Make an authenticated GitHub API request."""
    if pat is None:
        pat = get_pat()
    url = f"{API_BASE}{path}" if path.startswith("/") else path
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"token {pat}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()[:500]
        print(f"HTTP {e.code}: {err_body}", file=sys.stderr)
        raise


def create_pr(title, body, head, base="main"):
    """Create a new pull request. Returns PR dict."""
    data = {"title": title, "head": head, "base": base, "body": body}
    result = api_request("POST", "/pulls", data)
    print(f"PR #{result['number']}: {result['html_url']}")
    return result


def update_pr(pr_number, title=None, body=None):
    """Update an existing PR's title and/or body. Returns PR dict."""
    data = {}
    if title is not None:
        data["title"] = title
    if body is not None:
        data["body"] = body
    if not data:
        print("Nothing to update", file=sys.stderr)
        return get_pr(pr_number)
    result = api_request("PATCH", f"/pulls/{pr_number}", data)
    print(f"PR #{result['number']} updated: {result['html_url']}")
    return result


def get_pr(pr_number):
    """Get PR status."""
    result = api_request("GET", f"/pulls/{pr_number}")
    print(f"PR #{result['number']}: state={result['state']}, "
          f"merged={result['merged']}, mergeable={result.get('mergeable')}")
    return result


def main():
    parser = argparse.ArgumentParser(description="GitHub PR utility")
    sub = parser.add_subparsers(dest="command")

    c = sub.add_parser("create", help="Create a PR")
    c.add_argument("--title", required=True)
    c.add_argument("--body", default=None, help="PR body text")
    c.add_argument("--body-file", default=None, help="Read body from file")
    c.add_argument("--head", required=True, help="Head branch")
    c.add_argument("--base", default="main", help="Base branch")

    u = sub.add_parser("update", help="Update a PR")
    u.add_argument("pr_number", type=int)
    u.add_argument("--title", default=None)
    u.add_argument("--body", default=None)
    u.add_argument("--body-file", default=None)

    s = sub.add_parser("status", help="Get PR status")
    s.add_argument("pr_number", type=int)

    args = parser.parse_args()

    if args.command == "create":
        body = args.body
        if args.body_file:
            with open(args.body_file, "r", encoding="utf-8") as f:
                body = f.read()
        create_pr(args.title, body or "", args.head, args.base)

    elif args.command == "update":
        body = args.body
        if args.body_file:
            with open(args.body_file, "r", encoding="utf-8") as f:
                body = f.read()
        update_pr(args.pr_number, title=args.title, body=body)

    elif args.command == "status":
        get_pr(args.pr_number)

    else:
        parser.print_help()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
