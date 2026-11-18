#!/usr/bin/env python3
"""The journals due to be checked against their guidelines again: limits.checked over 18 months
ago (or never given, for a Verified journal). The monthly reverify workflow opens an issue with
this list.

    python tools/reverify.py [--out FILE.md]

Prints the issue's text (Markdown); exit code 0 always. The first line says how many are due.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import common  # noqa: E402
from tools.check_journal import REVERIFY_MONTHS  # noqa: E402


def due(folder: str = common.JOURNALS) -> list[dict]:
    out = []
    for path in common.journal_files(folder):
        j, _, _ = common.read_journal(path)
        if not j:
            continue
        limits = j.get("limits") if isinstance(j.get("limits"), dict) else {}
        checked = common.date_of(limits.get("checked"))
        status = (j.get("verified") or {}).get("status") if isinstance(j.get("verified"), dict) else None
        if checked is None and status != "verified":
            continue
        if checked is None or common.months_between(checked, common.today()) > REVERIFY_MONTHS:
            out.append({"file": f"journals/{os.path.basename(path)}", "name": j.get("name", "?"), "checked": checked.isoformat() if checked else None,
                        "status": status or "unverified", "guidelines": j.get("guidelines") or limits.get("source") or ""})
    return out


def markdown(items: list[dict]) -> str:
    head = f"{len(items)} journal{'s are' if len(items) != 1 else ' is'} due to be checked against {'their' if len(items) != 1 else 'its'} guidelines again"
    lines = [head, "",
             f"Each one's limits were last checked over {REVERIFY_MONTHS} months ago. Read the journal's guidelines, correct the file, "
             "set limits.checked to the day you read them, raise its version, and send it as a pull request.", ""]
    for it in items:
        when = f"last checked {it['checked']}" if it["checked"] else "never checked"
        link = f" ([guidelines]({it['guidelines']}))" if it["guidelines"].startswith(("http://", "https://")) else ""
        lines.append(f"- [ ] **{it['name']}** (`{it['file']}`, {it['status']}): {when}{link}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="List the journals due to be checked again.")
    ap.add_argument("--out", metavar="FILE.md")
    args = ap.parse_args(argv)
    text = markdown(due())
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
