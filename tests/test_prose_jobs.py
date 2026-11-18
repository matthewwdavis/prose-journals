"""The jobs that need prose (preview, LaTeX, Word layout): each skips, and says why, without prose or
the publisher's files; the layout comparison; the summary comment; and, when a copy of prose's
source is here, a real preview and LaTeX compile in a scratch prose."""
import hashlib
import os
import shutil

import pytest
from conftest import ROOT, good_journal, run, write

from tools import common, measure_layout, publisher, reverify, summarize

PROSE_SRC = os.environ.get("LIBRARY_TEST_PROSE_SRC") or ""   # (a copy of prose's source; the slow test is skipped without one)
HAVE_PROSE = os.path.isfile(os.path.join(PROSE_SRC, "prose", "__init__.py"))
HAVE_TECTONIC = any(os.path.isfile(p) for p in (shutil.which("tectonic") or "", os.path.join(os.path.dirname(os.sys.executable), "tectonic")))


@pytest.mark.parametrize("tool,job", [("render_preview.py", "preview"), ("compile_latex.py", "latex"), ("measure_layout.py", "word-layout")])
def test_without_prose_each_job_skips_and_says_so(tmp_path, tool, job):
    report = tmp_path / "r.md"
    got = run(tool, os.path.join(ROOT, "journals", "genome_research.json"), "--report", report)
    assert got.returncode == 0, got.stdout + got.stderr
    r = common.read_report(report.read_text())
    assert r["job"] == job and r["result"] == "skipped"
    assert "was skipped: prose wasn't given (PROSE_SRC=none)" in r["lines"][0]


def test_a_prose_src_that_isnt_prose_is_said(tmp_path):
    got = run("render_preview.py", os.path.join(ROOT, "journals", "nature.json"), "--report", tmp_path / "r.md",
              env={"PROSE_SRC": str(tmp_path / "nowhere")})
    assert got.returncode == 0
    assert "isn't a copy of prose's source (no prose/__init__.py)" in (tmp_path / "r.md").read_text()


@pytest.mark.skipif(not HAVE_TECTONIC, reason="Tectonic isn't here")
def test_missing_publisher_files_skip_the_latex_check(tmp_path):
    fake = tmp_path / "prose-src"
    (fake / "prose").mkdir(parents=True)
    (fake / "prose" / "__init__.py").write_text("")
    j = good_journal()
    j["latex"] = {"template": "publisher", "class": "sn-jnl", "files": {"sn-jnl.cls": None}}
    p = write(tmp_path / "journals" / "tree_genetics_genomes.json", j)
    report = tmp_path / "r.md"
    got = run("compile_latex.py", p, "--report", report, "--cache", tmp_path / "cache", env={"PROSE_SRC": str(fake)})
    assert got.returncode == 0
    r = common.read_report(report.read_text())
    assert r["result"] == "skipped"
    assert r["lines"][0].endswith("the LaTeX check needs the publisher's sn-jnl.cls, which the library never holds: skipped")
    j["latex"]["fetch"] = {"store": "springer", "url": "https://example.org/sn.zip", "files": {"sn-jnl.cls": "0" * 64}}
    write(p, j)
    run("compile_latex.py", p, "--report", report, "--cache", tmp_path / "cache", env={"PROSE_SRC": str(fake)})
    assert "it can be downloaded from the publisher, but downloading wasn't allowed: --download" in report.read_text()


def test_publisher_files_from_a_folder_with_their_checksums(tmp_path):
    (tmp_path / "mine").mkdir()
    (tmp_path / "mine" / "sn-jnl.cls").write_text("% a class\n")
    good = hashlib.sha256(b"% a class\n").hexdigest()
    j = {"id": "x", "latex": {"files": {"sn-jnl.cls": good, "sn-nature.bst": None}}}
    found, missing, notes = publisher.gather(j, folders=[str(tmp_path / "mine")], cache=str(tmp_path / "cache"))
    assert list(found) == ["sn-jnl.cls"] and missing == ["sn-nature.bst"] and notes == []
    j["latex"]["files"]["sn-jnl.cls"] = "0" * 64
    _, _, notes = publisher.gather(j, folders=[str(tmp_path / "mine")], cache=str(tmp_path / "cache"))
    assert notes == ["sn-jnl.cls differs from the copies the journal file names (the publisher may have revised it); used as it is"]


def test_publisher_files_of_an_earlier_package_or_corrected_by_prose_are_the_journals(tmp_path):
    """latex.fetch takes the publisher's earlier packages ("earlier") and the copies prose's own
    corrections make ("corrections"), as Wiley's journals do since Wiley's package of 18 Nov 2026."""
    sha = lambda b: hashlib.sha256(b).hexdigest()   # noqa: E731
    (tmp_path / "mine").mkdir()
    j = {"id": "x", "latex": {"fetch": {"store": "s", "files": {"a.cls": sha(b"new\n"), "a.bst": sha(b"new bst\n")},
                                        "earlier": [{"date": "2026-11-13", "files": {"a.cls": sha(b"old\n")}}],
                                        "corrections": [{"file": "a.bst", "from": sha(b"new bst\n"), "to": sha(b"fixed bst\n"),
                                                         "edits": [[0, 1, ["fixed bst"]]], "why": "a reason"}]}}}
    for cls, bst, said in ((b"old\n", b"fixed bst\n", []), (b"new\n", b"new bst\n", []), (b"other\n", b"new bst\n", ["a.cls"])):
        (tmp_path / "mine" / "a.cls").write_bytes(cls)
        (tmp_path / "mine" / "a.bst").write_bytes(bst)
        found, missing, notes = publisher.gather(j, folders=[str(tmp_path / "mine")], cache=str(tmp_path / "cache"))
        assert sorted(found) == ["a.bst", "a.cls"] and missing == []
        assert [n.split(" ")[0] for n in notes] == said


def test_nothing_is_downloaded_into_the_library(tmp_path):
    j = {"id": "x", "latex": {"fetch": {"store": "s", "url": "https://example.org/p.zip", "files": {"a.cls": None}}}}
    _, missing, notes = publisher.gather(j, cache=os.path.join(ROOT, "reports", "cache"), download=True)
    assert missing == ["a.cls"]
    assert notes == ["the cache is inside the library's folder, where a publisher's file must never be; nothing was downloaded"]
    assert not os.path.exists(os.path.join(ROOT, "reports", "cache", "s"))


def test_layouts_compared_within_the_tolerances():
    given = {"measured_from": "then", "fonts": {"A": "Arial"}, "hand": ["styles.h1.before_pt"],
             "page": {"width_mm": 210.0, "margins_mm": {"top": 20.0}}, "body": {"size": 10.0, "font": "Arial", "justify": True},
             "styles": {"h1": {"size": 12.0, "before_pt": 30.0}}}
    near = {"measured_from": "now", "fonts": {"A": "Helvetica"},
            "page": {"width_mm": 210.4, "margins_mm": {"top": 19.6}}, "body": {"size": 10.15, "font": "Arial", "justify": True},
            "styles": {"h1": {"size": 12.0, "before_pt": 12.0}}}
    assert measure_layout.compare(given, near) == []
    far = {**near, "page": {"width_mm": 211.0, "margins_mm": {"top": 20.0}}, "body": {"size": 10.3, "font": "Times", "justify": True},
           "styles": {"h1": {"size": 12.0, "before_pt": 12.0}, "h2": {"size": 11.0}}}
    extra = []
    assert measure_layout.compare(given, far, extra=extra) == [
        "body.font: the file says \"Arial\", measured \"Times\"",
        "body.size: the file says 10, measured 10.3 (more than 0.2 pt apart)",
        "page.width_mm: the file says 210, measured 211 (more than 0.5 mm apart)",
    ]
    assert extra == ["styles.h2: measured {\"size\": 11.0}, which the file doesn't give"]


def test_the_summary_comment(tmp_path):
    reports = [
        common.write_report(None, "validate", "passed", [], "schema (strict), id = file name"),
        common.write_report(None, "latex", "skipped", ["journals/x.json: the LaTeX check needs the publisher's sn-jnl.cls, which the library "
                                                       "never holds: skipped"], "LaTeX skipped"),
        common.write_report(None, "preview", "passed", [], "preview made (PDF, Word, 2 page pictures)"),
        common.write_report(None, "word-layout", "failed", ["journals/x.json: error: word.layout.body.size: the file says 13, measured 10"],
                            "Word layout compared (1 differences)", "<details>x</details>"),
    ]
    text, ok = summarize.summarize(reports, "journals/x.json", "https://example.org/run")
    assert not ok
    lines = text.splitlines()
    assert lines[0] == summarize.MARKER
    assert lines[1] == "### Library check: journals/x.json: needs changes"
    assert lines[3] == "validate passed · latex skipped · word-layout failed · preview passed"
    assert "- journals/x.json: error: word.layout.body.size: the file says 13, measured 10" in lines
    assert ("Checked: schema (strict), id = file name; LaTeX skipped; Word layout compared (1 differences); "
            "preview [made (PDF, Word, 2 page pictures)](https://example.org/run)") in lines
    assert lines[-1] == "<details>x</details>"
    ok_text, ok = summarize.summarize(reports[:3])
    assert ok and "### Library check: passed" in ok_text


def test_reverify_lists_journals_due(tmp_path, monkeypatch):
    j = good_journal()
    write(tmp_path / "tree_genetics_genomes.json", j)
    monkeypatch.setenv("LIBRARY_TODAY", "2028-06-02")
    items = reverify.due(str(tmp_path))
    assert [i["file"] for i in items] == ["journals/tree_genetics_genomes.json"]
    text = reverify.markdown(items)
    assert text.startswith("1 journal is due to be checked against its guidelines again")
    assert "- [ ] **Tree Genetics & Genomes** (`journals/tree_genetics_genomes.json`, community): last checked 2026-11-01" in text
    monkeypatch.setenv("LIBRARY_TODAY", "2027-01-01")
    assert reverify.due(str(tmp_path)) == []


def _settings_snapshot() -> dict:
    """The real settings folders' files and times (read only), to show the scratch prose never touched them."""
    out = {}
    for d in ("~/Library/Application Support/prose", "~/Library/Application Support/mosaic-prose"):
        d = os.path.expanduser(d)
        if os.path.isdir(d):
            for name in os.listdir(d):
                p = os.path.join(d, name)
                out[p] = os.stat(p).st_mtime_ns
    return out


@pytest.mark.slow
@pytest.mark.skipif(not HAVE_PROSE, reason="no copy of prose's source here")
def test_a_real_preview_and_latex_compile(tmp_path):
    before = _settings_snapshot()
    report = tmp_path / "preview.md"
    got = run("render_preview.py", os.path.join(ROOT, "journals", "genome_research.json"), "--out", tmp_path / "out",
              "--report", report, env={"PROSE_SRC": PROSE_SRC})
    assert got.returncode == 0, got.stdout + got.stderr
    r = common.read_report(report.read_text())
    assert r["result"] == "passed", report.read_text()
    assert r["checked"] == "preview made (PDF, Word, 2 page pictures)"
    for name in ("sample.pdf", "sample.docx", "page-1.png", "page-2.png"):
        assert (tmp_path / "out" / "genome_research" / name).stat().st_size > 1000
    assert open(tmp_path / "out" / "genome_research" / "sample.pdf", "rb").read(5) == b"%PDF-"
    if HAVE_TECTONIC:
        report = tmp_path / "latex.md"
        got = run("compile_latex.py", os.path.join(ROOT, "journals", "genome_research.json"), "--report", report,
                  "--cache", tmp_path / "cache", env={"PROSE_SRC": PROSE_SRC})
        assert got.returncode == 0, got.stdout + got.stderr
        assert common.read_report(report.read_text())["result"] == "passed"
    assert _settings_snapshot() == before
