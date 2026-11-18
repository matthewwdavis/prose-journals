#!/usr/bin/env python3
"""The library's preview pages (GitHub Pages): an index of every journal, each with its preview, made
after a merge by publish.yml from what tools/render_preview.py wrote.

    python tools/build_site.py SITE_DIR [--reports DIR] [--missing-out FILE]

SITE_DIR holds previews/<id>/ (sample.pdf, sample.docx, page-1.png, page-2.png); this writes
SITE_DIR/index.html, listing every journal in index.json, linking the files each has, and
SITE_DIR/.nojekyll.

--reports DIR: the preview reports render_preview.py wrote (DIR/<id>.md). A journal whose preview
couldn't be made says so on the page, with the reason, instead of "no preview yet"; on GitHub
(GITHUB_ACTIONS) each is also an error on the run. --missing-out FILE writes one line for each
journal without a preview ("<name>: <why>"), for the workflow to fail on after the site is published.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import common  # noqa: E402

FILES = [("page-1.png", "page 1"), ("page-2.png", "page 2"), ("sample.pdf", "PDF"), ("sample.docx", "Word")]


def why_missing(reports: str | None, ident: str) -> str | None:
    """Why a journal's preview couldn't be made, from its report (reports/<id>.md), or None when it
    has no report or the report doesn't say it failed."""
    if not reports:
        return None
    path = os.path.join(reports, f"{ident}.md")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        r = common.read_report(fh.read())
    if r["result"] not in ("failed", "skipped", "unknown"):
        return None
    line = next((ln for ln in r["lines"] if "couldn't be made" in ln or "skipped" in ln), r["lines"][0] if r["lines"] else r["checked"])
    why = line.split(": ", 1)[1] if line.startswith("journals/") and ": " in line else line
    for said in ("the preview couldn't be made: ", "the preview was skipped: "):
        why = why[len(said):] if why.startswith(said) else why
    return why


def page(site: str, reports: str | None = None, missing: list[str] | None = None) -> str:
    with open(common.INDEX, encoding="utf-8") as fh:
        index = json.load(fh)
    rows = []
    for j in sorted(index.get("journals") or [], key=lambda e: str(e.get("name", "")).lower()):
        ident = str(j.get("id", ""))
        have = [(f, words) for f, words in FILES if os.path.isfile(os.path.join(site, "previews", ident, f))]
        links = " · ".join(f'<a href="previews/{html.escape(ident)}/{f}">{words}</a>' for f, words in have)
        if not have:
            why = why_missing(reports, ident)
            if why is not None or reports:
                why = why or "it has no report from the preview step"
                links = f'<span class="err">The preview couldn\'t be made: {html.escape(why)}</span>'
                if missing is not None:
                    missing.append(f"{j.get('name', ident)}: {why}")
            else:
                links = "no preview yet"
        thumb = (f'<a href="previews/{html.escape(ident)}/page-1.png"><img src="previews/{html.escape(ident)}/page-1.png" '
                 f'alt="Page 1 of the sample in {html.escape(str(j.get("name", "")))}" loading="lazy"></a>'
                 if ("page-1.png", "page 1") in have else "")
        badge = "Verified" if j.get("verified") == "verified" else "Community"
        rows.append(f'<li>{thumb}<div><b>{html.escape(str(j.get("name", ident)))}</b> <span class="badge">{badge}</span> '
                    f'<span class="v">version {html.escape(str(j.get("version", "")))}</span><br>'
                    f'<span class="s">{html.escape(str(j.get("summary", "")))}</span><br>{links}</div></li>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Journal library previews</title>
<style>
:root{{color-scheme:light dark;--fg:#1d1d1f;--bg:#fff;--mut:#666;--line:#ddd}}
@media (prefers-color-scheme:dark){{:root{{--fg:#eee;--bg:#1b1b1d;--mut:#aaa;--line:#333}}}}
body{{font:15px/1.45 -apple-system,system-ui,sans-serif;color:var(--fg);background:var(--bg);margin:0 auto;max-width:900px;padding:16px}}
ul{{list-style:none;padding:0}} li{{display:flex;gap:16px;padding:12px 0;border-top:1px solid var(--line)}}
img{{width:120px;border:1px solid var(--line);background:#fff}} .s,.v{{color:var(--mut)}}
.badge{{font-size:12px;border:1px solid var(--line);border-radius:8px;padding:0 6px}} a{{color:inherit}}
.err{{color:#b3261e}} @media (prefers-color-scheme:dark){{.err{{color:#f2b8b5}}}}
</style></head><body>
<h1>Journal library previews</h1>
<p>prose's sample document set in each journal of the library: its pages, PDF and Word file. The journals themselves are in
<a href="https://github.com/{html.escape(os.environ.get("GITHUB_REPOSITORY", "matthewwdavis/prose-journals"))}">the library</a>.</p>
<ul>
{chr(10).join(rows)}
</ul></body></html>
"""


def _escape(text: str) -> str:
    """A GitHub workflow command's message, escaped (so it stays one command)."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write the preview site's index page.")
    ap.add_argument("site")
    ap.add_argument("--reports", metavar="DIR", help="the preview reports (DIR/<id>.md): a preview that couldn't be made says why")
    ap.add_argument("--missing-out", metavar="FILE", help="one line for each journal without a preview")
    args = ap.parse_args(argv)
    site = args.site
    os.makedirs(site, exist_ok=True)
    missing: list[str] = []
    with open(os.path.join(site, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page(site, args.reports, missing))
    open(os.path.join(site, ".nojekyll"), "w").close()
    print(f"{os.path.join(site, 'index.html')}: written")
    for m in missing:
        print(f"::error title=Preview not made::{_escape(m)}" if os.environ.get("GITHUB_ACTIONS") == "true" else f"preview not made: {m}")
    if args.missing_out:
        with open(args.missing_out, "w", encoding="utf-8") as fh:
            fh.write("".join(f"{' '.join(m.split())}\n" for m in missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
