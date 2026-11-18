#!/usr/bin/env python3
"""Check journal files against the library's rules: what a pull request's "validate" job runs.

    python tools/check_journal.py journals/x.json [more ...] [--strict] [--base REF_OR_FOLDER]
        [--author LOGIN] [--changed-files LIST] [--report REPORT.md]

Each file is checked for:
- the format (labapp's journalfile.validate; --strict counts fields it doesn't know as errors);
- its id being its file name (journals/<id>.json);
- its version raised, when the file was already in the library at --base (a git ref such as
  origin/main, or a folder holding journals/) and has changed;
- "verified" only from a maintainer: verified.status "verified" is refused unless --author is
  in .github/CODEOWNERS (a note when --author isn't given);
- no references.csl_text when references.csl names a style in the CSL repository (and a note when
  a style written in has no CC BY-SA licence);
- sizes: latex.wrapper_text at most 64 KB, no text in it over 64 KB, the file at most 256 KB;
- LaTeX that keeps to the manuscript: latex.class a plain class name, latex.options plain options,
  and latex.wrapper_text without the commands that read or write other files, run programs or
  change how TeX reads (\\openin, \\openout, \\write18, \\directlua, \\catcode, \\input of a
  path outside the manuscript's folder...): the checks compile a contributor's LaTeX, and a
  journal file is data, never a program;
- web addresses only http(s); names (name, aliases, publisher, group) at most 120 characters;
- no other journal in journals/ with the same id, name or ISSN;
- --changed-files (a file listing the pull request's changed paths, one a line, or the paths
  themselves, comma-separated): nothing but journals/*.json, and one journal per pull request;
- limits.checked over 18 months ago: a note (due for re-verification);
- sources on another site than the journal's or publisher's (homepage, guidelines, latex.fetch):
  a warning, for a maintainer to look at.

Errors fail (exit code 1); warnings and notes are said but don't. --report writes the Markdown
report the pull request's comment shows: one line per problem, then "Checked: ...".
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import common  # noqa: E402
from tools.common import journalfile  # noqa: E402

MAX_FILE = 256 * common.KB
MAX_STRING = 64 * common.KB
MAX_WRAPPER = 64 * common.KB
MAX_NAME = 120
# LaTeX a journal's wrapper may not use (the checks compile it, so it must keep to the manuscript's own
# folder): reading or writing other files, running programs, changing how TeX reads its input
TEX_REFUSED = re.compile(r"\\(openin|openout|read|readline|write18|immediate|directlua|latelua|luaexec|ShellEscape|"
                         r"catcode|lccode|uccode|scantokens|everyeof|pdffiledump|filedump|pdfximage|pdfobj|special|"
                         r"verbatiminput|lstinputlisting|includepdf)(?![A-Za-z])")
# \input, \include and graphics may name only a file in the manuscript's folder (no path, no "..")
TEX_FILE_ARG = re.compile(r"\\(input|include|includeonly|includegraphics|includesvg|bibliography|addbibresource|IfFileExists|"
                          r"InputIfFileExists)(?![A-Za-z])\s*"
                          r"(?:\[[^\]]*\])?\s*\{([^}]*)\}")
# (TeX's own form, without braces: \input file)
TEX_BARE_INPUT = re.compile(r"\\(input|include)(?![A-Za-z])\s+([^\s{}\\]+)")
REVERIFY_MONTHS = 18
URL_KEYS = {"url", "page", "homepage", "guidelines"}
# (an address in running text that isn't http(s): "Source data: ..." is words, not an address)
SCHEME = re.compile(r"\b(?:(?:javascript|vbscript):\S|data:[a-z]+/[\w.+-]+[;,]|(?:file|ftps?|sftp|ssh|smb|gopher)://)", re.I)
TWO_LEVEL = {"co.uk", "ac.uk", "org.uk", "gov.uk", "com.au", "edu.au", "org.au", "co.jp", "ac.jp", "or.jp", "com.cn", "edu.cn",
             "ac.cn", "org.cn", "co.nz", "ac.nz", "com.br", "org.br", "co.in", "ac.in", "co.za", "ac.za", "co.kr", "ac.kr"}
# Sites any journal's sources may be on: copies of its pages, the templates' home, articles measured
ANYWHERE = {"web.archive.org", "doi.org", "overleaf.com", "ncbi.nlm.nih.gov", "zotero.org", "github.com", "crossref.org", "issn.org"}


class Problems:
    def __init__(self):
        self.items: list[tuple[str, str, str]] = []   # (level, where, sentence)

    def add(self, level: str, where: str, sentence: str) -> None:
        self.items.append((level, where, sentence))

    def lines(self) -> list[str]:
        order = {"error": 0, "warning": 1, "note": 2}
        return [f"{where}: {level}: {s}" for level, where, s in sorted(self.items, key=lambda x: order[x[0]])]

    @property
    def errors(self) -> int:
        return sum(1 for level, _, _ in self.items if level == "error")


# Helpers ------------------------------------------------------------------------------------------------

def maintainers(path: str = common.CODEOWNERS) -> set[str]:
    """The GitHub logins CODEOWNERS names (lower case), teams left out."""
    out: set[str] = set()
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.split("#", 1)[0]
                for owner in re.findall(r"@([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))(?![/\w-])", line):
                    out.add(owner.lower())
    except OSError:
        pass
    return out


def strings(value, path: str = ""):
    """Every text in a journal, with where it is (legends.sep, sources[2].url)."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from strings(v, f"{path}.{k}" if path else k)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from strings(v, f"{path}[{i + 1}]")


def tex_rules(lx: dict, where: str, probs: "Problems") -> None:
    """latex.class, latex.options and latex.wrapper_text keep to the manuscript (TEX_REFUSED)."""
    cls, opts, text = lx.get("class"), lx.get("options"), lx.get("wrapper_text")
    if isinstance(cls, str) and cls and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,59}", cls):
        probs.add("error", where, f"latex.class is \"{cls[:60]}\"; it can only be a class's name (letters, digits, - and _)")
    if isinstance(opts, str) and re.search(r"[\\{}%#$^~]", opts):
        probs.add("error", where, "latex.options can only list the class's options (no \\, braces, % or #)")
    if not isinstance(text, str) or not text:
        return
    found = sorted({m.group(1) for m in TEX_REFUSED.finditer(text)})
    if found:
        probs.add("error", where, "latex.wrapper_text uses " + ", ".join("\\" + f for f in found) + ", which a journal's LaTeX "
                                  "may not (they read or write other files, run programs or change how TeX reads); the "
                                  "checks compile the wrapper, so it may only set the manuscript out")
    for m in [*TEX_FILE_ARG.finditer(text), *TEX_BARE_INPUT.finditer(text)]:
        for name in (n.strip() for n in m.group(2).split(",")):
            if name.startswith(("/", "~", "\\")) or ".." in name or ":" in name:
                probs.add("error", where, f"latex.wrapper_text: \\{m.group(1)}{{{name[:60]}}} names a file outside the "
                                          "manuscript's folder; a wrapper may only use the manuscript's own files")


def site(url: str) -> str | None:
    """The site an address is on (its registrable domain: academic.oup.com -> oup.com), or None.
    An archived copy (web.archive.org/web/<date>/<address>) is the site it copies."""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    host = (p.hostname or "").lower().rstrip(".")
    if host == "web.archive.org":
        m = re.match(r"/web/[^/]+/(https?://.+)$", p.path + (f"?{p.query}" if p.query else ""))
        if m:
            return site(m.group(1))
    parts = host.split(".")
    if len(parts) < 2:
        return host or None
    n = 3 if ".".join(parts[-2:]) in TWO_LEVEL and len(parts) >= 3 else 2
    return ".".join(parts[-n:])


def base_copy(base: str, name: str) -> tuple[bytes | None, str | None]:
    """(the file as it is at base, None) or (None, why it couldn't be read); (None, None) when it
    isn't there (a new journal)."""
    if os.path.isdir(base):
        for p in (os.path.join(base, "journals", name), os.path.join(base, name)):
            if os.path.isfile(p):
                with open(p, "rb") as fh:
                    return fh.read(), None
        return None, None
    try:
        out = subprocess.run(["git", "-C", common.ROOT, "show", f"{base}:journals/{name}"], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"git couldn't be run ({exc})"
    if out.returncode == 0:
        return out.stdout, None
    err = out.stderr.decode("utf-8", "replace")
    if "does not exist" in err or "exists on disk, but not in" in err:
        return None, None
    return None, f"{base} isn't a folder or a git ref this copy has ({err.strip().splitlines()[-1] if err.strip() else 'git said no'})"


def changed_paths(arg: str) -> list[str]:
    if os.path.isfile(arg):
        with open(arg, encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = arg
    return [p.strip() for p in re.split(r"[\n,]", text) if p.strip()]


# The checks ---------------------------------------------------------------------------------------------

def check_changed(paths: list[str], probs: Problems, maintainer: bool = False) -> None:
    """Only journal files, one a pull request. A maintainer's pull request may change the library's
    other files too (its tools, workflows, README): a note, as a maintainer reviews those by hand."""
    journals = [p for p in paths if common.ID_FILE.match(p)]
    others = [p for p in paths if not common.ID_FILE.match(p)]
    if others and maintainer:
        probs.add("note", "pull request", f"it changes {len(others)} file(s) besides journals ({', '.join(others[:5])}"
                                          f"{'…' if len(others) > 5 else ''}), as a maintainer may; those are reviewed by hand")
    for p in others if not maintainer else []:
        probs.add("error", p, "a pull request to the library may change only journal files (journals/<id>.json); "
                              "this one changes another file, which a maintainer does by hand")
    if len(journals) > 1:
        probs.add("error", "pull request", f"it changes {len(journals)} journal files ({', '.join(journals)}); "
                                           "send one journal per pull request")


def check_file(path: str, probs: Problems, *, strict: bool, base: str | None, author: str | None, owners: set[str],
               others_folder: str) -> dict | None:
    where = common.rel(path)
    name = os.path.basename(path)
    j, raw, why = common.read_journal(path)
    if raw and len(raw) > MAX_FILE:
        probs.add("error", where, f"the file is {len(raw) / common.KB:.0f} KB; a journal file may be at most 256 KB")
    if j is None:
        probs.add("error", where, why or "it can't be read")
        return None
    # 1. The format
    errors, warnings = journalfile.validate(j)
    for e in errors:
        probs.add("error", where, e)
    for w in warnings:
        probs.add("error" if strict else "note", where, w)
    ident = j.get("id")
    # 2. Its id is its file name
    if not name.endswith(".json") or name[:-5] != ident:
        probs.add("error", where, f"its id is \"{ident}\", so the file has to be called journals/{ident}.json")
    # 3. Sizes
    lx = j.get("latex") if isinstance(j.get("latex"), dict) else {}
    wrapper = lx.get("wrapper_text")
    if isinstance(wrapper, str) and len(wrapper.encode("utf-8")) > MAX_WRAPPER:
        probs.add("error", where, f"latex.wrapper_text is {len(wrapper.encode('utf-8')) / common.KB:.0f} KB; "
                                  "a LaTeX wrapper may be at most 64 KB (a publisher's files never go in a journal file)")
    tex_rules(lx, where, probs)
    for at, s in strings(j):
        if at != "latex.wrapper_text" and len(s.encode("utf-8")) > MAX_STRING:
            probs.add("error", where, f"{at} is {len(s.encode('utf-8')) / common.KB:.0f} KB of text; no text in a journal file "
                                      "may be over 64 KB (a publisher's files never go in a journal file)")
    # 4. The reference style
    ref = j.get("references") if isinstance(j.get("references"), dict) else {}
    if ref.get("csl_text") is not None:
        if ref.get("csl"):
            probs.add("error", where, f"references.csl_text: the style \"{ref['csl']}\" is in the CSL repository, which prose "
                                      "downloads it from; leave references.csl_text out")
        elif isinstance(ref["csl_text"], str) and not re.search(r"creativecommons\.org/licenses/by-sa|CC BY-SA|Attribution-ShareAlike",
                                                                ref["csl_text"], re.I):
            probs.add("note", where, "references.csl_text: the style written in doesn't carry the CSL repository's licence "
                                     "(CC BY-SA); a maintainer checks it's the author's own")
    # 5. The badge
    status = (j.get("verified") or {}).get("status") if isinstance(j.get("verified"), dict) else None
    if status == "verified":
        if author is None:
            probs.add("note", where, "verified.status is \"verified\"; who sent it wasn't given (--author), so that wasn't checked")
        elif author.lower() not in owners:
            probs.add("error", where, f"verified.status is \"verified\", which only a maintainer sets; {author} isn't one, "
                                      "so set it to \"community\"")
    # 6. Web addresses and names
    for at, s in strings(j):
        last = re.sub(r"\[\d+\]$", "", at.rsplit(".", 1)[-1])
        if at.startswith(("latex.wrapper_text", "references.csl_text")):
            continue
        if last in URL_KEYS and s and not re.match(r"https?://", s, re.I):
            probs.add("error", where, f"{at} is \"{s[:80]}\"; a web address in a journal file starts with http:// or https://")
        elif SCHEME.search(s) and last not in URL_KEYS:
            probs.add("error", where, f"{at} holds an address that isn't http(s) (\"{SCHEME.search(s).group(0)}\"); "
                                      "only http:// and https:// addresses are allowed")
    named = [("name", j.get("name")), ("publisher", j.get("publisher")), ("group", j.get("group"))]
    named += [(f"aliases[{i + 1}]", a) for i, a in enumerate(j.get("aliases") or [])]
    for at, s in named:
        if isinstance(s, str) and len(s) > MAX_NAME:
            probs.add("error", where, f"{at} is {len(s)} characters long; names may be at most {MAX_NAME}")
    # 7. The same journal twice
    mine_issn = set(common.issns(j))
    for other in common.journal_files(others_folder):
        if os.path.basename(other) == name:
            continue   # (this file, or the library's copy of it: the one being changed)
        o, _, _ = common.read_journal(other)
        if not o:
            continue
        oname = f"journals/{os.path.basename(other)}"
        if o.get("id") == ident:
            probs.add("error", where, f"its id \"{ident}\" is already {oname}'s")
        if isinstance(o.get("name"), str) and isinstance(j.get("name"), str) and o["name"].casefold() == j["name"].casefold():
            probs.add("error", where, f"the library already has a journal called {o['name']} ({oname})")
        for n in sorted(mine_issn & set(common.issns(o))):
            probs.add("error", where, f"its ISSN {n} is already {o.get('name')}'s ({oname})")
    # 8. Its version
    if base:
        before, why = base_copy(base, name)
        if why:
            probs.add("error", where, f"its version couldn't be compared with the library's copy: {why}")
        elif before is not None and before != raw:
            try:
                old = json.loads(before.decode("utf-8"))
            except ValueError:
                old = {}
            was, now = common.semver(old.get("version", "1.0.0")), common.semver(j.get("version", "1.0.0"))
            if was and now and now <= was:
                probs.add("error", where, f"it changes the library's copy but its version is still {j.get('version', '1.0.0')}; "
                                          f"raise it above {old.get('version', '1.0.0')} (the last number for a correction, "
                                          "the middle one for an addition)")
    # 9. Due for re-verification
    checked = common.date_of((j.get("limits") or {}).get("checked")) if isinstance(j.get("limits"), dict) else None
    if checked and common.months_between(checked, common.today()) > REVERIFY_MONTHS:
        probs.add("note", where, f"limits.checked is {checked.isoformat()}, over {REVERIFY_MONTHS} months ago: "
                                 "the journal is due to be checked against its guidelines again")
    # 10. Where its facts come from
    home = [j.get("homepage"), j.get("guidelines"), (lx.get("fetch") or {}).get("page"), (lx.get("fetch") or {}).get("url")]
    sites = {site(u) for u in home if isinstance(u, str) and u} - {None}
    sources = [s.get("url") for s in j.get("sources") or [] if isinstance(s, dict) and isinstance(s.get("url"), str)]
    if sources and not (j.get("homepage") or j.get("guidelines")):
        probs.add("note", where, "it has no homepage or guidelines address, so where its sources are wasn't compared with them")
    elif sources:
        off = sorted({site(u) for u in sources if site(u) not in sites and site(u) not in ANYWHERE and
                      (urllib.parse.urlsplit(u).hostname or "") not in ANYWHERE} - {None})
        if off:
            probs.add("warning", where, f"sources are on {', '.join(off)}, not the journal's or publisher's own site "
                                        f"({', '.join(sorted(sites))}); a maintainer checks they're the journal's pages")
    return j


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check journal files against the library's rules.")
    ap.add_argument("files", nargs="*", metavar="FILE")
    ap.add_argument("--strict", action="store_true", help="count fields the format doesn't know as errors (the library does)")
    ap.add_argument("--base", metavar="REF_OR_FOLDER", help="the library before the change: a git ref or a folder with journals/")
    ap.add_argument("--author", metavar="LOGIN", help="who sent the change (their GitHub login)")
    ap.add_argument("--codeowners", metavar="FILE", default=common.CODEOWNERS, help=argparse.SUPPRESS)
    ap.add_argument("--journals", metavar="FOLDER", default=common.JOURNALS, help="the other journals (default journals/)")
    ap.add_argument("--changed-files", metavar="LIST", help="the pull request's changed paths: a file, one a line, or comma-separated")
    ap.add_argument("--report", metavar="FILE.md", help="write the Markdown report here")
    args = ap.parse_args(argv)
    if not args.files and not args.changed_files:
        ap.error("give one or more journal files (or --changed-files)")
    probs = Problems()
    files = list(args.files)
    if args.changed_files:
        paths = changed_paths(args.changed_files)
        check_changed(paths, probs, maintainer=bool(args.author) and args.author.lower() in maintainers(args.codeowners))
        if not files:   # (the changed journals that are still there; a deleted one has nothing to check)
            files = [os.path.join(common.ROOT, p) for p in paths if common.ID_FILE.match(p) and os.path.isfile(os.path.join(common.ROOT, p))]
    owners = maintainers(args.codeowners)
    names = []
    for f in files:
        j = check_file(f, probs, strict=args.strict, base=args.base, author=args.author, owners=owners, others_folder=args.journals)
        names.append(f"{common.rel(f)}" + (f" ({j.get('name')})" if j else ""))
    lines = probs.lines()
    for ln in lines:
        print(ln)
    what = ["schema" + (" (strict)" if args.strict else ""), "id = file name", "sizes", "addresses and names", "duplicates",
            "reference style", "badge" + ("" if args.author else " (no author given)")]
    if args.base:
        what.append(f"version against {args.base}")
    if args.changed_files:
        what.append("changed files")
    checked = f"{', '.join(what)}; {', '.join(names) if names else 'no journal file'}"
    result = "failed" if probs.errors else "passed"
    common.write_report(args.report, "validate", result, lines, checked)
    if not probs.errors:
        print(f"fine: {', '.join(names) if names else 'nothing to check'}")
    return 1 if probs.errors else 0


if __name__ == "__main__":
    sys.exit(main())
