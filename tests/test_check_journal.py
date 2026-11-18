"""check_journal.py: a good file passes, and each of the library's rules refuses what it should."""
import json
import os

import pytest
from conftest import ROOT, good_journal, run, write


def check(library, path, *args, env=None):
    return run("check_journal.py", path, "--strict", "--journals", library / "journals", "--codeowners", library / "CODEOWNERS",
               *args, env=env)


def changed(library, **edits):
    """The good journal with some fields changed, written as a new file in the library."""
    j = good_journal()
    j.update(edits)
    return write(library / "journals" / f"{j['id']}.json", j)


def test_a_good_file_passes_and_reports(library, tmp_path):
    report = tmp_path / "r.md"
    got = check(library, library / "journals" / "tree_genetics_genomes.json", "--author", "someone", "--report", report)
    assert got.returncode == 0, got.stdout
    text = report.read_text()
    assert text.startswith("<!-- job: validate; result: passed -->")
    assert "\nChecked: schema (strict), id = file name" in text


def test_every_library_journal_passes():
    files = sorted(os.path.join(ROOT, "journals", n) for n in os.listdir(os.path.join(ROOT, "journals")))
    assert len(files) >= 16
    got = run("check_journal.py", "--strict", "--author", "matthewwdavis", *files)
    assert got.returncode == 0, got.stdout
    assert "error" not in got.stdout


def test_the_id_has_to_be_the_file_name(library):
    p = write(library / "journals" / "tree_genetics.json", good_journal())
    got = check(library, p)
    assert got.returncode == 1
    assert "so the file has to be called journals/tree_genetics_genomes.json" in got.stdout


def test_format_errors_are_in_the_validators_words(library):
    p = changed(library, legends={"sep": ":"})
    got = check(library, p)
    assert got.returncode == 1
    assert "legends.sep is the text \":\"" in got.stdout


def test_strict_counts_unknown_fields(library):
    p = changed(library, legnds={"sep": "."})
    assert check(library, p).returncode == 1
    lenient = run("check_journal.py", p, "--journals", library / "journals")
    assert lenient.returncode == 0 and "note: legnds isn't a field this version knows" in lenient.stdout


def test_verified_only_from_a_maintainer(library):
    p = changed(library, verified={"status": "verified", "by": "x", "date": "2026-11-17"})
    got = check(library, p, "--author", "a-stranger")
    assert got.returncode == 1
    assert "only a maintainer sets; a-stranger isn't one" in got.stdout
    assert check(library, p, "--author", "The-Maintainer").returncode == 0
    alone = check(library, p)
    assert alone.returncode == 0 and "wasn't given (--author)" in alone.stdout


def test_a_big_string_is_refused(library):
    p = changed(library, evidence="x" * (64 * 1024 + 1))
    got = check(library, p)
    assert got.returncode == 1
    assert "evidence is 64 KB of text; no text in a journal file may be over 64 KB" in got.stdout


def test_a_big_wrapper_is_refused(library):
    p = changed(library, latex={"template": "publisher", "class": "sn-jnl", "wrapper_text": "%" * (65 * 1024)})
    got = check(library, p)
    assert got.returncode == 1
    assert "latex.wrapper_text is 65 KB; a LaTeX wrapper may be at most 64 KB" in got.stdout


def test_a_big_file_is_refused(library):
    p = changed(library, evidence=["y" * 60000] * 5)
    got = check(library, p)
    assert got.returncode == 1
    assert "a journal file may be at most 256 KB" in got.stdout


def test_csl_text_with_a_repository_style_is_refused(library):
    p = changed(library, references={"csl": "tree-genetics-and-genomes", "csl_text": "<style>…</style>"})
    got = check(library, p)
    assert got.returncode == 1
    assert "leave references.csl_text out" in got.stdout


def test_csl_text_of_its_own_needs_a_look(library):
    p = changed(library, references={"csl": None, "csl_text": "<style><info><rights>All mine</rights></info></style>"})
    got = check(library, p)
    assert got.returncode == 0
    assert "note: references.csl_text: the style written in doesn't carry the CSL repository's licence" in got.stdout


@pytest.mark.parametrize("field,value,words", [
    ("homepage", "ftp://example.org/journal", "a web address in a journal file starts with http:// or https://"),
    ("evidence", "see javascript:alert(1)", "holds an address that isn't http(s)"),
])
def test_only_http_addresses(library, field, value, words):
    p = changed(library, **{field: value})
    got = check(library, p)
    assert got.returncode == 1 and words in got.stdout


def test_words_that_look_like_a_scheme_are_fine(library):
    assert check(library, changed(library, evidence="Source data: one file per figure")).returncode == 0


def test_names_at_most_120_characters(library):
    got = check(library, changed(library, aliases=["A" * 121]))
    assert got.returncode == 1 and "aliases[1] is 121 characters long; names may be at most 120" in got.stdout


def test_duplicates_of_another_journal(library):
    other = good_journal()
    other.update(id="tgg", issn={"online": "1614-2950"}, name="TREE GENETICS & GENOMES")
    write(library / "journals" / "tgg.json", other)
    got = check(library, library / "journals" / "tree_genetics_genomes.json")
    assert got.returncode == 1
    assert "the library already has a journal called TREE GENETICS & GENOMES (journals/tgg.json)" in got.stdout
    assert "its ISSN 1614-2950 is already" in got.stdout


def test_changed_files_only_one_journal(library, tmp_path):
    lst = tmp_path / "changed.txt"
    lst.write_text("journals/tree_genetics_genomes.json\ntools/check_journal.py\n")
    got = run("check_journal.py", "--changed-files", lst, "--journals", library / "journals")
    assert got.returncode == 1
    assert "tools/check_journal.py: error: a pull request to the library may change only journal files" in got.stdout
    got = run("check_journal.py", "--changed-files", "journals/a.json,journals/b.json")
    assert got.returncode == 1 and "it changes 2 journal files" in got.stdout


def test_a_change_raises_the_version(library, tmp_path):
    base = tmp_path / "base"
    write(base / "journals" / "tree_genetics_genomes.json", good_journal())
    same = check(library, library / "journals" / "tree_genetics_genomes.json", "--base", base)
    assert same.returncode == 0, same.stdout   # (unchanged: nothing to raise)
    p = changed(library, aliases=["Tree Genet Genomes"])
    got = check(library, p, "--base", base)
    assert got.returncode == 1 and "its version is still 1.0.0; raise it above 1.0.0" in got.stdout
    assert check(library, changed(library, aliases=["Tree Genet Genomes"], version="1.1.0"), "--base", base).returncode == 0
    new = tmp_path / "empty"
    (new / "journals").mkdir(parents=True)
    assert check(library, p, "--base", new).returncode == 0   # (a new journal)


def test_an_unknown_base_is_said(library):
    got = check(library, library / "journals" / "tree_genetics_genomes.json", "--base", "no-such-ref-anywhere")
    assert got.returncode == 1 and "isn't a folder or a git ref" in got.stdout


def test_old_limits_are_due_again(library):
    got = check(library, library / "journals" / "tree_genetics_genomes.json", env={"LIBRARY_TODAY": "2028-06-02"})
    assert got.returncode == 0
    assert "note: limits.checked is 2026-11-01, over 18 months ago" in got.stdout
    fresh = check(library, library / "journals" / "tree_genetics_genomes.json", env={"LIBRARY_TODAY": "2028-04-30"})
    assert "18 months" not in fresh.stdout


def test_sources_on_another_site_are_a_warning(library):
    p = changed(library, sources=[{"url": "https://example.com/copied-guidelines", "what": "a copy"},
                                  {"url": "https://web.archive.org/web/2025/https://link.springer.com/journal/11295", "what": "archived"}])
    got = check(library, p)
    assert got.returncode == 0
    assert "warning: sources are on example.com, not the journal's or publisher's own site (springer.com)" in got.stdout


def test_report_lists_problems_then_checked(library, tmp_path):
    report = tmp_path / "r.md"
    check(library, write(library / "journals" / "x.json", good_journal()), "--report", report)
    lines = report.read_text().splitlines()
    assert lines[0] == "<!-- job: validate; result: failed -->"
    assert lines[1].startswith("- ") and "error:" in lines[1]
    assert lines[-1].startswith("Checked: ")
    data = json.loads((library / "journals" / "x.json").read_text())
    assert data["id"] == "tree_genetics_genomes"


def test_a_maintainers_pull_request_may_change_other_files(library, tmp_path):
    lst = tmp_path / "changed.txt"
    lst.write_text("README.md\n.github/workflows/check.yml\n")
    got = run("check_journal.py", "--changed-files", lst, "--journals", library / "journals", "--author", "The-Maintainer",
              "--codeowners", library / "CODEOWNERS")
    assert got.returncode == 0, got.stdout
    assert "it changes 2 file(s) besides journals (README.md, .github/workflows/check.yml), as a maintainer may" in got.stdout
    got = run("check_journal.py", "--changed-files", lst, "--journals", library / "journals", "--author", "someone",
              "--codeowners", library / "CODEOWNERS")
    assert got.returncode == 1 and "README.md: error: a pull request to the library may change only journal files" in got.stdout


@pytest.mark.parametrize("latex,words", [
    ({"wrapper_text": "\\input{/etc/passwd}"}, "\\input{/etc/passwd} names a file outside the manuscript's folder"),
    ({"wrapper_text": "\\input ../../secret.tex"}, "\\input{../../secret.tex} names a file outside"),
    ({"wrapper_text": "\\IfFileExists{~/x}{}{}"}, "\\IfFileExists{~/x} names a file outside"),
    ({"wrapper_text": "\\newread\\f \\openin\\f=x"}, "uses \\openin, which a journal's LaTeX may not"),
    ({"wrapper_text": "\\immediate\\write18{ls}"}, "uses \\immediate, \\write18"),
    ({"wrapper_text": "\\catcode`\\|=0"}, "uses \\catcode"),
    ({"class": "article}\\input{x"}, "latex.class is \"article}\\input{x\"; it can only be a class's name"),
    ({"options": "a4paper]{article}\\input{x}%"}, "latex.options can only list the class's options"),
])
def test_latex_that_reaches_outside_the_manuscript_is_refused(library, latex, words):
    j = good_journal()
    j["latex"] = {"wrapper": "journal.latex", **latex}
    path = write(library / "journals" / "tree_genetics_genomes.json", j)
    got = check(library, path)
    assert got.returncode == 1 and words in got.stdout, got.stdout


def test_latex_that_keeps_to_the_manuscript_passes(library):
    j = good_journal()
    j["latex"] = {"wrapper": "journal.latex", "class": "sn-jnl", "options": "pdflatex,sn-nature",
                  "wrapper_text": "\\documentclass{article}\\IfFileExists{logo.pdf}{\\includegraphics[width=2cm]{logo.pdf}}{}"
                                  "\\input{body}\\bibliography{$bibstem$}\\csname prose@sep\\endcsname"}
    path = write(library / "journals" / "tree_genetics_genomes.json", j)
    got = check(library, path)
    assert "latex." not in got.stdout, got.stdout
