"""The journal library both apps read: journal files shared in one place (a GitHub repository, a
folder or a web address), found by name, ISSN or alias, fetched the first time a journal is used and
kept in a cache both apps share, so each journal is set up once for everyone.

    search(query)              built-in journals, your own and the library's, best match first
    fetch(id, version)         a library journal: from the cache, else downloaded, its checksum and
                               contents checked, and kept (journals/<id>@<version>.json)
    resolve(id, version)       what a document written for that journal (and version) should use
    refresh(source)            the library's list read again (at most once a day unless forced);
                               newer versions of journals already fetched are fetched too
    share_by_pull_request(...) a journal offered to the library as a pull request, through gh

The library is named in one of four forms:

    github:owner/repository[@branch]
                                a GitHub repository (its default branch, or the one named): read
                                straight from raw.githubusercontent.com when it's public (no gh
                                needed), else with GitHub's own tool, gh, signed in as the person
                                (so a private library works for whoever its owner gave access);
                                shared to by pull request, or written in by those who can change it
    https://…/                  a web address holding index.json and the files: read only
    file:///a/folder, /a/folder a folder (a lab's shared drive; and the tests' fake library)

Its layout: index.json ({"format": 2, "journals": [{"id", "name", "aliases", "issn", "publisher",
"group", "file", "version", "sha256", "size", "verified", "updated", "needs", "summary",
"changes"}]}; format 1, from older prose, has only id, name, file, summary, needs, added) and the
journal files it names (journals/<id>.json).

The cache (cache_root(): beside the list of your own journals, so prose and mosaic share it):

    index.json                  {"source", "read": ISO time, "index": the library's index.json}
    journals/<id>@<version>.json  every journal fetched, each version kept while something uses it
    used.json                   {id: {version: last day a document used it}}: what prune() keeps

Versions are x.y.z: a patch (z) corrects, a minor (y) adds, a major (x) would change documents
already written. A document keeps the major version it was written with ("journal_version") until
the person updates it; patches and minors arrive by themselves.

The core: the journals each app ships (builtin_journals/index.json, "core": true). SHIP_ALL is the
switch: True (today) ships every built-in journal; False ships only the core, and the rest are
fetched from the library on first use like any other (journalfile.builtin() then also lists the
fetched ones). Moving a journal out of the core is then a change to index.json, not to code.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from . import journalfile

DEFAULT_SOURCE = "github:matthewwdavis/prose-journals@main"   # the public library: its main branch
SNAPSHOT = os.path.join(journalfile.BUILTIN_DIR, "library_index.json")   # the library's list when the app was made
READ_THROUGH_GH = False  # True: a GitHub library is read through gh first (for a private one); False: from
                         # raw.githubusercontent.com (public), with gh only when the file isn't there (a private one)
RAW_GITHUB = "https://raw.githubusercontent.com"   # (LABAPP_GITHUB_RAW: a folder laid out the same way, for tests)
SHIP_ALL = True          # every built-in journal is shipped; False: only the core ("core": true in builtin_journals/index.json)
DAY = 24 * 3600
MAX_FILE = 256 * 1024    # (the library's own limit for a journal file)
INDEX_FORMAT = 2


class LibraryError(ValueError):
    """The library can't be reached, read or changed; the message says why, for the person."""


class OfflineError(LibraryError):
    """The library couldn't be reached (no connection, or it's down)."""


# The core ----------------------------------------------------------------------------------------------

def ship_all() -> bool:
    """Whether every built-in journal is shipped (SHIP_ALL; LABAPP_CORE_ONLY=1 tries the small core)."""
    if os.environ.get("LABAPP_CORE_ONLY", "").lower() in ("1", "true", "yes"):
        return False
    return SHIP_ALL


def core_ids() -> list[str]:
    """The journals kept in the apps whatever happens: builtin_journals/index.json's "core" ones."""
    return [j["id"] for j in journalfile.builtin_index()["journals"] if j.get("core")]


def shipped_ids() -> list[str]:
    """The built-in journals this copy of the app ships: all of them, or only the core."""
    items = journalfile.builtin_index()["journals"]
    return [j["id"] for j in items if ship_all() or j.get("core")]


# Where the library is ----------------------------------------------------------------------------------

def kind_of(source: str) -> str:
    s = (source or "").strip()
    if s.startswith("github:"):
        return "github"
    if s.startswith("https://"):
        return "web"
    if s.startswith("http://"):
        raise LibraryError("A library on the web needs a secure address (https://).")
    if s.startswith("file://"):
        return "folder"
    if not s:
        raise LibraryError("No library is set: choose one in Settings.")
    return "folder"


def folder_of(source: str) -> str:
    s = source.strip()
    if s.startswith("file://"):
        return urllib.parse.unquote(urllib.parse.urlparse(s).path)
    return os.path.expanduser(s)


def github_of(source: str) -> tuple[str, str | None]:
    """("owner/repository", the branch or None for the default one) of a github: library."""
    s = source.strip()[7:].strip().strip("/")
    repo, _, ref = s.partition("@")
    return repo.strip("/"), (ref.strip() or None)


def describe(source: str) -> str:
    k = kind_of(source)
    if k == "github":
        return f"the GitHub repository {github_of(source)[0]}"
    return source if k == "web" else f"the folder {folder_of(source)}"


# GitHub, through gh ------------------------------------------------------------------------------------

def gh_path() -> str | None:
    """GitHub's command-line tool (LABAPP_GH: another one, for tests)."""
    if os.environ.get("LABAPP_GH"):
        return os.environ["LABAPP_GH"]
    found = shutil.which("gh")
    if found:
        return found
    for p in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh", "/usr/bin/gh", os.path.expanduser("~/.local/bin/gh"),
              r"C:\Program Files\GitHub CLI\gh.exe"):
        if os.path.isfile(p):
            return p
    return None


_OFFLINE_WORDS = ("could not resolve", "no such host", "dial tcp", "connection refused", "network is unreachable",
                  "timeout", "timed out", "error connecting")


def gh_api(args: list[str], stdin: str | None = None) -> dict | list:
    """gh api's answer, parsed. FileNotFoundError for a 404; OfflineError when GitHub can't be
    reached; LibraryError, in words, for the rest."""
    gh = gh_path()
    if not gh:
        raise LibraryError("This library is a GitHub repository, read with GitHub's tool “gh”, which isn't installed here. "
                           "Install it (cli.github.com) and sign in with “gh auth login”, or set a folder as the library in Settings.")
    hidden = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        out = subprocess.run([gh, "api", *args], input=stdin, capture_output=True, text=True, timeout=60, **hidden)
    except subprocess.TimeoutExpired:
        raise OfflineError("GitHub didn't answer in time.") from None
    except OSError as exc:
        raise LibraryError(f"GitHub's tool couldn't be run ({exc}).") from None
    if out.returncode != 0:
        err = (out.stderr or out.stdout).strip()
        low = err.lower()
        if "404" in err or "not found" in low:
            raise FileNotFoundError(err)
        if any(w in low for w in _OFFLINE_WORDS):
            raise OfflineError("GitHub can't be reached: is this computer online?")
        if "401" in err or "gh auth login" in low or "authentication" in low:
            raise LibraryError("GitHub's tool isn't signed in: run “gh auth login” in a terminal.")
        if "403" in err:
            raise LibraryError("Your GitHub account can't change this library; ask its owner for access.")
        raise LibraryError(f"GitHub said: {err[:300]}")
    try:
        return json.loads(out.stdout or "{}")
    except ValueError:
        return {}


# Reading, whatever the library is ----------------------------------------------------------------------

def safe(rel: str) -> str:
    """A path inside the library, or LibraryError (no "..", no hidden files, no absolute paths)."""
    rel = str(rel or "")
    parts = rel.replace("\\", "/").split("/")
    if not rel or any(p in ("", ".", "..") or p.startswith(".") for p in parts) or len(rel) > 200 or ":" in rel:
        raise LibraryError("The library lists a file the app won't read.")
    return "/".join(parts)


def _http(url: str, limit: int) -> bytes:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "labapp-journal-library"})
        with urllib.request.urlopen(req, timeout=30) as resp:   # noqa: S310 (https only: kind_of)
            return resp.read(limit + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise FileNotFoundError(url) from None
        raise LibraryError(f"The library answered {exc.code} for {url}.") from None
    except (OSError, ValueError) as exc:
        raise OfflineError(f"The library couldn't be reached ({getattr(exc, 'reason', exc)}).") from None


def read(source: str, rel: str, limit: int = 20_000_000) -> bytes:
    """One of the library's files. FileNotFoundError when it isn't there."""
    rel = safe(rel)
    k = kind_of(source)
    if k == "github":
        repo, ref = github_of(source)
        if READ_THROUGH_GH and gh_path():
            return _read_through_gh(repo, ref, rel, limit)
        try:
            return _raw(repo, ref, rel, limit)
        except FileNotFoundError:
            # (not there, or the repository is private: raw.githubusercontent.com answers 404 for both;
            # gh, signed in, can read a private one the person was given access to)
            if not gh_path():
                raise
            return _read_through_gh(repo, ref, rel, limit)
    if k == "web":
        return _http(source.rstrip("/") + "/" + rel, limit)
    root = folder_of(source)
    if not os.path.isdir(root):
        raise OfflineError(f"The library's folder isn't there (is its drive connected?): {root}")
    with open(os.path.join(root, *rel.split("/")), "rb") as fh:
        return fh.read(limit + 1)


def _raw(repo: str, ref: str | None, rel: str, limit: int) -> bytes:
    """A public GitHub repository's file, from raw.githubusercontent.com (or LABAPP_GITHUB_RAW's folder)."""
    base = os.environ.get("LABAPP_GITHUB_RAW") or RAW_GITHUB
    where = "/".join([repo, ref or "HEAD", rel])
    if not base.startswith("https://"):   # (a folder: the tests' stand-in for raw.githubusercontent.com)
        with open(os.path.join(folder_of(base), *where.split("/")), "rb") as fh:
            return fh.read(limit + 1)
    return _http(f"{base.rstrip('/')}/{where}", limit)


def _read_through_gh(repo: str, ref: str | None, rel: str, limit: int) -> bytes:
    got = gh_api([f"repos/{repo}/contents/{rel}" + (f"?ref={urllib.parse.quote(ref)}" if ref else "")])
    if not isinstance(got, dict):
        raise LibraryError(f"{rel} in the library is a folder, not a file.")
    if got.get("content") is None and got.get("download_url"):   # (a file over 1 MB)
        return _http(got["download_url"], limit)
    return base64.b64decode(got.get("content") or "")


# The index ---------------------------------------------------------------------------------------------

def _issns(value) -> list[str]:
    if isinstance(value, dict):
        value = [value.get("print"), value.get("online")]
    if isinstance(value, str):
        value = [value]
    out = []
    for v in value or []:
        v = str(v or "").strip().upper()
        if re.fullmatch(r"\d{4}-?\d{3}[\dX]", v):
            v = v if "-" in v else v[:4] + "-" + v[4:]
            if v not in out:
                out.append(v)
    return out


def _version(v) -> str:
    return v if isinstance(v, str) and re.fullmatch(r"\d+\.\d+\.\d+", v) else "1.0.0"


def normalise(entry: dict) -> dict | None:
    """One index entry, in format 2's shape whatever format it came in; None if it's unusable."""
    if not isinstance(entry, dict) or not entry.get("id") or not entry.get("file"):
        return None
    ident = str(entry["id"])
    if not re.fullmatch(r"[a-z0-9_]{1,40}", ident):
        return None
    verified = entry.get("verified")
    if isinstance(verified, dict):
        verified = verified.get("status")
    return {"id": ident, "name": str(entry.get("name") or ident), "aliases": [str(a) for a in entry.get("aliases") or [] if a],
            "issn": _issns(entry.get("issn")), "publisher": str(entry.get("publisher") or ""),
            "group": str(entry.get("group") or entry.get("publisher") or ""), "kind": str(entry.get("kind") or "journal"),
            "file": str(entry["file"]), "version": _version(entry.get("version")),
            "sha256": str(entry["sha256"]).lower() if entry.get("sha256") else None, "size": entry.get("size"),
            "verified": verified if verified in ("verified", "community", "unverified") else "community",
            "updated": str(entry.get("updated") or entry.get("added") or ""), "needs": [str(n) for n in entry.get("needs") or []],
            "summary": str(entry.get("summary") or ""),
            "changes": [c for c in entry.get("changes") or [] if isinstance(c, dict)],
            "older": [o for o in entry.get("older") or [] if isinstance(o, dict) and o.get("file") and o.get("version")]}


def parse_index(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise LibraryError("The library's list (index.json) can't be read.") from None
    if not isinstance(data, dict):
        raise LibraryError("The library's list (index.json) can't be read.")
    items = [n for n in (normalise(j) for j in data.get("journals") or []) if n]
    return {"format": data.get("format", 1), "generated": data.get("generated", ""), "journals": items}


def read_index(source: str) -> dict:
    """The library's index.json, normalised ({"format", "generated", "journals": [...]}); an empty
    list when it has none yet."""
    try:
        return parse_index(read(source, "index.json"))
    except FileNotFoundError:
        return {"format": INDEX_FORMAT, "generated": "", "journals": []}


def summary_of(journal: dict) -> str:
    """A journal in a line: "figures 89 and 183 mm, up to 247 mm deep; A4; reference style nature"."""
    j = journalfile.complete(journal)
    parts = []
    fig = j.get("figures") or {}
    if fig.get("types"):
        widths = [f"{t['width_mm']:g}" for t in fig["types"]]
        w = widths[0] if len(widths) == 1 else ", ".join(widths[:-1]) + " and " + widths[-1]
        parts.append(f"figures {w} mm" + (f", up to {fig['depth_mm']:g} mm deep" if fig.get("depth_mm") else ""))
    parts.append({"letter": "US Letter", "a4": "A4"}.get(j["page"]["paper"], str(j["page"]["paper"])))
    csl = (j.get("references") or {}).get("csl")
    if csl:
        parts.append(f"reference style {csl}")
    lx = j.get("latex") or {}
    if lx.get("class"):
        parts.append(f"LaTeX in the publisher's class {lx['class']}")
    return "; ".join(parts)


def index_entry(journal: dict, rel: str, data: bytes, updated: str | None = None) -> dict:
    """The index's entry for one journal file (rel: its path in the library; data: its bytes)."""
    issn = journal.get("issn") or {}
    return {"id": journal["id"], "name": journal["name"], "aliases": list(journal.get("aliases") or []),
            "issn": _issns(issn), "publisher": journal.get("publisher", ""), "group": journal.get("group") or journal.get("publisher", ""),
            "kind": journal.get("kind", "journal"), "file": rel, "version": _version(journal.get("version")),
            "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
            "verified": (journal.get("verified") or {}).get("status", "unverified"),
            "updated": updated or datetime.date.today().isoformat(),
            "needs": sorted((journal.get("latex") or {}).get("files") or {}), "summary": summary_of(journal), "changes": []}


def write_index(journals: list[dict]) -> bytes:
    items = sorted(journals, key=lambda j: (str(j.get("name", "")).lower(), j.get("id", "")))
    data = {"format": INDEX_FORMAT, "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "schema": journalfile.FORMAT, "journals": items}
    return (json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


# The cache both apps share ------------------------------------------------------------------------------

def cache_root() -> str:
    """The shared cache: LABAPP_LIBRARY_CACHE (tests), else "library" beside the list of your own
    journals (so prose and mosaic share it)."""
    if os.environ.get("LABAPP_LIBRARY_CACHE"):
        return os.environ["LABAPP_LIBRARY_CACHE"]
    from . import journals
    return os.path.join(os.path.dirname(journals.shared_user_file()), "library")


def _root(cache: str | None) -> str:
    return cache or cache_root()


def _write_atomic(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path))
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def cached_index(cache: str | None = None) -> dict | None:
    """{"source", "read", "index"} as last read, or None."""
    try:
        with open(os.path.join(_root(cache), "index.json"), encoding="utf-8") as fh:
            got = json.load(fh)
        got["index"] = parse_index(json.dumps(got.get("index") or {}).encode("utf-8"))
        return got
    except (OSError, ValueError, LibraryError, AttributeError):
        return None


def snapshot() -> dict:
    """The library's list as it was when this copy of the app was made (search works offline from
    the first start)."""
    try:
        with open(SNAPSHOT, "rb") as fh:
            return parse_index(fh.read())
    except (OSError, LibraryError):
        return {"format": INDEX_FORMAT, "generated": "", "journals": []}


def library_entries(cache: str | None = None, source: str | None = None) -> list[dict]:
    """The library's journals as last known: the cached list (when it's of this library), else the
    shipped snapshot."""
    got = cached_index(cache)
    if got and (source is None or got.get("source") == source):
        return got["index"]["journals"]
    return snapshot()["journals"]


def refresh(source: str = DEFAULT_SOURCE, cache: str | None = None, force: bool = False, max_age: float = DAY,
            update: bool = True) -> dict:
    """Read the library's list again when the last read is older than max_age (a day) or force, and
    fetch newer versions of journals already fetched. {"read": bool, "offline": bool, "message",
    "updated": [ids]}. Never raises for a missing connection: the cached list stays."""
    got = cached_index(cache)
    if got and not force and got.get("source") == source:
        try:
            age = (_now() - datetime.datetime.fromisoformat(got["read"].replace("Z", "+00:00"))).total_seconds()
        except (KeyError, ValueError, AttributeError):
            age = max_age + 1
        if age < max_age:
            return {"read": False, "offline": False, "message": "", "updated": []}
    try:
        index = read_index(source)
    except OfflineError as exc:
        return {"read": False, "offline": True, "message": str(exc), "updated": []}
    _write_atomic(os.path.join(_root(cache), "index.json"),
                  json.dumps({"source": source, "read": _now().strftime("%Y-%m-%dT%H:%M:%SZ"), "index": index},
                             indent=1, ensure_ascii=False).encode("utf-8"))
    updated = []
    if update:
        for ident, versions in cached_versions_all(cache).items():
            entry = next((e for e in index["journals"] if e["id"] == ident), None)
            if entry and newer(entry["version"], versions[-1]):
                try:
                    _download(entry, source, cache)
                    updated.append(ident)
                except LibraryError:
                    pass
    return {"read": True, "offline": False, "message": "", "updated": updated}


# Versions ----------------------------------------------------------------------------------------------

def parse_version(v: str) -> tuple[int, int, int]:
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", str(v or ""))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (0, 0, 0)


def major(v) -> int:
    if isinstance(v, int):
        return v
    s = str(v or "")
    return int(s) if s.isdigit() else parse_version(s)[0]


def newer(a: str, b: str) -> bool:
    return parse_version(a) > parse_version(b)


def pick(versions: list[str], want=None) -> str | None:
    """The version to use of those kept: the newest, or the newest of want's major version."""
    ok = sorted(versions, key=parse_version)
    if want not in (None, ""):
        ok = [v for v in ok if parse_version(v)[0] == major(want)]
    return ok[-1] if ok else None


def _cached_name(ident: str, version: str) -> str:
    return f"{ident}@{version}.json"


def cached_versions(ident: str, cache: str | None = None) -> list[str]:
    folder = os.path.join(_root(cache), "journals")
    out = []
    try:
        for name in os.listdir(folder):
            m = re.fullmatch(r"([a-z0-9_]{1,40})@(\d+\.\d+\.\d+)\.json", name)
            if m and m.group(1) == ident:
                out.append(m.group(2))
    except OSError:
        pass
    return sorted(out, key=parse_version)


def cached_versions_all(cache: str | None = None) -> dict[str, list[str]]:
    folder = os.path.join(_root(cache), "journals")
    out: dict[str, list[str]] = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for name in names:
        m = re.fullmatch(r"([a-z0-9_]{1,40})@(\d+\.\d+\.\d+)\.json", name)
        if m:
            out.setdefault(m.group(1), []).append(m.group(2))
    return {k: sorted(v, key=parse_version) for k, v in sorted(out.items())}


def load_cached(ident: str, version: str, cache: str | None = None) -> dict:
    path = os.path.join(_root(cache), "journals", _cached_name(ident, version))
    with open(path, encoding="utf-8") as fh:
        return journalfile.check(json.load(fh), path)


def fetched(cache: str | None = None) -> list[dict]:
    """Every journal fetched from the library, the newest version of each, by name."""
    out = []
    for ident, versions in cached_versions_all(cache).items():
        try:
            out.append(load_cached(ident, versions[-1], cache))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda j: j["name"].lower())


def note_used(ident: str, version: str, cache: str | None = None) -> None:
    """Remember that a document used this version today (prune keeps it)."""
    path = os.path.join(_root(cache), "used.json")
    try:
        with open(path, encoding="utf-8") as fh:
            used = json.load(fh)
    except (OSError, ValueError):
        used = {}
    used.setdefault(ident, {})[version] = datetime.date.today().isoformat()
    try:
        _write_atomic(path, json.dumps(used, indent=1).encode("utf-8"))
    except OSError:
        pass


def prune(cache: str | None = None, days: int = 90) -> list[str]:
    """Remove fetched versions nothing needs: keeps the newest of each major version, and any a
    document used in the last `days`. Returns the files removed."""
    root = _root(cache)
    try:
        with open(os.path.join(root, "used.json"), encoding="utf-8") as fh:
            used = json.load(fh)
    except (OSError, ValueError):
        used = {}
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    removed = []
    for ident, versions in cached_versions_all(cache).items():
        keep = {pick(versions, major(v)) for v in versions}
        keep |= {v for v, day in (used.get(ident) or {}).items() if str(day) >= cutoff}
        for v in versions:
            if v not in keep:
                path = os.path.join(root, "journals", _cached_name(ident, v))
                try:
                    os.remove(path)   # (a file this cache made)
                    removed.append(path)
                except OSError:
                    pass
    return removed


# Fetching ----------------------------------------------------------------------------------------------

def _download(entry: dict, source: str, cache: str | None) -> dict:
    """Download one index entry's file, check it, keep it; the journal."""
    try:
        data = read(source, entry["file"], MAX_FILE)
    except FileNotFoundError:
        raise LibraryError(f"The library lists {entry['name']}, but its file isn't there.") from None
    if len(data) > MAX_FILE:
        raise LibraryError(f"{entry['name']}'s file is larger than a journal file may be; it wasn't used.")
    if entry.get("sha256") and hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise LibraryError(f"{entry['name']}'s file isn't the one the library's list describes (its checksum differs), "
                           "so it wasn't used. Try again later.")
    try:
        journal = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise LibraryError(f"{entry['name']}'s file in the library isn't a journal file.") from None
    try:
        journalfile.check(journal, entry["file"])
    except journalfile.JournalFileError as exc:
        raise LibraryError(f"{entry['name']}'s file in the library has problems, so it wasn't used: {exc}") from None
    if journal.get("id") != entry["id"]:
        raise LibraryError(f"The library's file for {entry['name']} is for another journal ({journal.get('id')}).")
    version = _version(journal.get("version"))
    if version != entry["version"]:
        raise LibraryError(f"The library's file for {entry['name']} is version {version}, not {entry['version']} as listed.")
    _write_atomic(os.path.join(_root(cache), "journals", _cached_name(entry["id"], version)), data)
    return journal


def _entry(ident: str, entries: list[dict], want=None) -> dict | None:
    """The index entry to download for want (None: the newest; a major version: the newest of it)."""
    e = next((e for e in entries if e["id"] == ident), None)
    if e is None or want in (None, "") or major(e["version"]) == major(want):
        return e
    older = [o for o in e.get("older") or [] if major(o.get("version")) == major(want)]
    if older:
        o = max(older, key=lambda o: parse_version(o["version"]))
        return {**e, "file": str(o["file"]), "version": _version(o["version"]), "sha256": o.get("sha256")}
    return None


def fetch(ident: str, version=None, source: str = DEFAULT_SOURCE, cache: str | None = None) -> dict:
    """A library journal (version: None for the newest, else a version whose major version is kept).
    From the cache when it's there; else downloaded, its checksum and contents checked, and kept.
    OfflineError says when it needs a connection the first time."""
    got = pick(cached_versions(ident, cache), version)
    if got:
        return load_cached(ident, got, cache)
    entries = library_entries(cache, source)
    entry = _entry(ident, entries, version)
    known = next((e for e in entries if e["id"] == ident), None)
    name = (known or {}).get("name") or ident
    try:
        if entry is None or not entry.get("sha256"):
            if refresh(source, cache, force=True, update=False)["offline"]:
                raise OfflineError(name)
            got_index = cached_index(cache)
            if got_index and got_index.get("source") == source:
                entries = got_index["index"]["journals"]
            entry = _entry(ident, entries, version)
        if entry is None:
            if any(e["id"] == ident for e in entries):
                newest = next(e for e in entries if e["id"] == ident)
                raise LibraryError(f"The library no longer has version {major(version)} of {newest['name']}, "
                                   f"only {newest['version']}.")
            raise LibraryError(f"The library has no journal “{ident}”.")
        return _download(entry, source, cache)
    except OfflineError:
        raise OfflineError(f"{name} isn't on this computer yet, and the library can't be reached: "
                           "it needs a connection the first time it's used.") from None


def resolve(ident: str, version=None, source: str = DEFAULT_SOURCE, cache: str | None = None, online: bool = True) -> dict:
    """What a document set for this journal (written with `version`) uses:
    {"journal": the file or None, "version", "update": a newer major version known, or None,
     "missing": a sentence when it can't be had (offline, gone)}."""
    out = {"journal": None, "version": None, "update": None, "missing": None}
    have = pick(cached_versions(ident, cache), version)
    if have:
        out["journal"], out["version"] = load_cached(ident, have, cache), have
    elif online:
        try:
            j = fetch(ident, version, source, cache)
            out["journal"], out["version"] = j, _version(j.get("version"))
        except LibraryError as exc:
            out["missing"] = str(exc)
    else:
        entry = next((e for e in library_entries(cache, source) if e["id"] == ident), None)
        out["missing"] = (f"{entry['name'] if entry else ident} isn't on this computer yet: "
                          "it will be fetched when you're online.")
    if out["version"]:
        known = [e["version"] for e in library_entries(cache, source) if e["id"] == ident] + cached_versions(ident, cache)
        newest = pick(known)
        if newest and major(newest) > major(out["version"]):
            out["update"] = newest
        note_used(ident, out["version"], cache)
    return out


# Searching ---------------------------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower().replace("&", " and ")).strip()


def _issn_key(s: str) -> str:
    return re.sub(r"[^0-9X]", "", str(s or "").upper())


def _rank(item: dict, q: str) -> int | None:
    """0 ISSN, 1 the whole name or alias, 2 name starts with it, 3 an alias starts with it,
    4 every word somewhere (name, aliases, publisher, ISSN); None: no match."""
    qi = _issn_key(q)
    if len(qi) == 8 and re.fullmatch(r"\d{7}[\dX]", qi) and any(_issn_key(i) == qi for i in item["issn"]):
        return 0
    nq = _norm(q)
    if not nq:
        return None
    names = [_norm(item["name"])] + [_norm(a) for a in item["aliases"]]
    if nq in names:
        return 1
    if names[0].startswith(nq) or names[0].removeprefix("the ").startswith(nq):
        return 2
    if any(a.startswith(nq) for a in names[1:]):
        return 3
    hay = " ".join(names + [_norm(item["publisher"]), _norm(item.get("group", ""))]).split()
    hay += [_issn_key(i) for i in item["issn"]]
    if all(any(h.startswith(w) for h in hay) for w in nq.split()):
        return 4
    return None


def _item(j: dict, where: str, **extra) -> dict:
    issn = _issns(j.get("issn"))
    verified = j.get("verified")
    if isinstance(verified, dict):
        verified = verified.get("status")
    return {"id": j["id"], "name": j["name"], "aliases": list(j.get("aliases") or []), "issn": issn,
            "publisher": j.get("publisher") or "", "group": j.get("group") or j.get("publisher") or "",
            "kind": j.get("kind", "journal"), "version": _version(j.get("version")),
            "verified": verified or "unverified", "where": where, "summary": j.get("summary") or "", **extra}


WHERE_ORDER = {"built_in": 0, "fetched": 1, "mine": 2, "library": 3}
BADGE = {"verified": "Verified", "community": "Community", "unverified": "Not checked"}


def search(query: str, limit: int = 20, cache: str | None = None, source: str | None = None,
           mine: list[dict] | None = None, builtin: list[dict] | None = None) -> list[dict]:
    """Journals matching a name, alias, ISSN (with or without its hyphen) or publisher, best first:
    an exact ISSN, then the whole name, then names starting with it, then every word anywhere;
    Verified before Community at the same rank. Built-in journals, your own (mine: journal files),
    the ones fetched and the library's list (cached, else the shipped snapshot). Each result:
    {"id", "name", "aliases", "issn", "publisher", "group", "kind", "version", "verified", "badge",
     "where": "built_in" | "fetched" | "mine" | "library", "summary", "needs", "on_this_computer"}."""
    pool: dict[str, dict] = {}
    for j in builtin if builtin is not None else journalfile.builtin():
        if j.get("kind", "journal") in ("journal", "house"):
            pool[j["id"]] = _item(j, "built_in")
    for j in fetched(cache):
        if j["id"] not in pool:
            pool[j["id"]] = _item(j, "fetched")
    for e in library_entries(cache, source):
        if e["id"] in pool:
            pool[e["id"]].setdefault("library_version", e["version"])
            continue
        pool[e["id"]] = _item(e, "library", needs=e["needs"])
    for j in mine or []:
        pool["mine:" + j["id"]] = _item(j, "mine")
    hits = []
    for item in pool.values():
        r = _rank(item, query)
        if r is not None:
            item["badge"] = BADGE.get(item["verified"], "")
            item["on_this_computer"] = item["where"] != "library"
            item.setdefault("needs", [])
            hits.append((r, 0 if item["verified"] == "verified" else 1, WHERE_ORDER[item["where"]], item["name"].lower(), item))
    hits.sort(key=lambda h: h[:4])
    return [h[4] for h in hits[:limit]]


# Sharing: a pull request through gh ---------------------------------------------------------------------

def can_push(repo: str) -> bool:
    got = gh_api([f"repos/{repo}"])
    return bool((got.get("permissions") or {}).get("push")) if isinstance(got, dict) else False


def for_library(journal: dict, login: str = "") -> dict:
    """A journal file as it goes to the library: marked Community (a maintainer verifies), the
    reference style's text dropped when it names a style from the CSL repository."""
    j = json.loads(json.dumps(journal))
    j["verified"] = {"status": "community", **({"by": login} if login else {}), "date": datetime.date.today().isoformat()}
    ref = j.get("references") or {}
    if ref.get("csl") and "csl_text" in ref:
        ref.pop("csl_text")
    return j


def pull_request_body(journal: dict, login: str, checked: list[str] | None = None, app: str = "prose") -> str:
    srcs = [s.get("url") if isinstance(s, dict) else str(s) for s in journal.get("sources") or []]
    lines = [f"**{journal['name']}** (`journals/{journal['id']}.json`, version {journal.get('version', '1.0.0')}), "
             f"shared from {app} by @{login}." if login else f"**{journal['name']}**, shared from {app}.", "",
             "### Where its facts come from"]
    lines += [f"- {s}" for s in srcs if s] or ["- (no sources given: please add the journal's author guidelines)"]
    lines += ["", "### Checked in the app"]
    lines += [f"- [x] {c}" for c in checked or ["the file passes the journal-file check"]]
    lines += ["", "### For the reviewer",
              "- [ ] its sources are the journal's own pages",
              "- [ ] its figure sizes and limits match them",
              "- [ ] the preview looks like the journal",
              "", "- [x] I agree my contribution is CC0 (public domain): journal files are facts about journals."]
    return "\n".join(lines) + "\n"


def share_by_pull_request(repo: str, journal: dict, dry_run: bool = False, checked: list[str] | None = None,
                          app: str = "prose", now: datetime.datetime | None = None) -> dict:
    """Offer a journal to a library on GitHub as a pull request from the person's own copy (fork):
    1 who you are, 2 your copy of the library (made once; brought up to date), 3 a new branch,
    4 the journal file on it, 5 the pull request. If you already have one open for this journal,
    its file is updated instead. dry_run: only the reading steps run; the rest are returned as
    "planned". {"name", "pull_request": its address, "updated": bool, "branch", "planned": [...]}."""
    errors, _ = journalfile.validate(journal)
    if errors:
        raise LibraryError("The journal has problems to fix before it's shared: " + "; ".join(errors))
    me = gh_api(["user"])
    login = me.get("login") if isinstance(me, dict) else None
    if not login:
        raise LibraryError("GitHub's tool isn't signed in: run “gh auth login” in a terminal.")
    owner, name = repo.split("/", 1)
    j = for_library(journal, login)
    data = journalfile.dumps(j).encode("utf-8")
    rel = f"journals/{j['id']}.json"
    planned: list[list[str]] = []

    def write(args: list[str], body: dict | None = None) -> dict:
        if dry_run:
            planned.append([*args, json.dumps(body)] if body is not None else args)
            return {}
        got = gh_api([*args, "--input", "-"] if body is not None else args, stdin=json.dumps(body) if body is not None else None)
        return got if isinstance(got, dict) else {}

    # An open pull request of yours for this journal: update its file
    pulls = gh_api([f"repos/{repo}/pulls?state=open&per_page=100"])
    mine = [p for p in pulls if isinstance(p, dict) and ((p.get("head") or {}).get("user") or {}).get("login") == login
            and str((p.get("head") or {}).get("ref", "")).startswith(f"journal-{j['id']}-")] if isinstance(pulls, list) else []
    fork = f"{login}/{name}"
    if mine:
        branch = mine[0]["head"]["ref"]
        try:
            sha = gh_api([f"repos/{fork}/contents/{rel}?ref={branch}"]).get("sha")
        except FileNotFoundError:
            sha = None
        write(["--method", "PUT", f"repos/{fork}/contents/{rel}"],
              {"message": f"Update {j['name']}", "content": base64.b64encode(data).decode("ascii"), "branch": branch, **({"sha": sha} if sha else {})})
        return {"name": j["name"], "pull_request": mine[0].get("html_url", ""), "updated": True, "branch": branch, "planned": planned}
    # Your copy of the library (fork), made once and brought up to date
    write(["--method", "POST", f"repos/{repo}/forks"])
    if not dry_run:
        for _ in range(30):
            try:
                gh_api([f"repos/{fork}"])
                break
            except FileNotFoundError:
                import time
                time.sleep(2)
        else:
            raise LibraryError("GitHub is still making your copy of the library; try Share again in a minute.")
        try:
            write(["--method", "POST", f"repos/{fork}/merge-upstream"], {"branch": "main"})
        except LibraryError:
            pass   # (already up to date, or a copy with changes of its own: the branch starts from it)
    sha_main = "" if dry_run else (gh_api([f"repos/{fork}/git/ref/heads/main"]).get("object") or {}).get("sha", "")
    stamp = (now or datetime.datetime.now()).strftime("%Y%m%d%H%M")
    branch = f"journal-{j['id']}-{stamp}"
    write(["--method", "POST", f"repos/{fork}/git/refs"], {"ref": f"refs/heads/{branch}", "sha": sha_main or "<main>"})
    try:
        existing = gh_api([f"repos/{repo}/contents/{rel}"])
        is_update = True
    except FileNotFoundError:
        existing, is_update = {}, False
    try:
        fork_sha = None if dry_run else gh_api([f"repos/{fork}/contents/{rel}?ref={branch}"]).get("sha")
    except FileNotFoundError:
        fork_sha = None
    write(["--method", "PUT", f"repos/{fork}/contents/{rel}"],
          {"message": f"{'Update' if is_update else 'Add'} {j['name']}", "content": base64.b64encode(data).decode("ascii"),
           "branch": branch, **({"sha": fork_sha} if fork_sha else {})})
    pr = write(["--method", "POST", f"repos/{repo}/pulls"],
               {"title": f"{'Update' if is_update else 'Add'} {j['name']}", "head": f"{login}:{branch}", "base": "main",
                "body": pull_request_body(j, login, checked, app), "maintainer_can_modify": True})
    del existing
    return {"name": j["name"], "pull_request": pr.get("html_url", ""), "updated": False, "branch": branch, "planned": planned}


# Sharing, whatever the library is ----------------------------------------------------------------------

def _sha_on_github(repo: str, rel: str, ref: str | None = None) -> str | None:
    try:
        got = gh_api([f"repos/{repo}/contents/{safe(rel)}" + (f"?ref={urllib.parse.quote(ref)}" if ref else "")])
        return got.get("sha") if isinstance(got, dict) else None
    except FileNotFoundError:
        return None


def write(source: str, rel: str, data: bytes, message: str) -> None:
    """One file written into a library you can change (a folder, or a GitHub repository you have
    write access to)."""
    rel = safe(rel)
    k = kind_of(source)
    if k == "web":
        raise LibraryError("A library on the web can only be read. Share to a folder or a GitHub repository.")
    if k == "github":
        repo, ref = github_of(source)
        sha = _sha_on_github(repo, rel, ref)
        body = {"message": message, "content": base64.b64encode(data).decode("ascii"), **({"sha": sha} if sha else {}),
                **({"branch": ref} if ref else {})}
        gh_api(["--method", "PUT", f"repos/{repo}/contents/{rel}", "--input", "-"], stdin=json.dumps(body))
        return
    root = folder_of(source)
    if not os.path.isdir(root):
        raise LibraryError(f"The library's folder isn't there: {root}")
    _write_atomic(os.path.join(root, *rel.split("/")), data)


def share(source: str, journal: dict, app: str = "prose", dry_run: bool = False, checked: list[str] | None = None,
          summary: str | None = None) -> dict:
    """Offer a journal to the library. A GitHub library you can't change yourself (the public one)
    gets a pull request (share_by_pull_request): {"name", "where", "pull_request", "updated",
    "planned"}. One you can change (a lab's folder or repository) has the file written in, replacing
    an earlier one of the same id, and listed: {"name", "where", "planned"}."""
    where = describe(source)
    if kind_of(source) == "github":
        repo = github_of(source)[0]
        if not can_push(repo):
            got = share_by_pull_request(repo, journal, dry_run=dry_run, app=app, checked=checked)
            return {"name": journal["name"], "where": where, "pull_request": got["pull_request"], "updated": got["updated"],
                    "planned": got["planned"]}
    errors, _ = journalfile.validate(journal)
    if errors:
        raise LibraryError("The journal has problems to fix before it's shared: " + "; ".join(errors))
    rel = f"journals/{journal['id']}.json"
    if dry_run:
        return {"name": journal["name"], "where": where, "planned": [["write", rel], ["write", "index.json"]]}
    data = journalfile.dumps(journal).encode("utf-8")
    write(source, rel, data, f"{journal['name']}: shared from {app}")
    items = [j for j in read_index(source)["journals"] if j["id"] != journal["id"]]
    record = index_entry(journal, rel, data)
    if summary:
        record["summary"] = summary
    write(source, "index.json", write_index([*items, record]), f"List {journal['name']}")
    return {"name": journal["name"], "where": where, "planned": []}
