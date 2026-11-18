#!/usr/bin/env python3
"""A preview of one journal file: prose's sample document as a PDF (prose's own pages, Typst), a
Word file, and pictures of pages 1 and 2: what a pull request's "preview" job uploads.

    PROSE_SRC=/path/to/prose python tools/render_preview.py journals/x.json [--out DIR] [--report FILE.md]

The files go to DIR/<id>/ (default preview-out/<id>/, which git ignores): sample.pdf, sample.docx,
page-1.png, page-2.png. prose runs in throwaway folders (tools/prose_scratch.py), never with your own
settings. Without prose, the report says the preview was skipped and why. This job never fails:
what went wrong is in the report.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import common, prose_scratch  # noqa: E402

PAGES = 2
DPI = 110


def page_images(pdf: str, out_dir: str, pages: int = PAGES, dpi: int = DPI) -> tuple[list[str], str | None]:
    """PNG pictures of the PDF's first pages (pypdfium2, else PyMuPDF): ([paths], None) or ([], why)."""
    made = []
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(pdf)
        try:
            for i in range(min(pages, len(doc))):
                p = os.path.join(out_dir, f"page-{i + 1}.png")
                doc[i].render(scale=dpi / 72).to_pil().save(p)
                made.append(p)
        finally:
            doc.close()
        return made, None
    except ImportError:
        pass
    try:
        import fitz   # PyMuPDF
        doc = fitz.open(pdf)
        for i in range(min(pages, doc.page_count)):
            p = os.path.join(out_dir, f"page-{i + 1}.png")
            doc[i].get_pixmap(dpi=dpi).save(p)
            made.append(p)
        return made, None
    except ImportError:
        return [], "neither pypdfium2 nor PyMuPDF is here to draw the pages"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Make a journal file's preview: the sample as PDF, Word and page pictures.")
    ap.add_argument("file")
    ap.add_argument("--out", metavar="DIR", default=os.path.join(common.ROOT, "preview-out"))
    ap.add_argument("--report", metavar="FILE.md")
    ap.add_argument("--prose-src", metavar="DIR", help="a copy of prose's source (default: PROSE_SRC; 'none': no prose)")
    args = ap.parse_args(argv)
    j, _, why = common.read_journal(args.file)
    where = common.rel(args.file)
    if j is None or not isinstance(j.get("id"), str):
        common.write_report(args.report, "preview", "skipped", [f"{where}: the preview was skipped: {why or 'it has no id'}"],
                            "preview skipped")
        print(f"{where}: preview skipped: {why}")
        return 0
    src, why = prose_scratch.find_prose(args.prose_src)
    if src is None:
        line = f"{where}: the preview was skipped: {why}"
        common.write_report(args.report, "preview", "skipped", [line], "preview skipped (no prose)")
        print(line)
        return 0
    out_dir = os.path.join(os.path.abspath(args.out), j["id"])
    os.makedirs(out_dir, exist_ok=True)
    lines: list[str] = []
    made: list[str] = []
    try:
        with prose_scratch.Scratch(src) as s:
            got = s.set_up(args.file)
            from prose import exporting
            doc, which = s.sample()
            ws = s.workspace()
            settings = {"journal": got["key"], "cites": True}
            pdf = os.path.join(out_dir, "sample.pdf")
            exporting.export_pdf(ws, json.loads(json.dumps(doc)), settings, prose_scratch.SAMPLE_REFS, pdf)
            made.append("PDF")
            exporting.export_docx(ws, json.loads(json.dumps(doc)), settings, prose_scratch.SAMPLE_REFS, os.path.join(out_dir, "sample.docx"))
            made.append("Word")
            pics, why = page_images(pdf, out_dir)
            if pics:
                made.append(f"{len(pics)} page picture{'s' if len(pics) != 1 else ''}")
            else:
                lines.append(f"{where}: note: no page pictures: {why}")
        print(f"{where}: preview in {out_dir}: {', '.join(made)} (sample: {which})")
        result = "passed"
        checked = f"preview made ({', '.join(made)})"
    except Exception as exc:   # noqa: BLE001 (the preview never fails the check: what went wrong is said)
        print(traceback.format_exc()[-2000:])
        lines.append(f"{where}: the preview couldn't be made: {type(exc).__name__}: {str(exc)[:300]}")
        result = "failed"
        checked = f"preview not made{' (' + ', '.join(made) + ' only)' if made else ''}"
    common.write_report(args.report, "preview", result, lines, checked)
    return 0


if __name__ == "__main__":
    sys.exit(main())
