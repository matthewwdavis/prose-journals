#!/usr/bin/env bash
# What "Check a journal with prose" checks: the pull request whose quick checks just finished (found
# by its head commit, as GitHub doesn't name a fork's pull request to this event), or what a
# maintainer gave by hand (a pull request's number, or a journal file on the library's main branch).
#
# Reads EVENT, TARGET, RUN_HEAD_SHA, RUN_HEAD_OWNER, RUN_HEAD_BRANCH, RUN_CONCLUSION, GITHUB_REPOSITORY,
# DEFAULT_BRANCH (and GH_TOKEN for gh). Writes to GITHUB_OUTPUT: go (true/false), pr, head_sha,
# base_ref, author, file, changed (the changed paths, comma-separated) and why (when go is false).
set -euo pipefail
repo="$GITHUB_REPOSITORY"
out="${GITHUB_OUTPUT:-/dev/stdout}"

stop() {
  echo "$1"
  { echo "go=false"; echo "why=$1"; } >> "$out"
  exit 0
}

pr=""; head=""; base="$DEFAULT_BRANCH"; author=""
if [ "$EVENT" = "workflow_dispatch" ]; then
  if [[ "$TARGET" =~ ^[0-9]+$ ]]; then
    pr="$TARGET"
  elif [[ "$TARGET" =~ ^journals/[a-z0-9_]{1,40}\.json$ ]]; then
    head=$(git rev-parse HEAD)
    author="${GITHUB_ACTOR}"
    { echo "go=true"; echo "pr="; echo "head_sha=$head"; echo "base_ref=$base"; echo "author=$author"
      echo "file=$TARGET"; echo "changed=$TARGET"; } >> "$out"
    echo "Checking $TARGET on $base ($head)"
    exit 0
  else
    stop "Give a pull request's number or a journal file (journals/<id>.json), not \"$TARGET\"."
  fi
else
  [ "$RUN_CONCLUSION" = "cancelled" ] && stop "The quick checks were cancelled (the pull request was pushed again); the newer run is checked."
  # (an open pull request whose head is that branch, in that copy of the library, at that commit)
  pr=$(gh api --method GET "repos/$repo/pulls" -f state=open -f per_page=100 -f "head=${RUN_HEAD_OWNER}:${RUN_HEAD_BRANCH}" \
         --jq "map(select(.head.sha == \"$RUN_HEAD_SHA\")) | .[0].number // empty")
  [ -n "$pr" ] || stop "No open pull request is at commit ${RUN_HEAD_SHA:0:7} any more (closed, or pushed again: the newer commit is checked)."
fi

info=$(gh api "repos/$repo/pulls/$pr")
state=$(jq -r .state <<<"$info")
[ "$state" = "open" ] || stop "Pull request #$pr is $state."
head=$(jq -r .head.sha <<<"$info")
base=$(jq -r .base.ref <<<"$info")
author=$(jq -r .user.login <<<"$info")
if [ "$EVENT" != "workflow_dispatch" ] && [ "$head" != "$RUN_HEAD_SHA" ]; then
  stop "Pull request #$pr has moved on to ${head:0:7}; that commit is checked instead."
fi
changed=$(gh api --paginate "repos/$repo/pulls/$pr/files?per_page=100" --jq '.[].filename')
file=$(grep -E '^journals/[a-z0-9_]{1,40}\.json$' <<<"$changed" | head -n 1 || true)
{ echo "go=true"; echo "pr=$pr"; echo "head_sha=$head"; echo "base_ref=$base"; echo "author=$author"
  echo "file=$file"; echo "changed=$(paste -sd, - <<<"$changed")"; } >> "$out"
echo "Checking pull request #$pr by $author at ${head:0:7} (into $base): ${file:-no journal file}"
