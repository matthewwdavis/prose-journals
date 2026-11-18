#!/usr/bin/env python3
"""Make index.json from journals/*.json: the list the apps read to search the library.

    python tools/build_index.py                   # write index.json
    python tools/build_index.py --out FILE        # write it somewhere else
    python tools/build_index.py --check           # build it; fail on a clash, or when the committed
                                                  # index.json isn't what the journals make
    python tools/build_index.py --check --ignore-index
                                                  # clashes only (a pull request: the index is
                                                  # rebuilt after merging)

index.json is made here, never by hand. Its format is 2 (the apps' reader, journallibrary._index,
depends on it; fields it doesn't know are ignored by older apps):

    {"format": 2, "generated": "2026-12-01T10:00:00Z", "schema": 1,
     "journals": [{"id", "name", "aliases", "issn", "publisher", "group", "kind", "file", "version",
                   "sha256", "size", "verified", "updated", "needs", "summary", "changes"}]}

sorted by name (ignoring case). "issn" is [print, online] as present; "updated" is the date of the
file's last commit (today while it has changes not committed; else verified.date, else today); "needs" names the publisher's files its LaTeX
template uses (latex.files: the library never holds them); "changes" is the file's "history" when
it has one. Each entry is made by the apps' own code (labapp's journallibrary.index_entry, in the
vendored tools/_labapp) when that copy has it, so the index is what the apps would write; else here.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import common  # noqa: E402
from tools.common import journalfile  # noqa: E402

FORMAT = 2


try:   # (the apps' own index code, when the vendored labapp has it: the index is then exactly what they'd write)
    from tools._labapp import journallibrary as _apps
except ImportError:   # pragma: no cover (an older labapp)
    _apps = None


def entry(path: str, journal: dict, raw: bytes) -> dict:
    ident = journal["id"]
    verified = journal.get("verified") if isinstance(journal.get("verified"), dict) else {}
    history = journal.get("history") if isinstance(journal.get("history"), list) else []
    changes = [{"version": str(h.get("version", "")), "note": str(h.get("note", ""))} for h in history if isinstance(h, dict)]
    updated = common.git_date(path) or (str(verified.get("date"))[:10] if common.date_of(verified.get("date")) else None) \
        or common.today().isoformat()
    rel = f"journals/{os.path.basename(path)}"
    if _apps is not None and hasattr(_apps, "index_entry"):
        e = _apps.index_entry(journal, rel, raw, updated)
        e["changes"] = changes
        return e
    return {
        "id": ident,
        "name": journal["name"],
        "aliases": [a for a in journal.get("aliases") or [] if isinstance(a, str)],
        "issn": common.issns(journal),
        "publisher": journal.get("publisher") or "",
        "group": journal.get("group") or journal.get("publisher") or "",
        "kind": journal.get("kind") or "journal",
        "file": rel,
        "version": journal.get("version") or "1.0.0",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
        "verified": verified.get("status") or "unverified",
        "updated": updated,
        "needs": common.needs(journal),
        "summary": common.summary(journal),
        "changes": changes,
    }


def build(folder: str = common.JOURNALS) -> tuple[dict, list[str]]:
    """(the index, the problems: unreadable files and clashes of id, name or ISSN)."""
    problems: list[str] = []
    entries = []
    by_id: dict[str, str] = {}
    by_name: dict[str, str] = {}
    by_issn: dict[str, str] = {}
    for path in common.journal_files(folder):
        where = f"journals/{os.path.basename(path)}"
        j, raw, why = common.read_journal(path)
        if j is None:
            problems.append(f"{where}: {why}")
            continue
        errors, _ = journalfile.validate(j)
        if errors:
            problems.append(f"{where}: {errors[0]}" + (f" (and {len(errors) - 1} more)" if len(errors) > 1 else ""))
            continue
        e = entry(path, j, raw)
        if e["id"] in by_id:
            problems.append(f"{where}: its id \"{e['id']}\" is also {by_id[e['id']]}'s")
        by_id.setdefault(e["id"], where)
        key = e["name"].casefold()
        if key in by_name:
            problems.append(f"{where}: its name \"{e['name']}\" is also {by_name[key]}'s")
        by_name.setdefault(key, where)
        for n in e["issn"]:
            if n in by_issn:
                problems.append(f"{where}: its ISSN {n} is also {by_issn[n]}'s")
            by_issn.setdefault(n, where)
        entries.append(e)
    entries.sort(key=lambda e: (e["name"].casefold(), e["id"]))
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    index = {"format": FORMAT, "generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "schema": journalfile.FORMAT, "journals": entries}
    return index, problems


def dumps(index: dict) -> str:
    return json.dumps(index, indent=1, ensure_ascii=False) + "\n"


def same(a: dict, b: dict) -> bool:
    """Two indexes alike but for when they were made."""
    return {k: v for k, v in a.items() if k != "generated"} == {k: v for k, v in b.items() if k != "generated"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Make index.json from journals/*.json.")
    ap.add_argument("--check", action="store_true", help="build it and fail on a clash or when index.json is out of date")
    ap.add_argument("--ignore-index", action="store_true", help="with --check: only clashes (don't compare with index.json)")
    ap.add_argument("--out", metavar="FILE", help="where to write it (default index.json; with --check, nothing is written unless given)")
    ap.add_argument("--report", metavar="FILE.md", help="with --check: write the Markdown report the pull request's comment shows")
    ap.add_argument("--journals", metavar="FOLDER", default=common.JOURNALS, help=argparse.SUPPRESS)
    ap.add_argument("--index", metavar="FILE", default=common.INDEX, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    index, problems = build(args.journals)
    for p in problems:
        print(p)
    if args.check:
        bad = bool(problems)
        if not args.ignore_index:
            try:
                with open(args.index, encoding="utf-8") as fh:
                    committed = json.load(fh)
            except (OSError, ValueError):
                committed = None
            if committed is None:
                print("index.json is missing or isn't JSON: run python tools/build_index.py")
                bad = True
            elif not same(committed, index):
                print("index.json isn't what the journal files make: run python tools/build_index.py and commit it")
                bad = True
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(dumps(index))
        said = list(problems)
        if bad and not problems:
            said.append("index.json isn't what the journal files make: run python tools/build_index.py and commit it")
        common.write_report(args.report, "index", "failed" if bad else "passed", [f"error: {p}" for p in said],
                            f"index ({len(index['journals'])} journals: no clash of id, name or ISSN"
                            + ("" if args.ignore_index else "; index.json up to date") + ")" if not bad else "index")
        if not bad:
            print(f"index: {len(index['journals'])} journals, no clashes" + ("" if args.ignore_index else ", index.json up to date"))
        return 1 if bad else 0
    if problems:
        print("index.json not written: fix the problems above first")
        return 1
    out = args.out or args.index
    # (unchanged but for its date: left as it is, so a rebuild with nothing new makes no commit)
    try:
        with open(out, encoding="utf-8") as fh:
            if same(json.load(fh), index):
                print(f"{common.rel(out)}: already up to date ({len(index['journals'])} journals)")
                return 0
    except (OSError, ValueError):
        pass
    with open(out + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(dumps(index))
    os.replace(out + ".tmp", out)
    print(f"{common.rel(out)}: {len(index['journals'])} journals")
    return 0


if __name__ == "__main__":
    sys.exit(main())
