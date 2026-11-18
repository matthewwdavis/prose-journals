# The journal library for prose and mosaic

A shared, growing list of journals for two apps: **prose** (writing manuscripts, like Word) and
**mosaic** (putting figures together). Each journal is one small file saying what the journal
wants: its figure sizes, page, sections, word limits, reference style, how it captions figures,
its LaTeX template and its Word layout, with where each fact comes from.

When you pick a journal in either app, the app gets its file from here once, keeps it, and from
then on works with it offline. Anyone can add a journal: prose sends it here for you.

## Using it

**In prose**
- **Layout ▸ Journal ▸ Find a Journal…** — type a journal's name, a short name or its ISSN. Journals
  from the library are listed with the ones prose comes with.
- **Layout ▸ Journal ▸ My Journals ▸ Browse the Library…** — every journal in the library, to add
  to your own list.

**In mosaic**
- **Size ▸ Find a Journal…** — the same search, for the journal's figure sizes and rules.

Each journal shows a badge:

| Badge | What it means |
|---|---|
| **Verified** (green) | A maintainer read the journal's own guidelines and checked the file, field by field. |
| **Community** (grey) | Sent by an author. The library's checks passed and a maintainer looked it over, but no one checked every field against the guidelines. The apps say "Community journal: checked by machine, not by a maintainer". |

A Verified journal is due to be checked again 18 months after its limits were read; the library
opens a reminder for it.

## Adding or correcting a journal

You don't need to know any code or GitHub. In prose, set the journal up as one of your own
(**My Journals ▸ Add…**), then choose **My Journals ▸ Share…**. prose does the GitHub parts and
tells you where your journal is waiting for review. [CONTRIBUTING.md](CONTRIBUTING.md) explains
what reviewers look for.

To ask for a journal you can't set up yourself, or to say something is wrong, open an issue
(**Issues ▸ New issue**).

## What's here

| | |
|---|---|
| `journals/<id>.json` | One file per journal, named by its id. The format is explained in prose's `docs/journal-format.md`; `schema/journal.schema.json` is its schema (text editors such as VS Code use it to underline mistakes). |
| `index.json` | The list the apps search: made by `tools/build_index.py`, never by hand. |
| `tools/` | The library's checks (below). `tools/_labapp` is a copy of the code both apps share, put there by labapp's `tools/vendor.py`; don't edit it here. |
| `.github/` | The checks that run on each pull request, the review rules (CODEOWNERS) and the forms for issues and pull requests. |
| `MAINTAINERS.json` | Who looks after which publisher's journals. |

The library never holds a publisher's files (LaTeX classes and styles). A journal file names them
and says where prose downloads them; each person's copy stays on their own computer.

## Licence

The journal files are facts about journals and are public domain (CC0 1.0): use them for
anything. Everyone who contributes agrees to this. The tools are under the MIT License
(`LICENSE-tools`). See [LICENSE](LICENSE).

---

## For maintainers

### The tools

Run them from this folder with prose's Python (prose's conda environment, `environment.yml` in
prose). Each says what it does with `--help`.

| Tool | Does |
|---|---|
| `tools/check_journal.py FILE… [--strict] [--base REF] [--author LOGIN] [--changed-files LIST] [--report R.md]` | Every rule a journal file must pass (format, id = file name, version raised, the badge, sizes, LaTeX that keeps to the manuscript, addresses, names, duplicates, changed files, re-verification, sources). |
| `tools/build_index.py [--check [--ignore-index]] [--out FILE]` | `journals/*.json` → `index.json` (format 2). `--check` fails on two journals with the same id, name or ISSN, and when the committed `index.json` is out of date. |
| `tools/render_preview.py FILE [--out DIR]` | prose's sample as PDF (prose's own pages) and Word, and pictures of pages 1–2. Needs prose (below). |
| `tools/compile_latex.py FILE [--download] [--publisher-files DIR]` | prose's sample in the journal's LaTeX, compiled with Tectonic. Needs prose, Tectonic and the publisher's files. |
| `tools/measure_layout.py FILE [--measured OUT.json]` | The journal's printed layout measured from its LaTeX (with prose's own `tools/measure_journal_layout.py`) and compared with its `word.layout`. Word's stand-in fonts aren't compared (they depend on the fonts installed); the PDF's own fonts are. |
| `tools/summarize.py REPORT.md…` | The jobs' reports as the one comment a pull request shows. |
| `tools/reverify.py` | The journals due to be checked again (limits read over 18 months ago). |
| `tools/sync_builtin.py [--from FOLDER]` | Copies the apps' built-in journals (labapp's `builtin_journals`, `"kind": "journal"` only) into `journals/` as Verified, and labapp's schema into `schema/`. |
| `tools/publisher.py --known-urls journals` | The publishers' download addresses the library's journals use (the checks download only from these). |
| `tools/build_site.py SITE` | The preview site's index page (after a merge). |

The jobs that need prose find it through `PROSE_SRC` (a copy of prose's source) or an installed
`prose`; `PROSE_SRC=none` turns it off. Without prose, Tectonic or a publisher's files they skip
and say why. prose always runs with throwaway settings folders (`PROSE_HOME`, `PROSE_CACHE`,
`PROSE_TEMPLATES` set to a temporary folder), never your own.

The built-in journals are made in labapp (`labapp/builtin_journals`); their Word layouts are
measured there with prose's `tools/measure_journal_layout.py`. After they change:

```
python tools/sync_builtin.py --from <labapp>/labapp/builtin_journals
python tools/build_index.py
python -m pytest -q
```

`tools/_labapp` holds only labapp's journal-file code (`journalfile.py`, `journallibrary.py`, the
schema). When it changes in labapp, copy it in again with labapp's own tool:
`python <labapp>/tools/vendor.py --only journal tools`.

### The checks on GitHub

They're switched off until the repository variable **`LIBRARY_CI`** is `on`
(**Settings ▸ Secrets and variables ▸ Actions ▸ Variables**).

A pull request gets two workflows, one after the other:

1. **Check a journal** (`check.yml`) runs on every pull request, including one from someone's own
   copy of the library (a fork: that's how the apps' Share… sends a journal). It has no secrets and
   can't change anything:

   | Job | Does | Fails when |
   |---|---|---|
   | validate | `check_journal.py --strict` with the pull request's author and base | any error |
   | index | `build_index.py --check --ignore-index` | two journals with the same id, name or ISSN |

2. **Check a journal with prose** (`check-prose.yml`) starts when the first finishes. It runs in the
   library itself, so it can read prose (private for now) with the secret `PROSE_READ_TOKEN`, and
   post the comment:

   | Job | Runs on | Does | Fails when |
   |---|---|---|---|
   | validate, index | Ubuntu | again, here (the results the comment shows) | as above |
   | latex | Ubuntu | `compile_latex.py --download` | Tectonic fails |
   | word-layout | macOS | `measure_layout.py --download` | the measured and given layouts differ |
   | preview | Ubuntu | `render_preview.py`; the PDF, Word file and pictures are the run's artifacts | never |
   | summary | Ubuntu | one comment on the pull request, updated on each run, and the result **journal-check** on its commit | any job above failed |

   latex, word-layout and preview run only once validate passes. Main's rules require
   **journal-check**.

Neither workflow runs a pull request's code: the tools, the workflows and the rules come from the
library's main branch, and only the pull request's `journals/` folder is taken, as data
(`.github/scripts/take_journals.sh`, which refuses links). The second workflow also:

- downloads a publisher's package only from an address the library's journals already use;
- only reads the caches (they're written after merges);
- uses the token only to get prose, and never keeps it;
- compiles a journal's LaTeX only after validate has refused LaTeX that reads or writes other files
  or runs programs.

A first-time contributor's checks wait until a maintainer approves them (the button on the pull
request). Look at the journal file before approving.

Run either check by hand: **Actions ▸ Check a journal with prose ▸ Run workflow**, with a pull
request's number or a journal file on main (`journals/<id>.json`).

After a merge, **Publish** (`publish.yml`):

- rebuilds `index.json` (a commit by the Actions bot, which main's rules let through);
- makes every journal's preview;
- puts the previews on the library's GitHub Pages site (**Settings ▸ Pages ▸ Source: GitHub
  Actions**).

Once a month, **Journals due to be checked again** (`reverify.yml`) opens or updates an issue
listing the journals whose limits were read over 18 months ago.

**prose in the checks.** The jobs that run prose get it from `PROSE_REPO` (a repository variable;
default `matthewwdavis/prose`) at `PROSE_REF` (default `main`). They read it with the repository
secret `PROSE_READ_TOKEN`: a fine-grained token that can only read prose's contents. Without it,
or when it has expired, those jobs fail and say so in the comment ("PROSE_READ_TOKEN isn't set" /
"…couldn't read prose: the token may have expired"); make a new token and replace the secret.
