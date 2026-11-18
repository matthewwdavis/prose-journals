"""A scratch prose for the jobs that need prose (preview, LaTeX, Word layout): find prose, run it in
throwaway folders, set a journal file up in it, and make prose's sample document.

prose is found from --prose-src or the environment variable PROSE_SRC (a copy of prose's source:
the folder with prose/, editor/ and tools/), else an installed `prose` package. PROSE_SRC=none
means "no prose" (the tests use it). When prose isn't there, each job says so and skips.

prose runs with PROSE_HOME, PROSE_CACHE and PROSE_TEMPLATES set to a new temporary folder, always
(never a person's own settings), and writes no .pyc files into the prose it's given.

A journal is set up as one of "your own" journals, from a copy of the file renamed lib_<id> and
"<name> (library check)", because prose won't import a journal with the name or id of one it has
built in (for one of those, the copy takes prose's own reference style, templates/csl/<id>.csl,
so nothing is downloaded). The LaTeX jobs use prose's own built-in template for a journal prose
has built in: its wrapper is written for prose's built-in LaTeX export (BibTeX references), which a
journal of your own doesn't use; they say whether the file's latex section is prose's.
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

SAMPLE_REFS = [
    {"id": "smith2020", "type": "article-journal", "title": "A made-up paper", "author": [{"family": "Smith", "given": "Alice"}],
     "issued": {"date-parts": [[2020]]}, "container-title": "Journal of Examples", "volume": "12", "page": "34-56"},
    {"id": "jones2019", "type": "book", "title": "A made-up book", "author": [{"family": "Jones", "given": "Bob"}],
     "issued": {"date-parts": [[2019]]}, "publisher": "Example Press"},
]
FIGURE_ASSET = "0123abcd.pdf"   # (the picture the sample places: editor/tests/fixtures/sample.md)


def find_prose(given: str | None = None) -> tuple[str | None, str]:
    """(the source folder to put on sys.path, "" for an installed prose; or None) and, when None,
    why: a sentence for the report."""
    src = given if given is not None else os.environ.get("PROSE_SRC", "")
    if src.strip().lower() == "none":
        return None, "prose wasn't given (PROSE_SRC=none)"
    if src.strip():
        src = os.path.abspath(os.path.expanduser(src))
        if os.path.isfile(os.path.join(src, "prose", "__init__.py")):
            return src, ""
        return None, f"PROSE_SRC is {src}, which isn't a copy of prose's source (no prose/__init__.py)"
    import importlib.util
    try:
        found = importlib.util.find_spec("prose")
    except (ImportError, ValueError):
        found = None
    if found is not None and found.origin:
        return "", ""
    return None, "prose isn't here (set PROSE_SRC to a copy of prose's source)"


def tectonic(src: str | None) -> str | None:
    """Tectonic, the LaTeX program prose uses: on PATH or beside this Python."""
    for p in (shutil.which("tectonic"), os.path.join(os.path.dirname(sys.executable), "tectonic")):
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


class Scratch:
    """prose in throwaway folders. Use as `with Scratch(src) as s:`; then s.set_up(path)."""

    def __init__(self, src: str):
        self.src = src
        self.work = tempfile.mkdtemp(prefix="library-prose-")
        self.ws = None
        self.prose = None

    def __enter__(self):
        for var, sub in (("PROSE_HOME", "home"), ("PROSE_CACHE", "cache"), ("PROSE_TEMPLATES", "templates")):
            os.environ[var] = os.path.join(self.work, sub)   # (always set: never a person's own settings)
            os.makedirs(os.environ[var], exist_ok=True)
        sys.dont_write_bytecode = True                       # (no .pyc files in the prose given)
        if self.src:
            sys.path.insert(0, self.src)
        self.prose = importlib.import_module("prose")
        where = os.path.dirname(os.path.abspath(self.prose.__file__))
        if self.src and not where.startswith(os.path.join(self.src, "")):
            raise RuntimeError(f"Python found another prose ({where}) before the one in PROSE_SRC ({self.src})")
        self.root = self.src or os.path.dirname(where)
        return self

    def __exit__(self, *exc):
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:   # noqa: BLE001 (closing a scratch workspace: nothing to keep)
                pass
        shutil.rmtree(self.work, ignore_errors=True)
        return False

    # The journal -------------------------------------------------------------------------------------

    def builtin_ids(self) -> set[str]:
        from prose import journals
        return set(getattr(journals, "FILES", {}) or {}) | set(getattr(journals, "JOURNALS", {}) or {})

    def templates(self) -> str:
        return os.path.join(self.root, "prose", "templates")

    def builtin_file(self, ident: str) -> dict | None:
        """prose's own copy of a built-in journal's file (its vendored labapp), or None."""
        p = os.path.join(self.root, "prose", "_labapp", "builtin_journals", f"{ident}.json")
        if not os.path.isfile(p):
            return None
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)

    def set_up(self, path: str, publisher_files: dict[str, str] | None = None, latex: bool = False) -> dict:
        """Set the journal file up in the scratch prose: {"key", "how", "notes"}.

        As one of your own journals (a renamed copy), except with latex=True for a journal prose has
        built in: prose's own LaTeX template for it is used then (its wrapper is written for prose's
        built-in export, which a journal of your own doesn't have), and "how" says whether the
        file's latex section is the same as prose's copy."""
        from prose import journals, myjournals
        with open(path, encoding="utf-8") as fh:
            j = json.load(fh)
        ident = j["id"]
        notes: list[str] = []
        if latex and ident in self.builtin_ids():
            own = self.builtin_file(ident) or {}
            same = (own.get("latex") or {}) == (j.get("latex") or {})
            if publisher_files:
                from prose import templatefiles
                if templatefiles.spec_of(ident).get("fetch"):
                    with tempfile.TemporaryDirectory(prefix="library-files-") as tmp:
                        for name, src in publisher_files.items():
                            shutil.copyfile(src, os.path.join(tmp, name))
                        notes += templatefiles.install_from(ident, tmp).get("notes") or []
            how = f"prose's built-in template for {ident}" + ("" if same else
                                                                 " (the file's latex section differs from prose's copy: prose's was compiled)")
            return {"key": ident, "how": how, "notes": notes, "same": same}
        copy = json.loads(json.dumps(j))
        copy["id"] = f"lib_{ident}"[:40]
        copy["name"] = f"{j['name'][:100]} (library check)"
        lx = copy.setdefault("latex", {})
        ref = copy.setdefault("references", {})
        own_style = os.path.join(self.templates(), "csl", f"{ident}.csl")
        if ident in self.builtin_ids() and not ref.get("csl_text") and os.path.isfile(own_style):
            with open(own_style, encoding="utf-8") as fh:   # (prose's own copy: no download)
                ref["csl_text"] = fh.read()
        mine = os.path.join(self.work, f"{copy['id']}.prosejournal")
        with open(mine, "w", encoding="utf-8") as fh:
            json.dump(copy, fh, ensure_ascii=False, indent=1)
        got = myjournals.import_(mine)
        journals.refresh_user()
        folder = myjournals.folder(copy["id"])
        for name, src in (publisher_files or {}).items():
            shutil.copyfile(src, os.path.join(folder, name))
        how = "as a journal of your own, " + ("with the LaTeX wrapper written in the file" if lx.get("wrapper_text")
                                               else "with prose's standard LaTeX wrapper")
        return {"key": got["key"], "how": how, "notes": notes + list(got.get("notes") or []), "same": True}

    # The sample --------------------------------------------------------------------------------------

    def workspace(self):
        """A prose workspace holding the sample's figure."""
        if self.ws is None:
            import typst
            from prose.workspace import Workspace
            self.ws = Workspace()
            src = os.path.join(self.work, "figure.typ")
            with open(src, "w", encoding="utf-8") as fh:
                fh.write("#set page(width: 180mm, height: 120mm, margin: 0pt)\n"
                         "#rect(width: 100%, height: 100%, fill: luma(220), stroke: 0.5pt)\n")
            with open(os.path.join(self.ws.assets, FIGURE_ASSET), "wb") as fh:
                fh.write(typst.compile(src, format="pdf"))
        return self.ws

    def sample(self) -> tuple[dict, str]:
        """prose's sample document as the editor hands it to the exports (pandoc's JSON), and
        which sample it is."""
        from prose import pandoc_io
        path = os.path.join(self.root, "editor", "tests", "fixtures", "sample.md")
        which = "editor/tests/fixtures/sample.md"
        if not os.path.isfile(path):
            path = os.path.join(self.root, "prose", "samples", "welcome.md")
            which = "prose/samples/welcome.md"
        with open(path, encoding="utf-8") as fh:
            doc = pandoc_io.md_to_json(fh.read())
        node = shutil.which("node") or os.path.join(os.path.dirname(sys.executable), "node")
        render = os.path.join(self.root, "editor", "tools", "render-json.mjs")
        if os.path.isfile(render) and os.path.isfile(node):
            out = subprocess.run([node, render], input=json.dumps(doc), capture_output=True, text=True, timeout=120)
            if out.returncode == 0:
                return json.loads(out.stdout)["pandoc"], which
        return doc, which + " (as pandoc reads it: the editor's own reading needs Node)"
