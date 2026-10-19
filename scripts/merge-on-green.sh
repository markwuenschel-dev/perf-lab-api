#!/usr/bin/env bash
# Merge a pull request only after every check on its current head commit has COMPLETED and passed.
#
#   scripts/merge-on-green.sh <pr-number> [--merge|--squash|--rebase]
#
# main has no branch protection, so `gh pr merge --auto` merges at once, before CI has even started
# (PR #306, 2026-10-19). This script is the guard instead. It:
#   1. waits until checks are reported for the PR's head commit at all;
#   2. waits until every workflow run and every check run for that commit has completed;
#   3. refuses unless each check concluded success, skipped or neutral;
#   4. refuses if the PR's head moved meanwhile, and merges with --match-head-commit so GitHub
#      refuses too if it moves at the last moment.
# It exits non-zero without merging on any failure or timeout. DRY_RUN=1 runs every check and stops
# before the merge.
set -euo pipefail

pr="${1:?usage: scripts/merge-on-green.sh <pr-number> [--merge|--squash|--rebase]}"
method="${2:---merge}"
start_timeout="${CHECKS_START_TIMEOUT_S:-600}"   # how long checks may take to appear
finish_timeout="${CHECKS_FINISH_TIMEOUT_S:-3600}" # how long they may take to complete
poll="${CHECKS_POLL_S:-20}"

head=$(gh pr view "$pr" --json headRefOid -q .headRefOid)
echo "PR #$pr: head $head"

runs_api="repos/{owner}/{repo}/commits/$head/check-runs?per_page=100"

deadline=$((SECONDS + start_timeout))
until [ "$(gh api "$runs_api" -q '.total_count')" -gt 0 ]; do
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "no checks were reported for $head within ${start_timeout}s; not merging" >&2
    exit 1
  fi
  sleep "$poll"
done

deadline=$((SECONDS + finish_timeout))
while :; do
  open_runs=$(gh run list --commit "$head" --json status -q '[.[] | select(.status != "completed")] | length')
  open_checks=$(gh api "$runs_api" -q '[.check_runs[] | select(.status != "completed")] | length')
  if [ "$open_runs" -eq 0 ] && [ "$open_checks" -eq 0 ]; then
    break
  fi
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "checks for $head did not complete within ${finish_timeout}s; not merging" >&2
    exit 1
  fi
  sleep "$poll"
done

failed=$(gh api "$runs_api" -q '[.check_runs[]
  | select(.conclusion != "success" and .conclusion != "skipped" and .conclusion != "neutral")
  | "\(.name)=\(.conclusion)"] | join(", ")')
if [ -n "$failed" ]; then
  echo "checks did not pass for $head: $failed; not merging" >&2
  exit 1
fi
gh api "$runs_api" -q '.check_runs[] | "  \(.conclusion)  \(.name)"'

now=$(gh pr view "$pr" --json headRefOid -q .headRefOid)
if [ "$now" != "$head" ]; then
  echo "PR #$pr head moved from $head to $now while checks ran; not merging" >&2
  exit 1
fi

if [ -n "${DRY_RUN:-}" ]; then
  echo "DRY_RUN: all checks passed for $head; would run: gh pr merge $pr $method --match-head-commit $head"
  exit 0
fi
gh pr merge "$pr" "$method" --match-head-commit "$head"
gh pr view "$pr" --json state,mergeCommit,baseRefName -q '"PR #'"$pr"': \(.state) as \(.mergeCommit.oid) into \(.baseRefName)"'
