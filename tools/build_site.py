#!/usr/bin/env python3
"""The library's preview pages (GitHub Pages): an index of every journal, each with its preview, made
after a merge by publish.yml from what tools/render_preview.py wrote.

    python tools/build_site.py SITE_DIR

SITE_DIR holds previews/<id>/ (sample.pdf, sample.docx, page-1.png, page-2.png); this writes
SITE_DIR/index.html, listing every journal in index.json, linking the files each has, and
SITE_DIR/.nojekyll.
"""
from __future__ import annotations

import html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import common  # noqa: E402

FILES = [("page-1.png", "page 1"), ("page-2.png", "page 2"), ("sample.pdf", "PDF"), ("sample.docx", "Word")]


def page(site: str) -> str:
    with open(common.INDEX, encoding="utf-8") as fh:
        index = json.load(fh)
    rows = []
    for j in sorted(index.get("journals") or [], key=lambda e: str(e.get("name", "")).lower()):
        ident = str(j.get("id", ""))
        have = [(f, words) for f, words in FILES if os.path.isfile(os.path.join(site, "previews", ident, f))]
        links = " · ".join(f'<a href="previews/{html.escape(ident)}/{f}">{words}</a>' for f, words in have) or "no preview yet"
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
</style></head><body>
<h1>Journal library previews</h1>
<p>prose's sample document set in each journal of the library: its pages, PDF and Word file. The journals themselves are in
<a href="https://github.com/{html.escape(os.environ.get("GITHUB_REPOSITORY", "matthewwdavis/prose-journals"))}">the library</a>.</p>
<ul>
{chr(10).join(rows)}
</ul></body></html>
"""


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print(__doc__)
        return 2
    site = args[0]
    os.makedirs(site, exist_ok=True)
    with open(os.path.join(site, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page(site))
    open(os.path.join(site, ".nojekyll"), "w").close()
    print(f"{os.path.join(site, 'index.html')}: written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
