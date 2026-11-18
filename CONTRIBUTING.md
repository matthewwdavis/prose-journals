# Adding a journal to the library

Thank you for helping. A journal you add here can be found by everyone who uses prose or mosaic,
so the next person writing for it starts with the right page, sections, figure sizes and
reference style.

This guide is for people who know Word, not code. You won't need to touch GitHub yourself.

## The short version

1. In prose, choose **Layout ▸ Journal ▸ My Journals ▸ Add…** and fill in the form from the
   journal's author guidelines: its page, font, sections, word limits, reference style and figure
   sizes. Try it on a manuscript.
2. Choose **My Journals ▸ Share…** and pick the journal.
3. prose sends it to the library for review (on GitHub this is called a *pull request*; prose makes
   it for you, from your own GitHub account, and shows you the link). It says: "Shared for review.
   It joins the library when a maintainer accepts it."
4. The library checks it automatically within a few minutes and posts what it found on that page,
   with a preview: prose's sample manuscript set for your journal, as a PDF and a Word file. (The
   first time you send one, a maintainer starts the check by hand, so it may take a little longer.)
5. A maintainer reads it, may ask a question there, and accepts it. The next day everyone's prose
   and mosaic can find it, with a grey **Community** badge.

To correct a journal already in the library, add it to your own journals from the library
(**My Journals ▸ Browse the Library…**), change what's wrong (**My Journals ▸ Edit…**), and Share
it again. Describe what you changed and where the journal says so.

## What to put in

Everything in a journal file comes from the journal's own pages: its author guidelines, its
figure guidelines, its LaTeX template page. In the file, `sources` lists each page you read and
when; prose fills in the ones you give it.

- **Figure sizes**: the widths the journal gives (single column, double column…) and the deepest a
  figure may be, in millimetres, exactly as the guidelines say.
- **Word and item limits**: for one article type (say which), with the day you read them.
- **Reference style**: its name in the Citation Style Language list (zotero.org/styles): the end of
  the style's address, such as `tree-genetics-and-genomes`. prose downloads the style itself, so the
  file names it rather than holding it.
- **LaTeX**: if the journal has a LaTeX template, its class's name and the names of its files.
  **Never the files themselves** (see below).

Anything you're not sure of, leave out: prose uses a sensible default, and the review will ask.

The format is described field by field in prose's `docs/journal-format.md`, which also explains
how to edit a journal file by hand in a plain-text editor (TextEdit with **Format ▸ Make Plain
Text**, BBEdit or VS Code — not Word, which would save it as a document).

## What reviewers check

The automatic check (it runs on every journal sent in) makes sure that:

- the file is a journal file prose and mosaic can read, with no field they don't know;
- it's one file, `journals/<id>.json`, named by its id, and nothing else is changed;
- a correction raises the file's `version` (the last number for a correction, the middle one for
  an addition, the first for a change that would alter documents already written);
- it isn't a journal the library already has (same name, id or ISSN);
- web addresses are ordinary `http://` or `https://` ones, names are at most 120 characters,
  and the file is at most 256 KB with no text in it over 64 KB;
- its LaTeX only sets out the manuscript: it doesn't read or write other files or run programs
  (the check compiles it, so it refuses commands such as `\openin`, `\write18` or `\input` of a
  file outside the manuscript's folder);
- its sources are on the journal's or the publisher's own website (or a note asks a maintainer
  to look);
- prose's sample manuscript compiles in its LaTeX template, its Word layout matches what the
  journal prints, and the preview is made.

A maintainer then reads it with the checklist you'll see on the pull request:

- the sources are the journal's own pages, and each says when it was read;
- the figure sizes and limits are the ones those pages give;
- the preview looks like a manuscript for this journal;
- there are no publisher's files in it;
- you agreed that your contribution is CC0 (below).

## What the badges mean

- **Community** (grey): sent by an author, checked automatically and looked over by a maintainer,
  but not checked field by field against the guidelines. This is the badge every journal sent in
  gets. The automatic check refuses a file marked `"verified"` from anyone who isn't a maintainer.
- **Verified** (green): a maintainer has read the journal's guidelines and checked the file field by
  field, and set `"verified": {"status": "verified", "by": <their GitHub name>, "date": …}` in a
  change of their own. A Verified journal is checked again when its limits are 18 months old.

## Never include a publisher's files

Publishers' LaTeX classes and styles (`.cls`, `.sty`, `.bst` files and the like) belong to the
publishers, and many may not be passed on. **They are never put in the library, and never in a
journal file.** A journal file lists their names (and a checksum, so prose can tell it has the
right copy) under `latex.files`, and under `latex.fetch` where prose can download them. Each person's
prose gets them from the publisher, or from the package they downloaded themselves, and keeps them
on their own computer. The library's checks download them only into their own temporary cache,
and never keep or publish them.

## Your contribution is CC0

A journal file holds facts about a journal: its sizes, limits, sections and styles. Everything in
the library is dedicated to the public domain under **CC0 1.0** (see LICENSE), so anyone can use
it for anything, including other apps. When you send a journal you agree to this: the pull
request's checklist asks you to confirm "I agree my contribution is CC0".

A reference style written into a file (only for a style that isn't in the Citation Style Language
list) must be your own or under the Citation Style Language list's own licence (CC BY-SA).

## Without prose

If you'd rather write the file by hand: copy one from `journals/`, change it, check it with

```
python tools/check_journal.py --strict journals/my_journal.json
```

from a copy of this library, and open a pull request that adds only `journals/<id>.json`.
