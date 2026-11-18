"""Journal files: one journal, everything both apps know about it, in one JSON file.

A journal file says who publishes the journal, its page, the sections a manuscript has, how it names
and captions figures, its house style, supplement, statements, word limits, reference style, LaTeX
template, Word layout, the font its author guidelines ask for in a manuscript ("manuscript_font") and
its figure rules (sizes, panel labels, text, lines, resolution, files), with where each fact comes from. prose's docs/journal-format.md explains every field; the schema is
journal.schema.json beside this file. Only "format", "id" and "name" are needed: everything else has
a sensible default (complete()).

    python -m labapp.journalfile validate FILE...        # say what's wrong with each, in words
    python -m labapp.journalfile upgrade OLD NEW         # an older journal file (.prosejournal) as a new one

In an app the copy is <app>/_labapp: python -m prose._labapp.journalfile validate FILE.

The built-in journals are files too (builtin_journals/, listed in order by its index.json): both
apps' tables are made from them (journals.py here, prose's journals.py), so a journal is kept once.

A person's own journals are kept in the one list both apps share (journals.py: shared_user_file):
each entry the journal file itself ("journal"), with the older entry's keys beside it (name,
depth_mm, types, ..., "manuscript": what prose's My Journals dialog shows) so an older version of
either app still reads it (legacy_view). from_legacy() turns an older entry into a journal file.
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import zipfile

FORMAT = 1
HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "journal.schema.json")
BUILTIN_DIR = os.path.join(HERE, "builtin_journals")
KINDS = ("journal", "house", "thesis", "letter", "general")
TEMPLATE_KEYS = ("files", "publisher", "citations", "provisional", "note", "fetch", "wrapper_for", "csl_name")
LIMIT_KEYS = ("title_chars", "title_words", "abstract_words", "summary_words", "main_text_words", "methods_words", "legend_words",
              "display_items", "figures", "tables", "references", "keywords")
FORM_LIMITS = ("title_chars", "abstract_words", "main_text_words", "references", "figures", "tables", "display_items")
RULE_KEYS = ("labels", "text", "lines", "pictures", "export")


class JournalFileError(ValueError):
    """A journal file that can't be used; the message says what's wrong, in words for the person."""

    def __init__(self, problems: list[str], where: str = ""):
        self.problems = problems
        head = f"{where}: " if where else ""
        super().__init__(head + ("; ".join(problems) if len(problems) < 4 else
                                 "; ".join(problems[:3]) + f"; and {len(problems) - 3} more"))


# Checking a file against the schema ----------------------------------------------------------------

_SCHEMA: dict | None = None


def schema() -> dict:
    global _SCHEMA
    if _SCHEMA is None:
        with open(SCHEMA_PATH, encoding="utf-8") as fh:
            _SCHEMA = json.load(fh)
    return _SCHEMA


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is(value, t: str) -> bool:
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPES[t])


def _words(value) -> str:
    if value is None:
        return "nothing (null)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f"the text {json.dumps(value, ensure_ascii=False)}"
    if isinstance(value, (int, float)):
        return f"the number {value}"
    return "a list" if isinstance(value, list) else "a group of fields"


def _choices(options: list) -> str:
    shown = [json.dumps(o, ensure_ascii=False) for o in options]
    return shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " or " + shown[-1]


_TYPE_WORDS = {"object": "a group of fields { }", "array": "a list [ ]", "string": "text in quotes", "integer": "a whole number",
               "number": "a number", "boolean": "true or false", "null": "null"}


def _check(value, spec: dict, path: str, errors: list, warnings: list, root: dict) -> None:
    if "$ref" in spec:
        name = spec["$ref"].split("/")[-1]
        spec = {**root["$defs"][name], **{k: v for k, v in spec.items() if k != "$ref"}}
    where = path or "the file"
    if "anyOf" in spec:
        trials = []
        for option in spec["anyOf"]:
            e: list = []
            _check(value, option, path, e, [], root)
            if not e:
                break
            trials.append(e[0])
        else:
            errors.append(trials[-1] if len(trials) == 1 else f"{where}: {_words(value)} isn't any of the forms this field takes")
        return
    if "const" in spec and value != spec["const"]:
        errors.append(f"{where} has to be {json.dumps(spec['const'])}, not {_words(value)}")
        return
    if "enum" in spec and value not in spec["enum"]:
        errors.append(f"{where} is {_words(value)}; it has to be {_choices(spec['enum'])}")
        return
    types = spec.get("type")
    if types:
        types = [types] if isinstance(types, str) else types
        if not any(_is(value, t) for t in types):
            errors.append(f"{where} has to be {' or '.join(_TYPE_WORDS[t] for t in types)}, not {_words(value)}")
            return
    if isinstance(value, str):
        if "pattern" in spec and not re.search(spec["pattern"], value):
            errors.append(f"{where}: {_words(value)} isn't in the right form" + (f" ({spec['description']})" if spec.get("description") else ""))
        if len(value) < spec.get("minLength", 0):
            errors.append(f"{where} can't be empty")
        if "maxLength" in spec and len(value) > spec["maxLength"]:
            errors.append(f"{where} is longer than {spec['maxLength']} characters")
    if _is(value, "number"):
        if "minimum" in spec and value < spec["minimum"]:
            errors.append(f"{where} is {value}; it can't be less than {spec['minimum']}")
        if "exclusiveMinimum" in spec and value <= spec["exclusiveMinimum"]:
            errors.append(f"{where} is {value}; it has to be more than {spec['exclusiveMinimum']}")
        if "maximum" in spec and value > spec["maximum"]:
            errors.append(f"{where} is {value}; it can't be more than {spec['maximum']}")
    if isinstance(value, list):
        if len(value) < spec.get("minItems", 0):
            errors.append(f"{where} needs at least {spec['minItems']} item{'s' if spec['minItems'] != 1 else ''}")
        if "maxItems" in spec and len(value) > spec["maxItems"]:
            errors.append(f"{where} can have at most {spec['maxItems']} items")
        if "items" in spec:
            for i, item in enumerate(value):
                _check(item, spec["items"], f"{path}[{i + 1}]", errors, warnings, root)
    if isinstance(value, dict):
        props = spec.get("properties", {})
        for need in spec.get("required", []):
            if need not in value:
                errors.append(f"{where} needs \"{need}\"")
        names = spec.get("propertyNames", {}).get("enum")
        extra = spec.get("additionalProperties", True)
        for k, v in value.items():
            sub = f"{path}.{k}" if path else k
            if k in props:
                _check(v, props[k], sub, errors, warnings, root)
            elif names is not None and k not in names:
                errors.append(f"{sub}: there's no such style; the styles are {_choices(names)}")
            elif extra is False:
                close = [p for p in props if _near(k, p)]
                warnings.append(f"{sub} isn't a field this version knows" + (f" (did you mean \"{close[0]}\"?)" if close else
                                                                               "; it's kept, but nothing reads it"))
            elif isinstance(extra, dict):
                _check(v, extra, sub, errors, warnings, root)


def _near(a: str, b: str) -> bool:
    """Two field names a slip of the keyboard apart."""
    if a.lower() == b.lower() or a.replace("-", "_") == b:
        return True
    if abs(len(a) - len(b)) > 2:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] <= 2 and min(len(a), len(b)) > 3


def validate(journal, folder: str | None = None) -> tuple[list[str], list[str]]:
    """(errors, warnings) for a journal file's contents, each a sentence naming the field ("legends.
    sep is ..."). Errors make the file unusable; warnings are fields this version doesn't know (a
    newer app's, or a typing slip). With folder, the files it names beside it are checked too."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(journal, dict):
        return ["a journal file is a group of fields { } with at least \"format\", \"id\" and \"name\""], []
    if isinstance(journal.get("format"), int) and not isinstance(journal.get("format"), bool) and journal["format"] > FORMAT:
        return [f"this journal file is format {journal['format']}, from a newer version of prose or mosaic; update the app to use it"], []
    root = schema()
    _check(journal, root, "", errors, warnings, root)
    if errors:
        return errors, warnings
    fig = journal.get("figures") or {}
    names = [t["name"].lower() for t in fig.get("types", [])]
    if len(set(names)) != len(names):
        errors.append("figures.types: two widths have the same name; give each its own")
    if fig and fig.get("listed", True) and journal.get("kind", "journal") == "journal" and not fig.get("depth_mm"):
        errors.append("figures.depth_mm: a journal's figures need the deepest a figure may be, in millimetres")
    if fig.get("max_width_mm") and names and fig["max_width_mm"] < max(t["width_mm"] for t in fig["types"]):
        errors.append("figures.max_width_mm is narrower than its widest figure type")
    for part in ("front", "body", "back"):
        kinds = [p["kind"] for p in (journal.get("structure") or {}).get(part, [])]
        dup = sorted({k for k in kinds if kinds.count(k) > 1})
        if dup:
            errors.append(f"structure.{part}: {', '.join(dup)} is listed twice")
    keys = journal.get("structure", {}).get("keywords") or {}
    if "min" in keys and "max" in keys and keys["min"] > keys["max"]:
        errors.append("structure.keywords: min is more than max")
    lx = journal.get("latex") or {}
    if lx.get("template") == "publisher" and not (lx.get("class") or lx.get("fetch") or lx.get("files")):
        errors.append("latex: a publisher's template needs its class (\"class\") or the files to fetch (\"fetch\")")
    errors += _fetch_problems(lx.get("fetch") or {})
    ref = journal.get("references") or {}
    if ref.get("csl_text") is not None and "<style" not in ref["csl_text"]:
        errors.append("references.csl_text isn't a reference style (CSL): it has no <style>")
    if folder:
        for rel, what in _named_files(journal):
            p = os.path.join(folder, *rel.split("/"))
            if not os.path.isfile(p):
                warnings.append(f"{what} names {rel}, which isn't beside the journal file")
            else:
                want = (lx.get("files") or {}).get(rel)
                if want and _sha(p) != want:
                    warnings.append(f"latex.files: {rel} isn't the copy the file was made with (its checksum differs)")
    return errors, warnings


def fetch_checksums(fetch: dict) -> dict[str, list[str]]:
    """Every copy of the publisher's files a latex.fetch takes, by name: {name: [sha256, ...]}, the
    current package's first ("files" and "optional"), then each earlier package's ("earlier"), then
    the copies prose's own corrections make ("corrections" "to"). A journal file of format 1 without
    "earlier" or "corrections" gives one checksum a file, as before."""
    out: dict[str, list[str]] = {}

    def add(name, sha) -> None:
        if isinstance(name, str) and isinstance(sha, str) and sha not in out.setdefault(name, []):
            out[name].append(sha)

    for n, h in (fetch.get("files") or {}).items():
        add(n, h)
    for n, o in (fetch.get("optional") or {}).items():
        add(n, (o or {}).get("sha256") if isinstance(o, dict) else None)
    for e in fetch.get("earlier") or []:
        for part in ("files", "optional"):
            for n, h in ((e or {}).get(part) or {}).items():
                add(n, h)
    for c in fetch.get("corrections") or []:
        add((c or {}).get("file"), (c or {}).get("to"))
    return out


def _fetch_problems(fetch: dict) -> list[str]:
    """latex.fetch's earlier packages and corrections, checked against its files."""
    out: list[str] = []
    names = set(fetch.get("files") or {}) | set(fetch.get("optional") or {})
    for i, e in enumerate(fetch.get("earlier") or [], 1):
        for part in ("files", "optional"):
            for n in ((e or {}).get(part) or {}):
                if n not in names:
                    out.append(f"latex.fetch.earlier[{i}].{part}: {n} isn't one of the files latex.fetch gets")
    known = fetch_checksums({k: v for k, v in fetch.items() if k != "corrections"})
    for i, c in enumerate(fetch.get("corrections") or [], 1):
        where = f"latex.fetch.corrections[{i}]"
        if c.get("file") not in names:
            out.append(f"{where}: {c.get('file')} isn't one of the files latex.fetch gets")
            continue
        if c.get("from") not in known.get(c["file"], []):
            out.append(f"{where}: \"from\" isn't the checksum of a copy of {c['file']} that latex.fetch takes (\"files\" or \"earlier\")")
        if c.get("from") == c.get("to"):
            out.append(f"{where}: \"from\" and \"to\" are the same, so it changes nothing")
        for j, ed in enumerate(c.get("edits") or [], 1):
            ok = (isinstance(ed, list) and len(ed) == 3 and all(isinstance(x, int) and not isinstance(x, bool) for x in ed[:2])
                  and 0 <= ed[0] <= ed[1] and isinstance(ed[2], list) and all(isinstance(x, str) for x in ed[2]))
            if not ok:
                out.append(f"{where}.edits[{j}] isn't [first line, line after the last, [the lines put there]]")
    return out


def _named_files(journal: dict) -> list[tuple[str, str]]:
    out = []
    lx = journal.get("latex") or {}
    if lx.get("wrapper") and "wrapper_text" not in lx:
        out.append((lx["wrapper"], "latex.wrapper"))
    out += [(f, "latex.files") for f in (lx.get("files") or {})]
    w = journal.get("word") or {}
    if w.get("reference_docx"):
        out.append((w["reference_docx"], "word.reference_docx"))
    h = journal.get("house") or {}
    out += [(h[k], f"house.{k}") for k in ("starter", "typst") if h.get(k)]
    return out


def _sha(path: str) -> str:
    import hashlib
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def check(journal, where: str = "") -> dict:
    """The journal, checked: JournalFileError, with every problem in words, when it can't be used."""
    errors, _ = validate(journal)
    if errors:
        raise JournalFileError(errors, where)
    return journal


# Defaults --------------------------------------------------------------------------------------------

def complete(journal: dict) -> dict:
    """The journal with every default a minimal file leaves out (a copy): what a field left out means."""
    j = copy.deepcopy(journal)
    j.setdefault("version", "1.0.0")
    j.setdefault("kind", "journal")
    j.setdefault("publisher", "")
    j.setdefault("verified", {"status": "unverified"})
    j["page"] = {"paper": "a4", **(j.get("page") or {})}
    j["page"].setdefault("margin_mm", 25.4 if j["page"]["paper"] == "letter" else 25.0)
    j["captions"] = {"label": "Figure", "bar": False, **(j.get("captions") or {})}
    j["references"] = {"csl": None, **(j.get("references") or {})}
    lx = j.get("latex") or {}
    j["latex"] = {"template": "publisher" if lx.get("class") else "standard", **lx}
    if j.get("figures"):
        j["figures"] = {"listed": True, **j["figures"]}
    return j


def strip_evidence(section):
    """A section without its "evidence" (where its facts come from: for people, not the apps)."""
    if isinstance(section, dict):
        return {k: v for k, v in section.items() if k != "evidence"}
    return section


# The built-in journals ---------------------------------------------------------------------------------

_BUILTIN: list | None = None


def builtin_index() -> dict:
    with open(os.path.join(BUILTIN_DIR, "index.json"), encoding="utf-8") as fh:
        return json.load(fh)


def builtin() -> list[dict]:
    """The built-in journals' files, in the index's order (each checked). When the apps ship only
    the core (journallibrary.ship_all() False), the other journals the index lists are the ones
    fetched from the library (the newest kept version of each), in the same place in the order;
    one not fetched yet is left out until it is (reload())."""
    global _BUILTIN
    if _BUILTIN is None:
        from . import journallibrary
        out = []
        everything = journallibrary.ship_all()
        for item in builtin_index()["journals"]:
            if everything or item.get("core"):
                with open(os.path.join(BUILTIN_DIR, item["file"]), encoding="utf-8") as fh:
                    out.append(check(json.load(fh), item["file"]))
                continue
            versions = journallibrary.cached_versions(item["id"])
            if versions:
                try:
                    out.append(journallibrary.load_cached(item["id"], versions[-1]))
                except (OSError, ValueError):
                    pass
        _BUILTIN = out
    return copy.deepcopy(_BUILTIN)


def reload() -> list[dict]:
    """builtin() read again (a journal was fetched from the library)."""
    global _BUILTIN
    _BUILTIN = None
    return builtin()


def figure_entry(journal: dict) -> dict | None:
    """The journal as the shared figure table has it (journals.py: JOURNALS), or None when it has
    no figure sizes to list: {"name", "depth_mm", "types", "any_width"?, "max_width_mm"?, "note",
    "source", "labels"... (its rules), "publisher" (the group the lists show it under: its "group",
    else its publisher)}."""
    fig = journal.get("figures")
    if not fig or not fig.get("types"):
        return None
    out = {"name": fig.get("name") or journal["name"], "depth_mm": fig.get("depth_mm"),
           "types": [{"name": t["name"], "width_mm": t["width_mm"]} for t in fig["types"]]}
    for k in ("any_width", "max_width_mm"):
        if k in fig:
            out[k] = fig[k]
    out["note"] = fig.get("note", "")
    out["source"] = fig.get("source", "")
    for k in RULE_KEYS:
        if k in fig:
            out[k] = copy.deepcopy(fig[k])
    out["publisher"] = journal.get("group") or journal.get("publisher") or "My journals"   # (the group both apps list it under)
    return out


def rules_of(journal: dict) -> dict:
    fig = journal.get("figures") or {}
    return {k: copy.deepcopy(fig[k]) for k in RULE_KEYS if k in fig}


# Starting from a reference style (CSL) ----------------------------------------------------------------

_STYLE_SUFFIX = re.compile(r"\s*\((?:author-date|numeric|numbered|note|author|label|harvard|vancouver|[^)]*edition[^)]*)\)\s*$", re.I)
_GUIDE_WORDS = re.compile(r"author|instruction|guide|submi|gta|manuscript|prepar|style", re.I)
_GENERIC_LINKS = ("springer.com/cda/content/document",)   # (Springer's own style points, not one journal's)


def _clean_url(url: str) -> str | None:
    url = (url or "").strip()
    return url if re.match(r"^https?://\S+$", url) else None


def starter(style: dict, today: str | None = None) -> dict:
    """A starter journal file from what a reference style says about its journal (cslindex.info, or
    an entry of the shipped list: cslindex.find / search): its name, ISSNs, publisher when the style
    names one, the author guidelines' or the journal's page, how it cites (numbers or author and year)
    and the style itself (references.csl). Everything else takes the defaults (complete), for the
    person to fill in; it's "unverified" until someone checks it against the journal's guidelines."""
    import datetime
    title = _STYLE_SUFFIX.sub("", " ".join((style.get("title") or style.get("name") or "").split()))[:120] or "Journal"
    name = re.sub(r"[^a-z0-9-]", "", (style.get("name") or "").lower())
    j: dict = {"format": FORMAT, "id": slug(title), "name": title, "version": "1.0.0", "kind": "journal",
               "publisher": style.get("publisher") or ""}
    issn = {k: v for k, v in (("print", style.get("issn")), ("online", style.get("eissn")))
            if v and re.fullmatch(r"[0-9]{4}-[0-9]{3}[0-9X]", v)}
    if issn:
        j["issn"] = issn
    short = " ".join((style.get("short") or "").split())
    if short and short.lower() != title.lower() and len(short) <= 120:
        j["aliases"] = [short]
    links = [u for u in (_clean_url(x) for x in style.get("links") or []) if u and not any(g in u for g in _GENERIC_LINKS)]
    guide = next((u for u in links if _GUIDE_WORDS.search(u.split("//", 1)[-1].split("/", 1)[-1])), None)
    home = next((u for u in links if u != guide), None)
    if guide:
        j["guidelines"] = guide
    if home:
        j["homepage"] = home
    j["verified"] = {"status": "unverified", "note": "Started from its reference style: only the name, ISSNs, links and "
                     "reference style come from there. Check the rest against the journal's author guidelines."}
    j["captions"] = {"label": "Figure"}
    ref: dict = {"csl": name or None}
    if style.get("citations") in ("numeric", "author-date", "note", "label", "author"):
        ref["citations"] = style["citations"]
    where = f"https://www.zotero.org/styles/{name}" if name else ""
    ref["evidence"] = (f"The Citation Style Language style {name}" + (f" (it takes its rules from {style['parent']})" if style.get("parent") else "")
                       + f", read {today or datetime.date.today().isoformat()}.")
    j["references"] = ref
    if where:
        j["sources"] = [{"url": where, "what": "Its reference style (Citation Style Language): name, ISSNs, links and how it cites."}]
    return check(j, title)


def from_csl(csl_text: str, csl_name: str | None = None) -> dict:
    """A starter journal file from a CSL style's text (a .csl file): starter() of what its <info> says.
    csl_name: the style's name in the repository (the end of its address on zotero.org/styles), when
    the text doesn't say. ValueError when the text isn't a style."""
    from . import cslindex
    return starter(cslindex.info(csl_text, csl_name))


# Older entries and files -----------------------------------------------------------------------------

LEGACY_FORM = {"paper": "a4", "margin_mm": 25.0, "font": "Times New Roman", "size_pt": 12.0, "line_spacing": 1.0, "columns": 1,
               "label": "Figure", "bar": False, "csl": {"name": "nature"}, "sections": [], "numbered_headings": False,
               "limits": {}, "latex": None}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40] or "journal"


def _styles(font: str, size: float) -> dict:
    style = lambda extra=0.0: {"font": font, "size": size + extra}   # noqa: E731
    return {"normal": style(), "title": style(5), "h1": style(2), "h2": style(), "h3": style(), "quote": style()}


def _structure(sections: list[str]) -> dict | None:
    """The headings a journal expects, in order, as a structure: the body up to References, the rest after."""
    if not sections:
        return None
    ids = [slug(s) for s in sections]
    cut = ids.index("references") if "references" in ids else len(ids)
    return {"abstract": {"name": "Abstract", "words": None}, "front": [],
            "body": [{"kind": i, "name": s} for i, s in zip(ids[:cut], sections[:cut]) if i != "abstract"],
            "back": [{"kind": i, "name": s} for i, s in zip(ids[cut:], sections[cut:])]}


def apply_form(journal: dict, form: dict, shown: dict | None = None) -> dict:
    """The journal with My Journals' form fields (an entry's "manuscript": paper, margin, font,
    size, line spacing, columns, label, bar, reference style, sections, numbered headings, limits,
    LaTeX class) set in it. With shown (the form as it was filled in from the journal), only the
    fields changed since are set, so what the file holds beyond the form (a structure with hints,
    styles of its own) stays."""
    j = copy.deepcopy(journal)
    f = {**LEGACY_FORM, **{k: v for k, v in (form or {}).items() if v is not None or k == "latex"}}
    for k in ("margin_mm", "size_pt", "line_spacing"):   # (an older form may hold numbers as text)
        try:
            f[k] = float(f[k])
        except (TypeError, ValueError):
            f[k] = LEGACY_FORM[k]
    f["columns"] = 2 if str(f.get("columns")) == "2" else 1
    f["limits"] = {k: _whole(v) for k, v in (f.get("limits") or {}).items() if _whole(v) is not None}
    changed = (lambda k: True) if shown is None else (lambda k: f.get(k) != shown.get(k))   # noqa: E731
    page = j.setdefault("page", {})
    if changed("paper"):
        page["paper"] = f["paper"]
    if changed("margin_mm"):
        page["margin_mm"] = f["margin_mm"]
    if changed("font") or changed("size_pt"):
        j.setdefault("text", {})["styles"] = {**((j.get("text") or {}).get("styles") or {}), **_styles(f["font"], f["size_pt"])}
    if changed("line_spacing"):
        text = j.setdefault("text", {})
        if f["line_spacing"] != 1:
            text["leading_em"] = round(0.65 + (f["line_spacing"] - 1) * 0.6, 3)
        else:
            text.pop("leading_em", None)
    if changed("numbered_headings"):
        j.setdefault("text", {})["numbered_headings"] = bool(f["numbered_headings"])
    caps = j.setdefault("captions", {})
    for k in ("label", "bar"):
        if changed(k):
            caps[k] = f[k]
    if changed("sections"):
        st = _structure(f["sections"])
        if st is None:
            j.pop("structure", None)
        else:
            words = ((j.get("structure") or {}).get("abstract") or {}).get("words")
            st["abstract"]["words"] = words
            j["structure"] = st
    if changed("limits"):
        lim = j.setdefault("limits", {})
        lim.setdefault("article_type", "Article")
        lim.setdefault("source", "the limits you gave")
        lim["limits"] = {**{k: None for k in LIMIT_KEYS}, **(lim.get("limits") or {}),
                         **{k: (f["limits"] or {}).get(k) for k in FORM_LIMITS}}
        if not any(v is not None for v in lim["limits"].values()):
            j.pop("limits")
    if j.get("structure") and (j.get("limits") or {}).get("limits", {}).get("abstract_words") and changed("limits"):
        j["structure"].setdefault("abstract", {})["words"] = j["limits"]["limits"]["abstract_words"]
    if changed("csl"):
        ref = j.setdefault("references", {})
        csl = f["csl"] or {}
        if csl.get("file"):
            ref["csl"] = None
            ref["file"] = "style.csl"
        else:
            ref["csl"] = csl.get("name") or "nature"
            ref["file"] = "style.csl"
    lx = j.setdefault("latex", {})
    if changed("columns"):
        if f["columns"] == 2:
            lx["columns"] = 2
        else:
            lx.pop("columns", None)
    if changed("latex"):
        got = f["latex"]
        for k in ("class", "options", "files", "fetch"):
            lx.pop(k, None) if k != "fetch" else None
        if got:
            lx["template"] = "publisher"
            lx["class"] = os.path.splitext(got["class"])[0]
            lx["options"] = got.get("options", "")
            lx["files"] = {name: None for name in got.get("files") or []}
        else:
            lx["template"] = "standard"
    lx.setdefault("wrapper", "journal.latex")
    return j


def _whole(value) -> int | None:
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        return None
    return v if v >= 1 else None


def form_of(journal: dict) -> dict:
    """My Journals' form, filled in from a journal file (the inverse of apply_form, as far as the
    form goes)."""
    j = complete(journal)
    normal = ((j.get("text") or {}).get("styles") or {}).get("normal") or {}
    lead = (j.get("text") or {}).get("leading_em")
    lx = j["latex"]
    ref = j["references"]
    st = j.get("structure") or {}
    sections = [p["name"] for part in ("body", "back") for p in st.get(part, [])]
    files = list(lx.get("files") or {})
    cls = lx.get("class")
    return {
        "paper": j["page"]["paper"], "margin_mm": float(j["page"].get("margin_mm", 25.0)),
        "font": normal.get("font", "Times New Roman"), "size_pt": float(normal.get("size", 12.0)),
        "line_spacing": round(1 + (lead - 0.65) / 0.6, 2) if lead else 1.0,
        "columns": 2 if lx.get("columns") == 2 else 1,
        "label": j["captions"]["label"], "bar": bool(j["captions"]["bar"]),
        "csl": {"name": ref["csl"]} if ref.get("csl") else {"file": True},
        "sections": sections, "numbered_headings": bool((j.get("text") or {}).get("numbered_headings")),
        "limits": {k: v for k, v in ((j.get("limits") or {}).get("limits") or {}).items() if k in FORM_LIMITS and v is not None},
        "latex": ({"class": next((f for f in files if os.path.splitext(f)[0] == cls and f.endswith(".cls")), f"{cls}.cls"),
                   "files": files, "options": lx.get("options", "")} if cls else None),
    }


def from_legacy(entry: dict) -> dict:
    """A journal file from an entry of the shared list as older versions kept it ({"id", "name",
    "depth_mm", "types", "note", "source", rules, "manuscript": prose's form}), or from an older
    .prosejournal's journal.json ({"format": 1, "journal": entry})."""
    if "journal" in entry and isinstance(entry["journal"], dict) and "format" not in entry["journal"]:
        entry = entry["journal"]
    name = " ".join(str(entry.get("name") or "").split())[:120]
    ident = str(entry.get("id") or "")
    if not re.fullmatch(r"[a-z0-9_]{1,40}", ident):
        ident = slug(name)
    j: dict = {"format": FORMAT, "id": ident, "name": name, "version": "1.0.0", "kind": "journal", "publisher": "My journals",
               "verified": {"status": "unverified"}}
    fig = {"depth_mm": entry.get("depth_mm"), "types": copy.deepcopy(entry.get("types") or []),
           "note": str(entry.get("note") or ""), "source": str(entry.get("source") or "added by you")}
    fig.update({k: copy.deepcopy(entry[k]) for k in RULE_KEYS if isinstance(entry.get(k), dict)})
    j["figures"] = fig
    if isinstance(entry.get("manuscript"), dict):
        j = apply_form(j, entry["manuscript"])
    return j


def legacy_view(journal: dict) -> dict:
    """The keys an older version of either app reads from an entry of the shared list."""
    fig = journal.get("figures") or {}
    out = {"id": journal["id"], "name": journal["name"], "depth_mm": fig.get("depth_mm"),
           "types": copy.deepcopy(fig.get("types") or []), "note": fig.get("note", ""), "source": fig.get("source") or "added by you",
           "user": True}
    out.update(rules_of(journal))
    out["manuscript"] = form_of(journal)
    return out


def read_any(path: str) -> dict:
    """A journal from a file: this format (JSON), or an older .prosejournal (a zip of journal.json,
    journal.latex and style.csl), upgraded, its wrapper and reference style taken in. Checked:
    JournalFileError says what's wrong."""
    name = os.path.basename(path)
    if zipfile.is_zipfile(path):
        try:
            with zipfile.ZipFile(path) as z:
                data = json.loads(z.read("journal.json"))
                wrap = z.read("journal.latex").decode("utf-8") if "journal.latex" in z.namelist() else None
                style = z.read("style.csl").decode("utf-8") if "style.csl" in z.namelist() else None
        except (KeyError, ValueError, zipfile.BadZipFile) as exc:
            raise JournalFileError([f"it isn't a journal file ({exc})"], name) from None
        if isinstance(data, dict) and data.get("format") == FORMAT and "id" in data and "journal" not in data:
            j = data   # (this format, zipped)
        else:
            j = from_legacy(data.get("journal") or {} if isinstance(data, dict) else {})
            if wrap and (j.get("latex") or {}).get("class"):
                j["latex"]["wrapper_text"] = wrap
            if style and "<style" in style:
                j.setdefault("references", {})["csl_text"] = style
        return check(j, name)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except UnicodeDecodeError:
        raise JournalFileError(["it isn't a journal file (not text)"], name) from None
    except ValueError as exc:
        raise JournalFileError([f"it isn't valid JSON: {exc}"], name) from None
    return check(data, name)


def dumps(journal: dict) -> str:
    """A journal file's text: indented, with accents and symbols as themselves."""
    return json.dumps(journal, indent=1, ensure_ascii=False) + "\n"


# Command line ------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m labapp.journalfile", description="Check or upgrade journal files.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="say what's wrong with each journal file")
    v.add_argument("paths", nargs="+", metavar="FILE")
    v.add_argument("--beside", metavar="FOLDER", help="also check the files it names (wrapper, class files) in this folder")
    v.add_argument("--strict", action="store_true", help="count fields this version doesn't know as errors")
    u = sub.add_parser("upgrade", help="an older journal file (.prosejournal) as a new one")
    u.add_argument("old")
    u.add_argument("new")
    args = ap.parse_args(argv)
    if args.cmd == "upgrade":
        j = read_any(args.old)
        with open(args.new, "w", encoding="utf-8") as fh:
            fh.write(dumps(j))
        print(f"{args.new}: {j['name']}, format {FORMAT}")
        return 0
    bad = 0
    for path in args.paths:
        try:
            if zipfile.is_zipfile(path):
                data = read_any(path)
            else:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
        except JournalFileError as exc:
            print(f"{path}: {exc}")
            bad += 1
            continue
        except (OSError, ValueError) as exc:
            print(f"{path}: can't be read as JSON ({exc})")
            bad += 1
            continue
        errors, warnings = validate(data, args.beside)
        for e in errors:
            print(f"{path}: error: {e}")
        for w in warnings:
            print(f"{path}: {'error' if args.strict else 'note'}: {w}")
        if errors or (args.strict and warnings):
            bad += 1
        elif not warnings:
            print(f"{path}: fine ({data.get('name')})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
