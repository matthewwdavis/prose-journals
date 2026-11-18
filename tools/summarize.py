#!/usr/bin/env python3
"""Merge the check's job reports into the one comment a pull request shows.

    python tools/summarize.py REPORT.md ... [--title TEXT] [--preview-url URL] [--out FILE.md]
    python tools/summarize.py --dir reports/ ...

Each job (validate, latex, word-layout, preview, index) writes a report (tools/common.py:
write_report). The comment lists every problem, one a line in the checks' own words, then one
"Checked: ..." line ("Checked: schema, LaTeX compiles in sn-jnl, Word layout compared, preview
<link>"), then the jobs' details folded. Its first line is a marker the workflow finds the comment
by, so a new run updates it rather than adding another.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import common  # noqa: E402

MARKER = "<!-- prose-journals-check -->"
ORDER = ["validate", "index", "latex", "word-layout", "preview"]
WORDS = {"passed": "passed", "failed": "failed", "skipped": "skipped", "unknown": "didn't report"}


PROSE_JOBS = ("latex", "word-layout", "preview")
LIMIT = 60_000   # (a pull request's comment holds at most 65,536 characters)


def missing(reports: list[dict], expect: list[str]) -> list[dict]:
    """A report for each expected job that didn't write one: the jobs that run prose wait for
    validate to pass; any other job that's missing stopped before it could report (a failure)."""
    have = {r["job"] for r in reports}
    validate_failed = any(r["job"] == "validate" and r["result"] != "passed" for r in reports)
    out = []
    for job in expect:
        if job in have:
            continue
        if job in PROSE_JOBS and validate_failed:
            out.append({"job": job, "result": "skipped", "lines": [], "checked": f"{job} not run (it runs once validate passes)",
                        "details": ""})
        else:
            out.append({"job": job, "result": "unknown", "checked": "",
                        "lines": [f"error: the {job} job stopped before it could report; its log on the run's page says why"],
                        "details": ""})
    return out


def summarize(texts: list[str], title: str = "", preview_url: str = "", expect: list[str] | None = None) -> tuple[str, bool]:
    """(the comment, whether every job that ran passed)."""
    reports = [common.read_report(t) for t in texts]
    reports += missing(reports, expect or [])
    reports.sort(key=lambda r: ORDER.index(r["job"]) if r["job"] in ORDER else len(ORDER))
    ok = all(r["result"] in ("passed", "skipped") for r in reports if r["job"] != "preview")
    out = [MARKER, f"### Library check{': ' + title if title else ''}: {'passed' if ok else 'needs changes'}", ""]
    out.append(" · ".join(f"{r['job']} {WORDS.get(r['result'], r['result'])}" for r in reports) or "No job reported.")
    out.append("")
    problems = [ln for r in reports for ln in r["lines"]]
    out += [f"- {ln}" for ln in problems] if problems else ["No problems found."]
    out.append("")
    checked = []
    for r in reports:
        c = r["checked"]
        if r["job"] == "preview" and preview_url and r["result"] == "passed":
            c = f"preview [{c.replace('preview made', 'made').strip()}]({preview_url})"
        if c:
            checked.append(c)
    out.append(f"Checked: {'; '.join(checked) if checked else 'nothing'}")
    for r in reports:
        if r["details"]:
            if sum(len(x) + 1 for x in out) + len(r["details"]) > LIMIT:
                out += ["", f"(The {r['job']} job's details are too long for this comment: they're in its report on the run's page.)"]
                continue
            out += ["", r["details"]]
    return "\n".join(out).rstrip() + "\n", ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Merge the check's job reports into one pull request comment.")
    ap.add_argument("reports", nargs="*", metavar="REPORT.md")
    ap.add_argument("--dir", metavar="DIR", action="append", default=[], help="every .md report in this folder (and below)")
    ap.add_argument("--title", default="", help="what was checked (journals/x.json)")
    ap.add_argument("--preview-url", default="", help="where the preview's files are (the run's page)")
    ap.add_argument("--out", metavar="FILE.md")
    ap.add_argument("--expect", default="", help="the jobs that should have reported, comma-separated (a missing one is said)")
    ap.add_argument("--state-out", metavar="FILE", help="write success or failure here (the pull request's check result)")
    args = ap.parse_args(argv)
    paths = list(args.reports)
    for d in args.dir:
        paths += sorted(glob.glob(os.path.join(d, "**", "*.md"), recursive=True))
    texts = []
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            texts.append(fh.read())
    text, ok = summarize(texts, args.title, args.preview_url, [j for j in args.expect.split(",") if j])
    if args.state_out:
        with open(args.state_out, "w", encoding="utf-8") as fh:
            fh.write("success\n" if ok else "failure\n")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
