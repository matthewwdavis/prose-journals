#!/usr/bin/env python3
"""Copy the apps' built-in journals into the library, as Verified journals.

    python tools/sync_builtin.py                    # from labapp's builtin_journals: LABAPP_SRC, else ../labapp
    python tools/sync_builtin.py --from FOLDER      # from another copy of builtin_journals
    python tools/sync_builtin.py --check            # only say what would change

Every file in the folder whose "kind" is "journal" is copied to journals/<id>.json. The apps' other
built-in files (General, Cover Letter, the UC Davis dissertation, the in-house bioRxiv template)
stay built into the apps. On the way in, the library's rules (CONTRIBUTING.md) are applied:

- verified.status is "verified" (they are Matthew's, checked against each journal's guidelines);
- references.csl_text is dropped when references.csl names a style in the CSL repository;
- nothing but the journal file is copied: never a publisher's files (latex.files lists names only).

labapp's journal.schema.json beside the folder is copied to schema/journal.schema.json too.
Rebuild the index afterwards: python tools/build_index.py
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import common  # noqa: E402
from tools.common import journalfile  # noqa: E402

NOT_JOURNALS = {"index.json", "library_index.json"}   # (labapp's list of its journals, and its copy of the library's)


def defaults() -> list[str]:
    """Where labapp's built-in journals usually are: LABAPP_SRC (a labapp checkout), else a labapp
    checkout beside the library's folder."""
    roots = [os.environ["LABAPP_SRC"]] if os.environ.get("LABAPP_SRC") else []
    roots.append(os.path.join(os.path.dirname(common.ROOT), "labapp"))
    return [os.path.join(os.path.expanduser(r), "labapp", "builtin_journals") for r in roots]


def library_copy(journal: dict) -> dict:
    """A built-in journal as the library keeps it."""
    j = json.loads(json.dumps(journal))
    v = j.get("verified") if isinstance(j.get("verified"), dict) else {}
    j["verified"] = {**v, "status": "verified"}
    ref = j.get("references")
    if isinstance(ref, dict) and ref.get("csl") and "csl_text" in ref:
        del ref["csl_text"]
    return j


def sync(source: str, dest: str = common.JOURNALS, check: bool = False) -> tuple[list[str], int]:
    """(what was done, one line each; how many problems)."""
    said, bad = [], 0
    names = sorted(n for n in os.listdir(source) if n.endswith(".json") and n not in NOT_JOURNALS)
    os.makedirs(dest, exist_ok=True)
    seen = set()
    for name in names:
        j, _, why = common.read_journal(os.path.join(source, name))
        if j is None:
            said.append(f"{name}: skipped: {why}")
            bad += 1
            continue
        if j.get("kind", "journal") != "journal":
            continue
        out = library_copy(j)
        errors, warnings = journalfile.validate(out)
        if errors or warnings:
            said.append(f"{name}: not copied: {'; '.join(errors + warnings)}")
            bad += 1
            continue
        lx = out.get("latex") or {}
        if len((lx.get("wrapper_text") or "").encode("utf-8")) > 64 * common.KB:
            said.append(f"{name}: not copied: its latex.wrapper_text is larger than 64 KB")
            bad += 1
            continue
        text = journalfile.dumps(out)
        target = os.path.join(dest, f"{out['id']}.json")
        seen.add(os.path.basename(target))
        before = open(target, encoding="utf-8").read() if os.path.isfile(target) else None
        if before == text:
            continue
        if before is not None:
            old = json.loads(before)
            raised = (common.semver(out.get("version", "1.0.0")) or (0, 0, 0)) > (common.semver(old.get("version", "1.0.0")) or (0, 0, 0))
            note = "" if raised else (f" (its version is still {out.get('version', '1.0.0')}: raise it in labapp's file so "
                                      "copies already fetched are updated)")
            said.append(f"{out['id']}: {'would be ' if check else ''}updated{note}")
        else:
            said.append(f"{out['id']}: {'would be ' if check else ''}added")
        if not check:
            with open(target + ".tmp", "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(target + ".tmp", target)
    for name in sorted(os.listdir(dest)):
        mine = name.endswith(".json") and name not in seen and \
            ((common.read_journal(os.path.join(dest, name))[0] or {}).get("verified") or {}).get("by") == "prose (built in)"
        if mine:   # (a journal that came from the apps and no longer is in them; community journals are left out)
            said.append(f"{name[:-5]}: in the library but not a built-in journal in {source}; left as it is")
    schema = os.path.join(os.path.dirname(os.path.abspath(source)), "journal.schema.json")
    if os.path.isfile(schema):
        target = os.path.join(common.ROOT, "schema", "journal.schema.json")
        if not os.path.isfile(target) or open(schema, "rb").read() != open(target, "rb").read():
            said.append(f"schema/journal.schema.json: {'would be ' if check else ''}updated from {schema}")
            if not check:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copyfile(schema, target)
    return said, bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Copy the apps' built-in journals into journals/ as Verified journals.")
    ap.add_argument("--from", dest="source", metavar="FOLDER", help="a builtin_journals folder (default: labapp's)")
    ap.add_argument("--check", action="store_true", help="only say what would change")
    args = ap.parse_args(argv)
    source = os.path.expanduser(args.source) if args.source else next((d for d in defaults() if os.path.isdir(d)), None)
    if not source or not os.path.isdir(source):
        print(f"No built-in journals folder at {args.source or ' or '.join(defaults())}: give one with --from FOLDER")
        return 1
    said, bad = sync(source, check=args.check)
    for line in said:
        print(line)
    if not said:
        print(f"journals/ is the same as {source}")
    elif not args.check:
        print("Now rebuild the index: python tools/build_index.py")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
