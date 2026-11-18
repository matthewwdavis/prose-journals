"""build_index.py: index.json in format 2, exactly as the apps read it; clashes refused; the same
journals always make the same index."""
import hashlib
import json
import os
import re

from conftest import ROOT, good_journal, run, write

from tools import build_index, common

FIELDS = ["id", "name", "aliases", "issn", "publisher", "group", "kind", "file", "version", "sha256", "size", "verified", "updated",
          "needs", "summary", "changes"]


def test_format_2():
    index, problems = build_index.build()
    assert problems == []
    assert list(index) == ["format", "generated", "schema", "journals"]
    assert index["format"] == 2 and index["schema"] == 1
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", index["generated"])
    names = [e["name"].casefold() for e in index["journals"]]
    assert names == sorted(names)
    for e in index["journals"]:
        assert list(e) == FIELDS
        raw = open(os.path.join(ROOT, e["file"]), "rb").read()
        assert e["file"] == f"journals/{e['id']}.json"
        assert e["sha256"] == hashlib.sha256(raw).hexdigest() and e["size"] == len(raw)
        assert e["verified"] in ("verified", "community", "unverified")
        assert re.fullmatch(r"\d{4}-\d\d-\d\d", e["updated"])
        assert all(re.fullmatch(r"\d{4}-\d{3}[\dX]", n) for n in e["issn"])
        assert e["summary"].startswith("figures ") and "reference style" in e["summary"]
        assert isinstance(e["needs"], list) and e["changes"] == []
    nature = next(e for e in index["journals"] if e["id"] == "nature")
    assert nature["needs"] == ["sn-jnl.cls", "sn-nature.bst"] and nature["verified"] == "verified"


def test_the_committed_index_is_current():
    got = run("build_index.py", "--check")
    assert got.returncode == 0, got.stdout


def test_the_same_journals_make_the_same_index(tmp_path):
    write(tmp_path / "journals" / "tree_genetics_genomes.json", good_journal())
    a = run("build_index.py", "--journals", tmp_path / "journals", "--out", tmp_path / "a.json")
    b = run("build_index.py", "--journals", tmp_path / "journals", "--out", tmp_path / "b.json")
    assert a.returncode == 0 and b.returncode == 0
    ta, tb = (tmp_path / "a.json").read_text(), (tmp_path / "b.json").read_text()
    strip = lambda t: re.sub(r'"generated": "[^"]+"', "", t)   # noqa: E731
    assert strip(ta) == strip(tb)
    assert ta.endswith("}\n") and ta.startswith('{\n "format": 2,')
    e = json.loads(ta)["journals"][0]
    assert e["issn"] == ["1614-2942", "1614-2950"] and e["group"] == "Springer Nature"
    assert e["updated"] == "2026-11-17"   # (no commit: the file's verified.date)
    assert e["summary"].startswith("figures 84 and 174 mm, up to 234 mm deep; A4; reference style tree-genetics-and-genomes")


def test_rebuilding_with_nothing_new_leaves_the_file(tmp_path):
    write(tmp_path / "journals" / "tree_genetics_genomes.json", good_journal())
    out = tmp_path / "index.json"
    run("build_index.py", "--journals", tmp_path / "journals", "--out", out)
    before = out.read_text()
    got = run("build_index.py", "--journals", tmp_path / "journals", "--out", out)
    assert "already up to date" in got.stdout and out.read_text() == before


def clash(tmp_path, **edits):
    write(tmp_path / "journals" / "tree_genetics_genomes.json", good_journal())
    other = good_journal()
    other.update(id="other_journal", name="Other Journal", issn={"print": "1234-5678"})
    other.update(edits)
    write(tmp_path / "journals" / "other_journal.json", other)
    return run("build_index.py", "--check", "--ignore-index", "--journals", tmp_path / "journals", "--report", tmp_path / "r.md")


def test_clashes_fail(tmp_path):
    got = clash(tmp_path, id="tree_genetics_genomes")
    assert got.returncode == 1 and "its id \"tree_genetics_genomes\" is also journals/" in got.stdout
    got = clash(tmp_path, name="tree genetics & GENOMES")
    assert got.returncode == 1 and "its name \"Tree Genetics & Genomes\" is also journals/other_journal.json's" in got.stdout
    got = clash(tmp_path, issn={"online": "1614-2950"})
    assert got.returncode == 1 and "its ISSN 1614-2950 is also" in got.stdout
    assert "result: failed" in (tmp_path / "r.md").read_text()
    assert clash(tmp_path).returncode == 0


def test_check_finds_a_stale_index(tmp_path):
    write(tmp_path / "journals" / "tree_genetics_genomes.json", good_journal())
    idx = tmp_path / "index.json"
    run("build_index.py", "--journals", tmp_path / "journals", "--index", idx)
    assert run("build_index.py", "--check", "--journals", tmp_path / "journals", "--index", idx).returncode == 0
    data = json.loads(idx.read_text())
    data["generated"] = "2000-01-01T00:00:00Z"   # (when it was made doesn't count)
    idx.write_text(json.dumps(data))
    assert run("build_index.py", "--check", "--journals", tmp_path / "journals", "--index", idx).returncode == 0
    data["journals"][0]["version"] = "0.9.0"
    idx.write_text(json.dumps(data))
    got = run("build_index.py", "--check", "--journals", tmp_path / "journals", "--index", idx)
    assert got.returncode == 1 and "index.json isn't what the journal files make" in got.stdout


def test_issn_forms():
    assert common.issn_form("0028836x") == "0028-836X"
    assert common.issn_form("002883") is None
    assert common.issn_form("0028083X") == "0028-083X"
    assert common.issns({"issn": {"online": "1476-4687", "print": "0028-0836"}}) == ["0028-0836", "1476-4687"]
    assert common.issns({"issn": {"print": "nope"}}) == []
