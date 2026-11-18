#!/usr/bin/env python3
"""Measure the journal's printed layout from its LaTeX and compare it with the file's word.layout:
what a pull request's "word-layout" job runs (on a Mac, for the fonts).

    PROSE_SRC=/path/to/prose python tools/measure_layout.py journals/x.json [--report FILE.md]
        [--publisher-files DIR] [--cache DIR] [--download] [--measured FILE.json]

It runs prose's own tools/measure_journal_layout.py (from PROSE_SRC) on the journal, set up in a
scratch prose as the LaTeX job does, and compares what it measures with the file's word.layout:
sizes within 0.2 pt, lengths within 0.5 mm, everything else the same. Not compared: when it was
measured, the values corrected by hand (listed under "hand"), and the fonts Word stands in with
("fonts", and each part's "font" beside its "pdf_font"): those are chosen among the Word fonts
installed where it's measured (a computer with Word has Century Schoolbook; GitHub's machines
don't), so a different choice is a note, not a difference. The PDF's own fonts are compared.
A file without a word.layout passes, and the report holds the measured layout to paste in
(--measured FILE.json writes it too). Skipped, and said, without prose, Tectonic or the
publisher's files. Exit code 1 when the layouts differ.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import common, prose_scratch  # noqa: E402
from tools.compile_latex import add_publisher_args, prepare  # noqa: E402

PT = 0.2
MM = 0.5
NOT_COMPARED = {"measured_from", "hand", "fonts"}
SHOWN = 20
SHOW_JSON = 30_000   # (a pull request's comment holds at most 65,536 characters)


def shown(text: str) -> str:
    """The measured layout for the comment, or a sentence when it's too long for it."""
    if len(text) <= SHOW_JSON:
        return f"```json\n{text}\n```"
    return "It's too long for this comment: it's word-layout-measured.json in the word-layout job's report (Actions ▸ this run ▸ Artifacts), or run tools/measure_layout.py with --measured."


def compare(given: dict, measured: dict, pt: float = PT, mm: float = MM, extra: list | None = None) -> list[str]:
    """Where the measured layout differs from the given one, one sentence each ("styles.h1.size:
    the file says 17, measured 16.5"). What was measured that the file doesn't give at all (a field
    measured since the file was made) isn't a difference: it goes into `extra`, when given."""
    hand = set(given.get("hand") or [])
    out: list[str] = []
    extra = extra if extra is not None else []

    def walk(a, b, path: str) -> None:
        if path in hand or path.split(".")[0] in NOT_COMPARED:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                sub = f"{path}.{k}" if path else k
                if k == "font" and "pdf_font" in a and "pdf_font" in b:
                    # (Word's stand-in for the PDF's font: chosen among the fonts installed here)
                    if k in a and k in b and a[k] != b[k] and sub not in hand:
                        fonts.setdefault((json.dumps(a[k]), json.dumps(b[k])), []).append(sub)
                    continue
                if k not in b:
                    if sub.split(".")[0] not in NOT_COMPARED and sub not in hand:
                        out.append(f"{sub}: the file gives {json.dumps(a[k])[:60]}, which wasn't measured")
                elif k not in a:
                    if sub.split(".")[0] not in NOT_COMPARED and sub not in hand:
                        extra.append(f"{sub}: measured {json.dumps(b[k])[:60]}, which the file doesn't give")
                else:
                    walk(a[k], b[k], sub)
            return
        if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f"{path}[{i + 1}]")
            return
        num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)   # noqa: E731
        if num(a) and num(b):
            tol = mm if "_mm" in path else pt
            if abs(a - b) > tol + 1e-9:
                unit = "mm" if "_mm" in path else "pt"
                out.append(f"{path}: the file says {a:g}, measured {b:g} (more than {tol:g} {unit} apart)")
            return
        if a != b:
            out.append(f"{path}: the file says {json.dumps(a)[:60]}, measured {json.dumps(b)[:60]}")

    fonts: dict[tuple[str, str], list[str]] = {}
    walk(given, measured, "")
    for (said, here), where in sorted(fonts.items()):
        extra.append(f"{where[0]}{f' and {len(where) - 1} more' if len(where) > 1 else ''}: Word's stand-in font chosen here is "
                     f"{here}, the file says {said} (chosen among the Word fonts installed where it's measured; not compared)")
    return out


def load_measurer(root: str):
    path = os.path.join(root, "tools", "measure_journal_layout.py")
    if not os.path.isfile(path):
        return None
    spec = importlib.util.spec_from_file_location("measure_journal_layout", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Measure the journal's printed layout and compare it with its word.layout.")
    ap.add_argument("file")
    ap.add_argument("--report", metavar="FILE.md")
    ap.add_argument("--measured", metavar="FILE.json", help="write the measured layout here")
    add_publisher_args(ap)
    args = ap.parse_args(argv)
    where = common.rel(args.file)
    j, src, found, tex, skip = prepare(args, "word-layout", "the Word layout check")
    if not skip and not src:
        skip = f"{where}: the Word layout check was skipped: it needs a copy of prose's source (PROSE_SRC), for its tools/measure_journal_layout.py"
    if skip:
        print(skip)
        common.write_report(args.report, "word-layout", "skipped", [skip], "Word layout skipped")
        return 0
    given = (j.get("word") or {}).get("layout")
    try:
        with prose_scratch.Scratch(src) as s:
            mod = load_measurer(s.root)
            if mod is None:
                raise RuntimeError("prose's tools/measure_journal_layout.py isn't in PROSE_SRC")
            got = s.set_up(args.file, found, latex=True)
            ws = s.workspace()
            mod.figure_pdf(os.path.join(ws.assets, "fig1.pdf"), 80, 60)
            mod.figure_pdf(os.path.join(ws.assets, "fig2.pdf"), 160, 90)
            measured = mod.measure(mod.compile_journal(got["key"], ws, s.work))
            sub = mod.measure(mod.compile_journal(got["key"], ws, s.work, submission=True))
            layout = mod.entry_for(got["key"], measured, sub)
    except Exception as exc:   # noqa: BLE001 (said in the report)
        print(traceback.format_exc()[-2000:])
        line = f"{where}: error: its layout couldn't be measured: {type(exc).__name__}: {str(exc)[:300]}"
        common.write_report(args.report, "word-layout", "failed", [line], "Word layout not measured")
        print(line)
        return 1
    if not args.measured and args.report:   # (beside the report, so the job's artifact holds it)
        args.measured = os.path.join(os.path.dirname(os.path.abspath(args.report)), "word-layout-measured.json")
    if args.measured:
        with open(args.measured, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(layout, indent=1, ensure_ascii=False) + "\n")
    lines = [f"{where}: note: {got['how']}"] if not got["same"] else []
    if not given:
        text = json.dumps(layout, indent=1, ensure_ascii=False)
        details = (f"<details><summary>The measured layout: paste it into the file as \"word\": {{\"layout\": …}}</summary>\n\n"
                   f"{shown(text)}\n</details>")
        lines.append(f"{where}: note: it has no word.layout; the measured one is below")
        common.write_report(args.report, "word-layout", "passed", lines, "Word layout measured", details)
        print("\n".join(lines))
        return 0
    extra: list[str] = []
    diffs = compare(given, layout, extra=extra)
    lines += [f"{where}: note: word.layout.{e}" for e in extra[:SHOWN]]
    if not diffs:
        common.write_report(args.report, "word-layout", "passed", lines, "Word layout compared (the same)")
        print(f"{where}: its word.layout is what its LaTeX prints")
        return 0
    lines += [f"{where}: error: word.layout.{d}" for d in diffs[:SHOWN]]
    if len(diffs) > SHOWN:
        lines.append(f"{where}: error: and {len(diffs) - SHOWN} more differences")
    text = json.dumps(layout, indent=1, ensure_ascii=False)
    details = ("<details><summary>The layout measured now: if the journal's LaTeX really prints this, replace the file's "
               "\"word\": {\"layout\": …} with it (for a journal built into the apps, re-measure it in labapp with prose's "
               f"tools/measure_journal_layout.py instead)</summary>\n\n{shown(text)}\n</details>")
    common.write_report(args.report, "word-layout", "failed", lines, f"Word layout compared ({len(diffs)} differences)", details)
    print("\n".join(lines))
    return 1


if __name__ == "__main__":
    sys.exit(main())
