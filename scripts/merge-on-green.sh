#!/usr/bin/env bash
# Merge a pull request only after every check on its current head commit has COMPLETED and passed.
#
#   scripts/merge-on-green.sh <pr-number> [--merge|--squash|--rebase]
#
# main has no branch protection, so `gh pr merge --auto` merges at once, before CI has finished
# (PR #306, 2026-10-19: merged two seconds after its first check started; its PR-head Python job
# then failed). This script is the guard instead. It:
#   1. waits until checks are reported for the PR's head commit at all;
#   2. waits until every workflow run, check run and commit status on that commit has completed,
#      then waits one more poll and starts over if new checks appeared meanwhile;
#   3. refuses unless each check run concluded success, skipped or neutral and each commit status
#      is success, and refuses if the commit has more check runs than one page shows;
#   4. refuses if the PR's head moved meanwhile, and merges with --match-head-commit so GitHub
#      refuses too if it moves at the last moment.
# It exits non-zero without merging on any failure or timeout. DRY_RUN=1 runs every check and stops
# before the merge.
set -euo pipefail

pr="${1:?usage: scripts/merge-on-green.sh <pr-number> [--merge|--squash|--rebase]}"
method="${2:---merge}"
case "$method" in
  --merge|--squash|--rebase) ;;
  *) echo "merge method must be --merge, --squash or --rebase, not '$method'" >&2; exit 2 ;;
esac
start_timeout="${CHECKS_START_TIMEOUT_S:-600}"   # how long checks may take to appear
finish_timeout="${CHECKS_FINISH_TIMEOUT_S:-3600}" # how long they may take to complete
poll="${CHECKS_POLL_S:-20}"

head=$(gh pr view "$pr" --json headRefOid -q .headRefOid)
echo "PR #$pr: head $head"

runs_api="repos/{owner}/{repo}/commits/$head/check-runs?per_page=100"
status_api="repos/{owner}/{repo}/commits/$head/status?per_page=100"

# A count that reads as 0 when gh fails, so a transient error means "keep waiting", not a crash.
count() { local n; n=$("$@" 2>/dev/null) || n=0; echo "${n:-0}"; }
total_checks() { count gh api "$runs_api" -q '.total_count'; }

deadline=$((SECONDS + start_timeout))
until [ "$(total_checks)" -gt 0 ]; do
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "no checks were reported for $head within ${start_timeout}s; not merging" >&2
    exit 1
  fi
  sleep "$poll"
done

deadline=$((SECONDS + finish_timeout))
while :; do
  open_runs=$(count gh run list --commit "$head" --json status -q '[.[] | select(.status != "completed")] | length')
  open_checks=$(count gh api "$runs_api" -q '[.check_runs[] | select(.status != "completed")] | length')
  open_statuses=$(count gh api "$status_api" -q '[.statuses[] | select(.state == "pending")] | length')
  if [ "$open_runs" -eq 0 ] && [ "$open_checks" -eq 0 ] && [ "$open_statuses" -eq 0 ]; then
    before=$(total_checks)
    sleep "$poll"
    # Start over if a check appeared during the settle poll (another workflow or app reporting late).
    [ "$(total_checks)" -eq "$before" ] && break
  fi
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "checks for $head did not complete within ${finish_timeout}s; not merging" >&2
    exit 1
  fi
  sleep "$poll"
done

shown=$(gh api "$runs_api" -q '.check_runs | length')
total=$(gh api "$runs_api" -q '.total_count')
if [ "$total" -gt "$shown" ]; then
  echo "$head has $total check runs, more than the $shown this script reads; not merging" >&2
  exit 1
fi
failed=$(gh api "$runs_api" -q '[.check_runs[]
  | select(.conclusion != "success" and .conclusion != "skipped" and .conclusion != "neutral")
  | "\(.name)=\(.conclusion)"] | join(", ")')
failed_statuses=$(gh api "$status_api" -q '[.statuses[] | select(.state != "success")
  | "\(.context)=\(.state)"] | join(", ")')
if [ -n "$failed$failed_statuses" ]; then
  echo "checks did not pass for $head: ${failed}${failed:+${failed_statuses:+, }}${failed_statuses}; not merging" >&2
  exit 1
fi
gh api "$runs_api" -q '.check_runs[] | "  \(.conclusion)  \(.name)"'
gh api "$status_api" -q '.statuses[] | "  \(.state)  \(.context)"'

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
