"""The publisher's LaTeX files a journal's template needs, for the LaTeX and Word-layout jobs.

The library never holds a publisher's files (CONTRIBUTING.md). A job finds them, in this order:

1. a folder given with --publisher-files (a maintainer's own copies), as FOLDER/<name> or
   FOLDER/<id>/<name>;
2. prose's own copies (prose/templates/*/ in PROSE_SRC: the files prose ships itself);
3. the cache (--cache, LIBRARY_CACHE, else ~/.cache/prose-journals-library), from an earlier download;
4. with --download, the publisher's package from latex.fetch.url (https only), into the cache.
   When LIBRARY_DOWNLOAD_ALLOWED names a file (one address a line), only an address listed in it is
   downloaded: the checks of a pull request list the addresses the library's own journals already
   use (python tools/publisher.py --known-urls journals > FILE, before the pull request's files are
   taken), so a new address is downloaded only after a maintainer has merged it.

The cache is never inside the library's folder, so nothing found is ever committed. A file whose
checksum differs from the one the journal gives is still used (publishers revise their packages),
and said so.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import common  # noqa: E402

MAX_PACKAGE = 200 * 1024 * 1024
MAX_FILE = 5 * 1024 * 1024


def default_cache() -> str:
    return os.environ.get("LIBRARY_CACHE") or os.path.join(os.path.expanduser("~"), ".cache", "prose-journals-library")


def _sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _inside(path: str, folder: str) -> bool:
    path, folder = os.path.realpath(path), os.path.realpath(folder)
    return path == folder or path.startswith(os.path.join(folder, ""))


def _checksums(journal: dict) -> dict[str, str | None]:
    lx = journal.get("latex") or {}
    out: dict[str, str | None] = {}
    for n, h in ((lx.get("fetch") or {}).get("files") or {}).items():
        out[n] = h
    for n, h in (lx.get("files") or {}).items():
        out[n] = h or out.get(n)
    return out


def _find(folder: str, name: str) -> str | None:
    """FOLDER/name, else a file of that name (any case) one folder down."""
    p = os.path.join(folder, name)
    if os.path.isfile(p):
        return p
    try:
        subs = sorted(os.listdir(folder))
    except OSError:
        return None
    for sub in subs:
        d = os.path.join(folder, sub)
        if os.path.isdir(d):
            for n in os.listdir(d):
                if n.lower() == name.lower() and os.path.isfile(os.path.join(d, n)):
                    return os.path.join(d, n)
    return None


def gather(journal: dict, *, prose_root: str | None = None, folders: list[str] | None = None, cache: str | None = None,
           download: bool = False) -> tuple[dict[str, str], list[str], list[str]]:
    """({name: path} found, [names missing], [notes])."""
    wanted = _checksums(journal)
    names = common.needs(journal)
    found: dict[str, str] = {}
    notes: list[str] = []
    ident = journal.get("id", "")
    places: list[tuple[str, str]] = []
    for f in folders or []:
        places += [(os.path.join(f, ident), "the folder given"), (f, "the folder given")]
    if prose_root:
        t = os.path.join(prose_root, "prose", "templates")
        places.append((os.path.join(t, ident), "prose's own copy"))
        if os.path.isdir(t):
            places += [(os.path.join(t, d), "prose's own copy") for d in sorted(os.listdir(t)) if d != ident]
    cache = cache or default_cache()
    fetch = (journal.get("latex") or {}).get("fetch") or {}
    store_name = str(fetch.get("store") or ident)
    store = os.path.join(cache, store_name if re.fullmatch(r"[\w.-]+", store_name) else ident)
    places.append((store, "the cache"))
    for name in names:
        # (prefer a copy with the journal's checksum, then any copy of that name)
        hits = [(p, how) for d, how in places if os.path.isdir(d) for p in [os.path.join(d, name)] if os.path.isfile(p)]
        good = [h for h in hits if wanted.get(name) and _sha(h[0]) == wanted[name]]
        if good or hits:
            found[name] = (good or hits)[0][0]
    missing = [n for n in names if n not in found]
    if missing and download and fetch.get("url"):
        got, why = _download(fetch, store, missing, cache)
        if why:
            notes.append(why)
        found.update(got)
        missing = [n for n in names if n not in found]
    for name, path in found.items():
        if wanted.get(name) and _sha(path) != wanted[name]:
            notes.append(f"{name} differs from the copy the journal file names (the publisher may have revised it); used as it is")
    return found, missing, notes


def known_urls(folder: str) -> list[str]:
    """The publisher's download addresses (latex.fetch.url) the journals in folder use."""
    out = set()
    for path in common.journal_files(folder):
        j, _, _ = common.read_journal(path)
        url = (((j or {}).get("latex") or {}).get("fetch") or {}).get("url") if isinstance(j, dict) else None
        if isinstance(url, str) and url.startswith("https://"):
            out.add(url)
    return sorted(out)


def allowed(url: str) -> bool:
    """Whether url may be downloaded (LIBRARY_DOWNLOAD_ALLOWED: the file listing the addresses allowed)."""
    listing = os.environ.get("LIBRARY_DOWNLOAD_ALLOWED")
    if not listing:
        return True
    try:
        with open(listing, encoding="utf-8") as fh:
            return url in {ln.strip() for ln in fh if ln.strip()}
    except OSError:
        return False


def _download(fetch: dict, store: str, names: list[str], cache: str) -> tuple[dict[str, str], str | None]:
    url = str(fetch.get("url") or "")
    if not url.startswith("https://"):
        return {}, "latex.fetch.url isn't an https address, so the publisher's package wasn't downloaded"
    if not allowed(url):
        return {}, (f"latex.fetch.url ({url[:100]}) isn't an address the library's journals already use, so the publisher's "
                    "package wasn't downloaded for this check; a maintainer checks the address, and it's used once merged")
    if _inside(cache, common.ROOT):
        return {}, "the cache is inside the library's folder, where a publisher's file must never be; nothing was downloaded"
    os.makedirs(store, exist_ok=True)
    package = os.path.join(store, "package.download")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "prose-journals-library-ci"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(package, "wb") as out:   # noqa: S310 (https only, above)
            if not resp.geturl().startswith("https://"):
                return {}, "the publisher's site sent the download somewhere that isn't https; nothing was taken"
            total = 0
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_PACKAGE:
                    return {}, "the publisher's package is far larger than expected; nothing was taken"
                out.write(chunk)
    except OSError as exc:
        return {}, f"the publisher's package couldn't be downloaded from {url} ({exc})"
    got: dict[str, str] = {}
    lower = {n.lower(): n for n in names}
    if zipfile.is_zipfile(package):
        with zipfile.ZipFile(package) as z:
            for m in z.infolist():
                base = os.path.basename(m.filename)
                if not m.is_dir() and base.lower() in lower and lower[base.lower()] not in got and m.file_size <= MAX_FILE:
                    dest = os.path.join(store, lower[base.lower()])
                    with open(dest, "wb") as fh:
                        fh.write(z.read(m))
                    got[lower[base.lower()]] = dest
    elif len(names) == 1 and os.path.getsize(package) <= MAX_FILE:
        dest = os.path.join(store, names[0])
        os.replace(package, dest)
        got[names[0]] = dest
    if os.path.exists(package):
        os.remove(package)
    return got, None


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="The publisher's download addresses the journals in a folder use, one a line.")
    ap.add_argument("--known-urls", metavar="FOLDER", required=True, help="a folder of journal files (journals/)")
    args = ap.parse_args(argv)
    for url in known_urls(args.known_urls):
        print(url)
    return 0


def skipped_sentence(missing: list[str], journal: dict, download: bool) -> str:
    """"needs the publisher's sn-jnl.cls, which the library never holds: skipped"."""
    names = missing[0] if len(missing) == 1 else ", ".join(missing[:-1]) + " and " + missing[-1]
    fetch = (journal.get("latex") or {}).get("fetch") or {}
    if fetch.get("url") and not download:
        how = " (it can be downloaded from the publisher, but downloading wasn't allowed: --download)"
    elif fetch.get("url"):
        how = " (the publisher's package didn't have it)"
    else:
        how = ""
    return f"needs the publisher's {names}, which the library never holds{how}: skipped"


if __name__ == "__main__":
    sys.exit(main())
