"""The pieces the GitHub checks are made of, run here: taking a pull request's journal files as data
(.github/scripts/take_journals.sh), finding what to check (find_request.sh, by hand), downloads only
from addresses the library already uses, the summary's missing jobs and result, Word's stand-in
fonts not compared, and the preview site's index."""
import json
import os
import shutil
import subprocess

import pytest
from conftest import ROOT, good_journal, run, write

from tools import build_site, common, measure_layout, publisher, summarize

SCRIPTS = os.path.join(ROOT, ".github", "scripts")
HAVE_BASH_GIT = bool(shutil.which("bash") and shutil.which("git"))
needs_git = pytest.mark.skipif(not HAVE_BASH_GIT, reason="needs bash and git")


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false", *args],
                          cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture()
def pull_request(tmp_path):
    """A library ("origin") with main, a clone of it, and pull request 7's head in origin's refs/pull/7/head."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    write(origin / "journals" / "tree_genetics_genomes.json", good_journal())
    (origin / "tools.txt").write_text("the library's tools\n")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "main")
    git(origin, "checkout", "-q", "-b", "pr")
    j = good_journal()
    j.update(id="new_journal", name="A New Journal", issn={})
    write(origin / "journals" / "new_journal.json", j)
    (origin / "tools.txt").write_text("the pull request's tools\n")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "pr")
    head = git(origin, "rev-parse", "HEAD")
    git(origin, "update-ref", "refs/pull/7/head", head)
    git(origin, "checkout", "-q", "main")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(origin), str(clone))
    return origin, clone, head


def take(clone, *args):
    return subprocess.run(["bash", os.path.join(SCRIPTS, "take_journals.sh"), *args], cwd=clone, capture_output=True, text=True)


@needs_git
def test_only_the_pull_requests_journals_are_taken(pull_request):
    origin, clone, head = pull_request
    got = take(clone, "7", head, "main")
    assert got.returncode == 0, got.stdout + got.stderr
    assert (clone / "journals" / "new_journal.json").is_file()
    assert (clone / "tools.txt").read_text() == "the library's tools\n"   # (not the pull request's)
    assert (clone / "changed.txt").read_text().split() == ["journals/new_journal.json", "tools.txt"]


@needs_git
def test_a_pull_request_pushed_again_is_not_taken(pull_request):
    origin, clone, head = pull_request
    got = take(clone, "7", "0" * 40, "main")
    assert got.returncode == 1 and "it was pushed again" in got.stdout


@needs_git
def test_a_link_in_journals_is_refused(pull_request):
    origin, clone, head = pull_request
    git(origin, "checkout", "-q", "pr")
    os.symlink("../tools.txt", origin / "journals" / "sneaky.json")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "a link")
    head = git(origin, "rev-parse", "HEAD")
    git(origin, "update-ref", "refs/pull/7/head", head)
    git(origin, "checkout", "-q", "main")
    got = take(clone, "7", head, "main")
    assert got.returncode == 1 and "holds links or other repositories" in got.stdout and "journals/sneaky.json" in got.stdout
    assert not (clone / "journals" / "new_journal.json").exists()


@needs_git
@pytest.mark.parametrize("target,out", [
    ("journals/nature.json", {"go": "true", "pr": "", "file": "journals/nature.json", "changed": "journals/nature.json"}),
    ("../etc/passwd", {"go": "false"}),
])
def test_a_check_by_hand_of_a_file_on_main(tmp_path, target, out):
    outfile = tmp_path / "out.txt"
    env = {**os.environ, "EVENT": "workflow_dispatch", "TARGET": target, "RUN_HEAD_SHA": "", "RUN_HEAD_OWNER": "", "RUN_HEAD_BRANCH": "",
           "RUN_CONCLUSION": "", "GITHUB_REPOSITORY": "o/r", "DEFAULT_BRANCH": "main", "GITHUB_ACTOR": "the-maintainer",
           "GITHUB_OUTPUT": str(outfile)}
    got = subprocess.run(["bash", os.path.join(SCRIPTS, "find_request.sh")], cwd=ROOT, env=env, capture_output=True, text=True)
    assert got.returncode == 0, got.stderr
    said = dict(ln.split("=", 1) for ln in outfile.read_text().splitlines())
    assert {k: said.get(k) for k in out} == out


def test_a_download_only_from_an_address_the_library_already_uses(tmp_path, monkeypatch):
    listing = tmp_path / "known.txt"
    listing.write_text("https://authors.wiley.com/asset/WileyDesign.zip\n")
    monkeypatch.setenv("LIBRARY_DOWNLOAD_ALLOWED", str(listing))
    j = {"id": "x", "latex": {"fetch": {"store": "s", "url": "https://example.org/evil.zip", "files": {"a.cls": None}}}}
    _, missing, notes = publisher.gather(j, cache=str(tmp_path / "cache"), download=True)
    assert missing == ["a.cls"] and "isn't an address the library's journals already use" in notes[0]
    assert publisher.allowed("https://authors.wiley.com/asset/WileyDesign.zip")
    got = run("publisher.py", "--known-urls", os.path.join(ROOT, "journals"))
    assert got.returncode == 0 and got.stdout.split() == ["https://authors.wiley.com/asset/WileyDesign.zip"]


def test_the_summary_says_which_jobs_waited_for_validate_and_the_result(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "validate.md").write_text("<!-- job: validate; result: failed -->\n- journals/x.json: error: no\nChecked: schema\n")
    (reports / "index.md").write_text("<!-- job: index; result: passed -->\nChecked: index\n")
    state = tmp_path / "state.txt"
    got = run("summarize.py", "--dir", reports, "--expect", "validate,index,latex,word-layout,preview", "--state-out", state)
    assert "latex not run (it runs once validate passes)" in got.stdout and state.read_text() == "failure\n"
    (reports / "validate.md").write_text("<!-- job: validate; result: passed -->\nChecked: schema\n")
    got = run("summarize.py", "--dir", reports, "--expect", "validate,index,latex", "--state-out", state)
    assert "error: the latex job stopped before it could report" in got.stdout and state.read_text() == "failure\n"
    (reports / "latex.md").write_text("<!-- job: latex; result: passed -->\nChecked: LaTeX compiles\n")
    got = run("summarize.py", "--dir", reports, "--expect", "validate,index,latex", "--state-out", state)
    assert state.read_text() == "success\n"


def test_words_stand_in_fonts_are_not_compared_but_the_pdfs_fonts_are():
    given = {"body": {"pdf_font": "LMRoman10-Regular", "font": "Century Schoolbook", "size": 10.0},
             "styles": {"h1": {"pdf_font": "LMRoman12-Bold", "font": "Century Schoolbook"}}}
    here = json.loads(json.dumps(given))
    here["body"]["font"] = here["styles"]["h1"]["font"] = "Georgia"
    extra = []
    assert measure_layout.compare(given, here, extra=extra) == []
    assert extra == ['body.font and 1 more: Word\'s stand-in font chosen here is "Georgia", the file says "Century Schoolbook" '
                     "(chosen among the Word fonts installed where it's measured; not compared)"]
    here["body"]["pdf_font"] = "Times-Roman"
    assert measure_layout.compare(given, here) == ['body.pdf_font: the file says "LMRoman10-Regular", measured "Times-Roman"']


def test_the_preview_site_lists_every_journal(tmp_path):
    site = tmp_path / "site"
    (site / "previews" / "nature").mkdir(parents=True)
    (site / "previews" / "nature" / "page-1.png").write_bytes(b"png")
    assert build_site.main([str(site)]) == 0
    page = (site / "index.html").read_text()
    assert page.count("<li>") == 16 and 'href="previews/nature/page-1.png"' in page and "no preview yet" in page
    assert (site / ".nojekyll").is_file()


def test_the_preview_site_says_which_previews_couldnt_be_made_and_why(tmp_path, monkeypatch, capsys):
    site, reports = tmp_path / "site", tmp_path / "reports"
    (site / "previews" / "nature").mkdir(parents=True)
    (site / "previews" / "nature" / "page-1.png").write_bytes(b"png")
    common.write_report(str(reports / "nature.md"), "preview", "passed", [], "preview made (PDF)")
    common.write_report(str(reports / "plant_journal.md"), "preview", "failed",
                        ["journals/plant_journal.json: the preview couldn't be made: ValueError: The LaTeX class isn't among the journal's files."],
                        "preview not made")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert build_site.main([str(site), "--reports", str(reports), "--missing-out", str(tmp_path / "missing.txt")]) == 0
    page = (site / "index.html").read_text()
    assert "no preview yet" not in page
    assert "The preview couldn't be made: ValueError: The LaTeX class isn&#x27;t among the journal&#x27;s files." in page
    assert page.count("The preview couldn't be made") == 15   # (every journal but Nature: the others have no report)
    missing = (tmp_path / "missing.txt").read_text().splitlines()
    assert len(missing) == 15 and "The Plant Journal: ValueError: The LaTeX class isn't among the journal's files." in missing
    out = capsys.readouterr().out
    assert out.count("::error title=Preview not made::") == 15
