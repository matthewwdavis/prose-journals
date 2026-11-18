"""The repository itself: the workflows are switched off until publishing and take least privilege;
no publisher's file is anywhere; the vendored labapp is labapp's own; the files the plan names exist."""
import hashlib
import json
import os
import re

import pytest
from conftest import ROOT

WORKFLOWS = os.path.join(ROOT, ".github", "workflows")

try:
    import yaml
except ImportError:   # (a light text check instead)
    yaml = None


def workflow(name: str) -> dict:
    with open(os.path.join(WORKFLOWS, name), encoding="utf-8") as fh:
        text = fh.read()
    if yaml is None:
        pytest.skip("PyYAML isn't here")
    data = yaml.safe_load(text)
    data["on"] = data.pop(True, data.get("on"))   # (YAML reads a bare `on` as true)
    return data


SWITCH = "vars.LIBRARY_CI == 'on'"
ACTIONS = os.path.join(ROOT, ".github", "actions")


def _uses(folder: str):
    for base, _, names in os.walk(folder):
        for name in names:
            if name.endswith((".yml", ".yaml")):
                with open(os.path.join(base, name), encoding="utf-8") as fh:
                    for used in re.findall(r"^\s*(?:-\s+)?uses:\s*(\S+)", fh.read(), re.M):
                        yield name, used


@pytest.mark.parametrize("name", ["check.yml", "check-prose.yml", "publish.yml", "reverify.yml"])
def test_every_workflow_is_switched_off_until_publishing_and_takes_no_permissions_by_default(name):
    data = workflow(name)
    assert data["permissions"] == {}, f"{name}: permissions are given job by job"
    for job, spec in data["jobs"].items():
        guarded = SWITCH in spec.get("if", "")
        follows = "needs" in spec and job not in ("request", "changes")   # (after a guarded job)
        assert guarded or follows or job == "deploy", f"{name}: {job} runs without the switch"
        assert "permissions" in spec or job == "deploy", f"{name}: {job} doesn't say its permissions"


def test_the_quick_checks_run_on_every_pull_request_without_secrets_or_write_access():
    data = workflow("check.yml")
    assert data["on"] == {"pull_request": None}   # (every pull request, so the result is always posted)
    assert set(data["jobs"]) == {"validate", "index"}
    text = open(os.path.join(WORKFLOWS, "check.yml"), encoding="utf-8").read()
    assert "secrets." not in text and "pull_request_target" not in text
    for spec in data["jobs"].values():
        assert spec["permissions"] == {"contents": "read"}
        checkout = spec["steps"][0]
        assert checkout["with"]["ref"] == "${{ github.event.pull_request.base.sha }}" and checkout["with"]["persist-credentials"] is False


def test_the_checks_with_prose_run_after_the_quick_checks_and_never_run_the_pull_requests_code():
    data = workflow("check-prose.yml")
    assert data["on"]["workflow_run"] == {"workflows": ["Check a journal"], "types": ["completed"]}
    assert "workflow_dispatch" in data["on"]
    jobs = data["jobs"]
    assert set(jobs) == {"request", "validate", "index", "prose", "summary"}
    assert jobs["request"]["permissions"] == {"contents": "read", "pull-requests": "read", "statuses": "write"}
    assert jobs["summary"]["permissions"] == {"contents": "read", "pull-requests": "write", "statuses": "write"}
    for job in ("validate", "index", "prose"):
        assert jobs[job]["permissions"] == {"contents": "read"}
    assert "needs.validate.result == 'success'" in jobs["prose"]["if"]
    assert {m["job"]: m["os"] for m in jobs["prose"]["strategy"]["matrix"]["include"]} == {
        "latex": "ubuntu-latest", "word-layout": "macos-latest", "preview": "ubuntu-latest"}
    for job, spec in jobs.items():
        for step in spec["steps"]:
            w = step.get("with") or {}
            if step.get("uses", "").startswith("actions/checkout@"):
                # (the library's own main branch, never the pull request's commit; no token kept)
                assert "ref" not in w and "repository" not in w and w["persist-credentials"] is False, (job, step)
            assert "allow-unsafe-pr-checkout" not in w
    text = open(os.path.join(WORKFLOWS, "check-prose.yml"), encoding="utf-8").read()
    assert text.count("secrets.") == 1 and "token: ${{ secrets.PROSE_READ_TOKEN }}" in text   # (only to read prose)
    assert ".github/scripts/take_journals.sh" in text and "LIBRARY_DOWNLOAD_ALLOWED" in text
    prose = next(s for s in jobs["prose"]["steps"] if s.get("id") == "prose")
    assert prose["uses"] == "./.github/actions/prose" and prose["with"]["cache"] == "restore"
    assert prose["with"]["repository"] == "${{ vars.PROSE_REPO || 'matthewwdavis/prose' }}"
    assert prose["with"]["ref"] == "${{ vars.PROSE_REF || 'main' }}"


def test_prose_is_read_with_the_token_alone_and_a_missing_token_is_said_plainly():
    with open(os.path.join(ACTIONS, "prose", "action.yml"), encoding="utf-8") as fh:
        text = fh.read()
    assert "persist-credentials: false" in text and "github.token" not in text
    assert "PROSE_READ_TOKEN isn't set" in text and "PROSE_READ_TOKEN is set but couldn't read" in text
    assert "GITHUB_ENV" not in text and "GITHUB_PATH" not in text
    if yaml is not None:
        steps = yaml.safe_load(text)["runs"]["steps"]
        assert [s.get("uses", "").split("@")[0] for s in steps if s.get("uses")] == [
            "actions/checkout", "conda-incubator/setup-miniconda", "actions/cache/restore", "actions/cache"]


def test_publishing_deploys_the_previews_to_pages_and_only_the_index_job_can_write():
    data = workflow("publish.yml")
    jobs = data["jobs"]
    assert jobs["index"]["permissions"] == {"contents": "write"}
    assert jobs["site"]["permissions"] == {"contents": "read"}
    assert jobs["deploy"]["permissions"] == {"pages": "write", "id-token": "write"}
    assert jobs["deploy"]["environment"]["name"] == "github-pages"
    assert any(s.get("uses", "").startswith("actions/deploy-pages@") for s in jobs["deploy"]["steps"])


def test_actions_are_pinned_to_a_commit():
    for name, used in [*_uses(WORKFLOWS), *_uses(ACTIONS)]:
        if used.startswith("./"):
            continue
        assert re.search(r"@[0-9a-f]{40}$", used), f"{name}: {used} isn't pinned to a commit"


def test_untrusted_text_never_goes_straight_into_a_script():
    for name in os.listdir(WORKFLOWS):
        with open(os.path.join(WORKFLOWS, name), encoding="utf-8") as fh:
            text = fh.read()
        for block in re.findall(r"run: \|\n((?:\s{10,}.*\n)+)", text):
            assert "${{" not in block, f"{name}: a run script takes ${{{{ }}}} directly; pass it through env"


def test_no_publishers_file_anywhere():
    for folder, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".pytest_cache", "preview-out", "reports")]
        for n in names:
            assert not n.lower().endswith((".cls", ".sty", ".bst", ".clo", ".ldf", ".zip")), os.path.join(folder, n)


def test_the_vendored_labapp_is_labapps_own():
    base = os.path.join(ROOT, "tools", "_labapp")
    with open(os.path.join(base, "VENDORED.json"), encoding="utf-8") as fh:
        listed = json.load(fh)["files"]
    for rel, sha in listed.items():
        with open(os.path.join(base, *rel.split("/")), "rb") as fh:
            assert hashlib.sha256(fh.read().replace(b"\r\n", b"\n")).hexdigest() == sha, f"tools/_labapp/{rel} was edited here"


def test_the_schema_is_labapps():
    with open(os.path.join(ROOT, "schema", "journal.schema.json"), "rb") as a, \
            open(os.path.join(ROOT, "tools", "_labapp", "journal.schema.json"), "rb") as b:
        assert a.read() == b.read()


def test_the_plans_files_are_here():
    for rel in ("README.md", "CONTRIBUTING.md", "LICENSE", "LICENSE-tools", ".gitignore", "index.json", "MAINTAINERS.json",
                ".github/CODEOWNERS", ".github/PULL_REQUEST_TEMPLATE.md", ".github/ISSUE_TEMPLATE/config.yml",
                ".github/ISSUE_TEMPLATE/request-a-journal.yml", ".github/ISSUE_TEMPLATE/report-a-wrong-journal.yml"):
        assert os.path.isfile(os.path.join(ROOT, rel)), rel
    with open(os.path.join(ROOT, ".github", "PULL_REQUEST_TEMPLATE.md"), encoding="utf-8") as fh:
        assert "I agree my contribution is CC0" in fh.read()
    with open(os.path.join(ROOT, ".github", "CODEOWNERS"), encoding="utf-8") as fh:
        assert re.search(r"^journals/\*\* @matthewwdavis$", fh.read(), re.M)
    with open(os.path.join(ROOT, "MAINTAINERS.json"), encoding="utf-8") as fh:
        m = json.load(fh)
    assert m["owner"] == "matthewwdavis" and m["groups"] == {}
    with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as fh:
        assert "claude-commands.log" in fh.read().split()
    with open(os.path.join(ROOT, "LICENSE"), encoding="utf-8") as fh:
        assert "CC0 1.0 Universal" in fh.read()


def test_the_library_holds_only_the_apps_journals_for_now():
    from tools import common
    ids = sorted(n[:-5] for n in os.listdir(common.JOURNALS))
    assert len(ids) == 16 and not {"general", "cover_letter", "ucdavis_dissertation"} & set(ids)   # (the in-house templates stay in the apps)
    for i in ids:
        j, _, _ = common.read_journal(os.path.join(common.JOURNALS, f"{i}.json"))
        assert j["kind"] == "journal" and j["verified"]["status"] == "verified"
        assert not (j.get("references", {}).get("csl") and "csl_text" in j["references"])
