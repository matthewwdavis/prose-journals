"""sync_builtin.py: only the apps' journals come in, as Verified, without a repository style's text."""
import json

from conftest import good_journal, write

from tools import sync_builtin


def source(tmp_path):
    j = good_journal()
    j["verified"] = {"status": "unverified", "by": "prose (built in)", "date": "2026-11-16"}
    j["references"] = {"csl": "tree-genetics-and-genomes", "csl_text": "<style>…</style>"}
    write(tmp_path / "src" / "tree_genetics_genomes.json", j)
    write(tmp_path / "src" / "general.json", {"format": 1, "id": "general", "name": "General (no journal)", "kind": "general"})
    write(tmp_path / "src" / "index.json", {"format": 1, "journals": []})
    return tmp_path / "src"


def test_journals_only_as_verified(tmp_path):
    said, bad = sync_builtin.sync(str(source(tmp_path)), str(tmp_path / "journals"))
    assert bad == 0 and said == ["tree_genetics_genomes: added"]
    assert sorted(p.name for p in (tmp_path / "journals").iterdir()) == ["tree_genetics_genomes.json"]
    got = json.loads((tmp_path / "journals" / "tree_genetics_genomes.json").read_text())
    assert got["verified"] == {"status": "verified", "by": "prose (built in)", "date": "2026-11-16"}
    assert got["references"] == {"csl": "tree-genetics-and-genomes"}


def test_again_changes_nothing_and_says_when_a_version_wasnt_raised(tmp_path):
    src = source(tmp_path)
    sync_builtin.sync(str(src), str(tmp_path / "journals"))
    assert sync_builtin.sync(str(src), str(tmp_path / "journals")) == ([], 0)
    j = json.loads((src / "tree_genetics_genomes.json").read_text())
    j["aliases"] = ["Tree Genet Genomes"]
    write(src / "tree_genetics_genomes.json", j)
    said, _ = sync_builtin.sync(str(src), str(tmp_path / "journals"), check=True)
    assert said == ["tree_genetics_genomes: would be updated (its version is still 1.0.0: raise it in labapp's file so "
                    "copies already fetched are updated)"]
    j["version"] = "1.1.0"
    write(src / "tree_genetics_genomes.json", j)
    assert sync_builtin.sync(str(src), str(tmp_path / "journals"))[0] == ["tree_genetics_genomes: updated"]


def test_a_journal_that_is_no_longer_built_in_is_said(tmp_path):
    src = source(tmp_path)
    sync_builtin.sync(str(src), str(tmp_path / "journals"))
    (src / "tree_genetics_genomes.json").unlink()
    community = good_journal()
    community.update(id="community_one", name="Community One", issn={})
    write(tmp_path / "journals" / "community_one.json", community)
    said, _ = sync_builtin.sync(str(src), str(tmp_path / "journals"))
    assert said == [f"tree_genetics_genomes: in the library but not a built-in journal in {src}; left as it is"]
