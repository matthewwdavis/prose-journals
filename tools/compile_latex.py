#!/usr/bin/env python3
"""Does the journal's LaTeX compile? prose's sample, exported to LaTeX for the journal and compiled
with Tectonic: what a pull request's "latex" job runs.

    PROSE_SRC=/path/to/prose python tools/compile_latex.py journals/x.json [--report FILE.md]
        [--publisher-files DIR] [--cache DIR] [--download] [--out DIR]

The journal is set up in a scratch prose (tools/prose_scratch.py). A template that needs its
publisher's files (latex.files, latex.fetch.files) gets them as tools/publisher.py says: never from
the library, which doesn't hold them; with --download, a journal whose latex.fetch has a "url"
downloads the publisher's package into the cache only. When the files can't be had, the job is
skipped and says so ("needs the publisher's sn-jnl.cls, which the library never holds: skipped").
Skipped too, and said, without prose or Tectonic. --out DIR keeps the compiled PDF (DIR/<id>/latex.pdf).

Exit code 1 only when Tectonic fails.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import traceback
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import common, prose_scratch, publisher  # noqa: E402


def add_publisher_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--prose-src", metavar="DIR", help="a copy of prose's source (default: PROSE_SRC; 'none': no prose)")
    ap.add_argument("--publisher-files", metavar="DIR", action="append", default=[],
                    help="a folder with the publisher's files (DIR/<name> or DIR/<id>/<name>); may be given more than once")
    ap.add_argument("--cache", metavar="DIR", help="where downloaded publisher's files are kept (default: LIBRARY_CACHE or ~/.cache)")
    ap.add_argument("--download", action="store_true", help="download the publisher's package from latex.fetch.url into the cache")


def prepare(args, job: str, what: str) -> tuple[dict | None, str | None, dict, str | None, str]:
    """What a prose job needs before it starts: (journal, prose source, publisher files, Tectonic,
    the report line when it has to skip: else "")."""
    where = common.rel(args.file)
    j, _, why = common.read_journal(args.file)
    if j is None:
        return None, None, {}, None, f"{where}: {what} was skipped: {why}"
    errors, _ = common.journalfile.validate(j)
    if errors:
        return None, None, {}, None, f"{where}: {what} was skipped: the file doesn't pass the format check ({errors[0]})"
    src, why = prose_scratch.find_prose(args.prose_src)
    if src is None:
        return j, None, {}, None, f"{where}: {what} was skipped: {why}"
    tex = prose_scratch.tectonic(src)
    if tex is None:
        return j, src, {}, None, f"{where}: {what} was skipped: Tectonic (the LaTeX program prose uses) isn't here"
    root = src or None
    if src == "":   # (an installed prose: its templates are beside its package)
        import importlib.util
        spec = importlib.util.find_spec("prose")
        root = os.path.dirname(os.path.dirname(spec.origin)) if spec and spec.origin else None
    found, missing, notes = publisher.gather(j, prose_root=root, folders=args.publisher_files, cache=args.cache, download=args.download)
    if missing:
        return j, src, {}, tex, f"{where}: {what} {publisher.skipped_sentence(missing, j, args.download)}"
    for n in notes:
        print(f"{where}: note: {n}")
    return j, src, found, tex, ""


def latex_class(j: dict) -> str:
    lx = j.get("latex") or {}
    return lx.get("class") or "article (prose's standard LaTeX)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Compile prose's sample in the journal's LaTeX with Tectonic.")
    ap.add_argument("file")
    ap.add_argument("--report", metavar="FILE.md")
    ap.add_argument("--out", metavar="DIR", help="keep the compiled PDF here (DIR/<id>/latex.pdf)")
    add_publisher_args(ap)
    args = ap.parse_args(argv)
    where = common.rel(args.file)
    j, src, found, tex, skip = prepare(args, "latex", "the LaTeX check")
    if skip:
        print(skip)
        common.write_report(args.report, "latex", "skipped", [skip], "LaTeX skipped")
        return 0
    lines: list[str] = []
    try:
        with prose_scratch.Scratch(src) as s:
            got = s.set_up(args.file, found, latex=True)
            from prose import exporting
            doc, _ = s.sample()
            ws = s.workspace()
            out_zip = os.path.join(s.work, "latex.zip")
            exporting.export_latex(ws, json.loads(json.dumps(doc)), {"journal": got["key"], "cites": True}, prose_scratch.SAMPLE_REFS,
                                   out_zip, build_pdf=False)
            pkg = os.path.join(s.work, "latex")
            with zipfile.ZipFile(out_zip) as z:
                z.extractall(pkg)
            folder = os.path.join(pkg, "manuscript")
            # (shell escape stays off, as in prose: a journal file is data, never code)
            proc = subprocess.run([tex, "-X", "compile", "--keep-logs", "manuscript.tex"], cwd=folder, capture_output=True, text=True,
                                  timeout=900)
            pdf = os.path.join(folder, "manuscript.pdf")
            ok = proc.returncode == 0 and os.path.isfile(pdf)
            if ok and args.out:
                os.makedirs(os.path.join(args.out, j["id"]), exist_ok=True)
                shutil.copyfile(pdf, os.path.join(args.out, j["id"], "latex.pdf"))
            log = (proc.stdout + "\n" + proc.stderr).strip().splitlines()
    except Exception as exc:   # noqa: BLE001 (prose couldn't make the LaTeX: said)
        print(traceback.format_exc()[-2000:])
        line = f"{where}: error: prose couldn't export the sample to LaTeX for it: {type(exc).__name__}: {str(exc)[:300]}"
        common.write_report(args.report, "latex", "failed", [line], f"LaTeX not compiled in {latex_class(j)}")
        print(line)
        return 1
    how = got["how"] + (f"; publisher's files: {', '.join(sorted(found))}" if found else "")
    lines += [f"{where}: note: {n}" for n in got["notes"]]
    if not got["same"]:
        lines.append(f"{where}: note: its latex section differs from prose's built-in copy, so prose's copy was compiled, not the file's")
    if ok:
        print(f"{where}: LaTeX compiles in {latex_class(j)} ({how})")
        common.write_report(args.report, "latex", "passed", lines, f"LaTeX compiles in {latex_class(j)} ({got['how']})")
        return 0
    first = next((ln.strip() for ln in log if ln.startswith(("error:", "! ")) or "Undefined control sequence" in ln), "")
    lines.append(f"{where}: error: the sample doesn't compile in its LaTeX ({latex_class(j)}): {first or 'Tectonic stopped'}")
    tail = "\n".join(log[-25:])
    details = f"<details><summary>The end of Tectonic's log</summary>\n\n```\n{tail}\n```\n</details>"
    common.write_report(args.report, "latex", "failed", lines, f"LaTeX fails in {latex_class(j)} ({how})", details)
    print("\n".join(lines))
    print(tail)
    return 1


if __name__ == "__main__":
    sys.exit(main())
