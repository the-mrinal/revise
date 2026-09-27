"""Refresh titles and difficulties in server/data/problems.json from LeetCode.

problems.json lists every LeetCode problem Revise knows by number and slug.
This script asks LeetCode's public GraphQL endpoint for each slug's official
title and difficulty, checks the number LeetCode gives back, and writes the
file again with today's date as `fetched_on`.

    python server/scripts/fetch_problem_titles.py

To add a problem, add `"<number>": {"slug": "<slug>"}` to the file by hand,
then run the script. Slugs LeetCode does not answer for are listed at the end
and left without a title; nothing is guessed.
"""

from __future__ import annotations

import datetime
import json
import sys
import time
import urllib.request
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "problems.json"
ENDPOINT = "https://leetcode.com/graphql"
QUERY = (
    "query q($s: String!) { question(titleSlug: $s) "
    "{ questionFrontendId title titleSlug difficulty } }"
)
DELAY_SECONDS = 0.5


def fetch(slug: str) -> dict | None:
    body = json.dumps({"query": QUERY, "variables": {"s": slug}}).encode()
    req = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Referer": "https://leetcode.com",
            "User-Agent": "revise-problem-titles/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp).get("data", {}).get("question")


def dump(data: dict) -> str:
    """The file's layout: one problem per line, so a diff shows what changed."""
    head = {k: v for k, v in data.items() if k != "problems"}
    lines = ["{"]
    for k, v in head.items():
        lines.append(f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)},")
    lines.append('  "problems": {')
    rows = [
        f"    {json.dumps(num)}: {json.dumps(entry, ensure_ascii=False)}"
        for num, entry in data["problems"].items()
    ]
    lines.append(",\n".join(rows))
    lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> int:
    data = json.loads(DATA_FILE.read_text())
    problems: dict[str, dict] = data["problems"]
    unanswered: list[str] = []
    mismatched: list[str] = []

    for num in sorted(problems, key=int):
        entry = problems[num]
        slug = entry["slug"]
        try:
            q = fetch(slug)
        except Exception as e:  # network or HTTP error: report, don't guess
            print(f"{num} {slug}: {e}", file=sys.stderr)
            q = None
        if not q:
            unanswered.append(f"{num} {slug}")
        elif q["questionFrontendId"] != num:
            mismatched.append(f"{num} {slug}: LeetCode says #{q['questionFrontendId']}")
        else:
            entry["title"] = q["title"]
            entry["difficulty"] = q["difficulty"]
        time.sleep(DELAY_SECONDS)

    data["fetched_on"] = datetime.date.today().isoformat()
    data["problems"] = {k: problems[k] for k in sorted(problems, key=int)}
    DATA_FILE.write_text(dump(data))

    print(f"{len(problems)} problems, {len(unanswered)} unanswered, {len(mismatched)} mismatched")
    for line in unanswered:
        print("unanswered:", line)
    for line in mismatched:
        print("mismatched:", line)
    return 1 if unanswered or mismatched else 0


if __name__ == "__main__":
    sys.exit(main())
