#!/usr/bin/env bash
# Put a pull request's journal files over the library's own, as data: only journals/ is taken from
# the pull request's commit, so the tools, the rules and this script stay the library's.
#
#     .github/scripts/take_journals.sh PR_NUMBER HEAD_SHA BASE_REF
#
# Fetches the pull request's head, refuses it if it isn't HEAD_SHA any more (it was pushed again:
# a newer check follows) or if journals/ holds anything but plain files (a link could point the
# checks at files outside the library), then writes the list of the files it changes to
# changed.txt (one path a line). Exit code 1 on a refusal, with the reason as an error.
set -euo pipefail
pr="$1"
head="$2"
base="${3:-main}"

git fetch --no-tags --quiet origin "+refs/pull/${pr}/head:refs/remotes/pr/head" "+refs/heads/${base}:refs/remotes/origin/${base}"
got=$(git rev-parse refs/remotes/pr/head)
if [ "$got" != "$head" ]; then
  echo "::error::Pull request #${pr} is now at ${got:0:7}, not ${head:0:7}: it was pushed again, and the newer commit is checked instead."
  exit 1
fi

# (only plain files: 100644 or 100755; no links (120000) or submodules (160000))
odd=$(git ls-tree -r "$head" -- journals/ | awk '$1 != "100644" && $1 != "100755" {print $4}')
if [ -n "$odd" ]; then
  echo "::error::journals/ in this pull request holds links or other repositories, which the library never takes: $(echo "$odd" | tr '\n' ' ')"
  exit 1
fi

if git ls-tree -d "$head" -- journals >/dev/null 2>&1 && [ -n "$(git ls-tree "$head" -- journals)" ]; then
  git -c core.symlinks=false checkout "$head" -- journals/
fi
git diff --name-only --no-renames "refs/remotes/origin/${base}...${head}" > changed.txt
echo "The pull request changes:"
sed 's/^/  /' changed.txt
