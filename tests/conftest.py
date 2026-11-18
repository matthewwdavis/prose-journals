"""Shared helpers: run a tool as a person would (python tools/x.py), with prose turned off unless a
test asks for it, and a small journal file to start from."""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def run(tool: str, *args, env: dict | None = None, cwd: str = ROOT, timeout: int = 600) -> subprocess.CompletedProcess:
    e = {**os.environ, "PROSE_SRC": "none", "PYTHONDONTWRITEBYTECODE": "1", **(env or {})}
    return subprocess.run([sys.executable, os.path.join(ROOT, "tools", tool), *map(str, args)], capture_output=True, text=True,
                          env=e, cwd=cwd, timeout=timeout)


def good_journal() -> dict:
    """A small, correct Community journal (the format's own example)."""
    return {
        "format": 1, "id": "tree_genetics_genomes", "name": "Tree Genetics & Genomes", "version": "1.0.0", "kind": "journal",
        "publisher": "Springer", "group": "Springer Nature", "issn": {"print": "1614-2942", "online": "1614-2950"},
        "homepage": "https://link.springer.com/journal/11295",
        "guidelines": "https://link.springer.com/journal/11295/submission-guidelines",
        "verified": {"status": "community", "by": "someone", "date": "2026-11-17"},
        "page": {"paper": "a4"},
        "references": {"csl": "tree-genetics-and-genomes"},
        "limits": {"article_type": "Original Article", "source": "https://link.springer.com/journal/11295/submission-guidelines",
                   "checked": "2026-11-01", "limits": {"abstract_words": 250}, "unverified": None},
        "figures": {"depth_mm": 234, "types": [{"name": "Single column", "width_mm": 84}, {"name": "Double column", "width_mm": 174}]},
        "sources": [{"url": "https://link.springer.com/journal/11295/submission-guidelines", "what": "Submission guidelines. Read 2026-11-01."}],
    }


def write(path, data) -> str:
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    return str(path)


@pytest.fixture()
def library(tmp_path):
    """A small library folder: journals/ with the good journal, and a CODEOWNERS naming one maintainer."""
    write(tmp_path / "journals" / "tree_genetics_genomes.json", good_journal())
    (tmp_path / "CODEOWNERS").write_text("journals/** @the-maintainer\n")
    return tmp_path
