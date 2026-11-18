"""What the library's tools share: where things are, reading journal files, the index's summary
sentence, versions, dates and the Markdown reports each job writes.

Every tool runs as `python tools/<name>.py` from the repository's root (or anywhere: paths are
found from this file). labapp is vendored into tools/_labapp by labapp's own tools/vendor.py;
never edit that copy.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools._labapp import journalfile  # noqa: E402  (after the path is set)

JOURNALS = os.path.join(ROOT, "journals")
INDEX = os.path.join(ROOT, "index.json")
CODEOWNERS = os.path.join(ROOT, ".github", "CODEOWNERS")
ID_FILE = re.compile(r"^journals/([a-z0-9_]{1,40})\.json$")
KB = 1024


def read_journal(path: str) -> tuple[dict | None, bytes, str | None]:
    """(the journal, the file's exact bytes, None) or (None, bytes, what's wrong)."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, b"", f"it can't be read ({exc.strerror or exc})"
    try:
        data = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        return None, raw, "it isn't text (UTF-8)"
    except ValueError as exc:
        return None, raw, f"it isn't valid JSON ({exc})"
    if not isinstance(data, dict):
        return None, raw, "it isn't a journal file: a journal file is a group of fields { }"
    return data, raw, None


def journal_files(folder: str = JOURNALS) -> list[str]:
    """journals/*.json, sorted by file name."""
    if not os.path.isdir(folder):
        return []
    return [os.path.join(folder, n) for n in sorted(os.listdir(folder)) if n.endswith(".json") and not n.startswith(".")]


def rel(path: str) -> str:
    """A path as the repository names it (journals/x.json), else as given."""
    try:
        r = os.path.relpath(os.path.abspath(path), ROOT)
    except ValueError:
        return path
    return path if r.startswith("..") else r.replace(os.sep, "/")


# ISSNs, versions, dates ---------------------------------------------------------------------------------

def issn_form(value) -> str | None:
    """An ISSN as "1234-567X" (hyphenated, upper-case X), or None when it isn't one."""
    if not isinstance(value, str):
        return None
    s = re.sub(r"[\s-]", "", value).upper()
    return f"{s[:4]}-{s[4:]}" if re.fullmatch(r"\d{7}[\dX]", s) else None


def issns(journal: dict) -> list[str]:
    """The journal's ISSNs, print first then online, as present."""
    got = journal.get("issn") or {}
    if not isinstance(got, dict):
        return []
    out = []
    for k in ("print", "online"):
        v = issn_form(got.get(k))
        if v and v not in out:
            out.append(v)
    return out


def semver(value) -> tuple[int, int, int] | None:
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", str(value or ""))
    return tuple(int(x) for x in m.groups()) if m else None   # type: ignore[return-value]


def today() -> datetime.date:
    """Today (LIBRARY_TODAY=YYYY-MM-DD sets it, for the tests)."""
    forced = os.environ.get("LIBRARY_TODAY")
    if forced:
        return datetime.date.fromisoformat(forced)
    return datetime.datetime.now(datetime.timezone.utc).date()


def months_between(a: datetime.date, b: datetime.date) -> int:
    """Whole months from a to b."""
    return (b.year - a.year) * 12 + (b.month - a.month) - (1 if b.day < a.day else 0)


def date_of(value) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def git_date(path: str) -> str | None:
    """The date of the last commit that changed the file (YYYY-MM-DD); today when the file has
    changes not committed yet (it's being added or updated now); None outside git."""
    try:
        status = subprocess.run(["git", "-C", ROOT, "status", "--porcelain", "--", rel(path)], capture_output=True, text=True, timeout=30)
        if status.returncode != 0:
            return None
        if status.stdout.strip():
            return today().isoformat()
        out = subprocess.run(["git", "-C", ROOT, "log", "-1", "--format=%cs", "--", rel(path)],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    got = out.stdout.strip()
    return got if out.returncode == 0 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", got) else None


# The index's sentence -------------------------------------------------------------------------------------

def _mm(v) -> str:
    return f"{float(v):g}"


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def summary(journal: dict) -> str:
    """"figures 89 and 183 mm, up to 247 mm deep; A4; reference style nature"."""
    parts = []
    fig = journal.get("figures") or {}
    widths = []
    for t in fig.get("types") or []:
        w = _mm(t.get("width_mm")) if isinstance(t, dict) and isinstance(t.get("width_mm"), (int, float)) else None
        if w and w not in widths:
            widths.append(w)
    widths.sort(key=float)
    if widths or fig.get("depth_mm"):
        s = "figures"
        if fig.get("any_width") and (fig.get("max_width_mm") or widths):
            s += f" any width up to {_mm(fig.get('max_width_mm') or widths[-1])} mm"
        elif widths:
            s += f" {_and(widths)} mm"
        if fig.get("depth_mm"):
            s += f"{',' if widths else ''} up to {_mm(fig['depth_mm'])} mm deep"
        parts.append(s)
    paper = ((journal.get("page") or {}).get("paper") or "a4").lower()
    parts.append({"a4": "A4", "letter": "US Letter"}.get(paper, paper))
    ref = journal.get("references") or {}
    if ref.get("csl"):
        parts.append(f"reference style {ref['csl']}")
    elif ref.get("csl_text") or ref.get("file"):
        parts.append("its own reference style")
    else:
        parts.append("prose's general reference style")
    return "; ".join(parts)


def needs(journal: dict) -> list[str]:
    """The publisher's files the journal's LaTeX template needs, by name: latex.files and the files
    latex.fetch gets (never the files themselves)."""
    lx = journal.get("latex") or {}
    names = list((lx.get("files") or {}))
    names += list(((lx.get("fetch") or {}).get("files") or {}))
    return list(dict.fromkeys(n for n in names if isinstance(n, str)))


# Reports -------------------------------------------------------------------------------------------------

def write_report(path: str | None, job: str, result: str, lines: list[str], checked: str, details: str = "") -> str:
    """A job's Markdown report: a marker the summary reads (job and result: passed, failed, skipped),
    one line per problem, then "Checked: ...", then any details (Markdown) the comment shows folded."""
    text = f"<!-- job: {job}; result: {result} -->\n"
    text += "".join(f"- {' '.join(ln.split())}\n" for ln in lines)
    text += f"Checked: {checked}\n"
    if details:
        text += "\n" + details.rstrip() + "\n"
    if path:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return text


def read_report(text: str) -> dict:
    """{"job", "result", "lines", "checked"} from a report write_report made."""
    m = re.search(r"<!-- job: ([\w-]+); result: (\w+) -->", text)
    rows = text.splitlines()
    at = next((i for i, ln in enumerate(rows) if ln.startswith("Checked: ")), None)
    lines = [ln[2:] for ln in rows[:at] if ln.startswith("- ")]
    checked = rows[at][len("Checked: "):] if at is not None else ""
    details = "\n".join(rows[at + 1:]).strip() if at is not None else ""
    return {"job": m.group(1) if m else "?", "result": m.group(2) if m else "unknown", "lines": lines, "checked": checked,
            "details": details}
