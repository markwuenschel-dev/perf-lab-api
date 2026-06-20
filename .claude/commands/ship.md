---
description: Commit, push, open a detailed PR, merge it, and delete merged branches (local + GitHub)
argument-hint: "[optional: PR title or notes; 'squash'/'rebase' to override merge method]"
---

Run the full ship flow for the current working tree. Default merge method is **merge commit**;
if `$ARGUMENTS` contains `squash` or `rebase`, use that instead. Any other text in `$ARGUMENTS`
is a hint for the PR title/description.

Environment: Windows + PowerShell, repo `markwuenschel-dev/perf-lab-api` on plain HTTPS. The `gh`
CLI is installed and authenticated (scopes include `repo`) and Git Credential Manager
(`credential.helper=manager-core`) handles HTTPS auth — so **use `gh` for push/PR/merge**; there is
no token-over-HTTPS hack needed here. Never echo or write any token anywhere.

## Steps

1. **Survey.** `git status`, `git branch -vv`, and `gh pr list` for real remote state. Read the
   diff (`git diff` / `git diff --staged`) so the commits and PR body are accurate.

2. **Branch.** If on `main` (the default branch), create a `feat/…` / `fix/…` / `docs/…` /
   `chore:…` branch first — never commit straight to `main`. If already on a feature branch, stay
   on it.

3. **Don't commit junk.** Build artifacts / caches belong in `.gitignore`, not in a commit. In this
   repo that means `.venv/`, `__pycache__/`, `.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/`,
   `.vs/`, `.idea/`. Add ignores rather than staging them.

4. **Commit** in logical, conventional-commit-style groups (`feat:`, `fix:`, `test:`, `docs:`,
   `chore:`), one concern per commit, with a body explaining the *why*. End each message with:
   `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

5. **Verify before merge.** Run the affected tests: `uv run pytest -q <paths>`. Tests marked
   `requires_db` need a live PostgreSQL instance — if one isn't available, scope them out with
   `-m "not requires_db"` and say so in the PR's Testing section. Don't merge red.

6. **Push** the branch (HTTPS, GCM handles auth):
   ```
   git push -u origin HEAD
   ```

7. **Open a detailed PR** with `gh`. To avoid shell-escaping the body, write it to a temp file
   first (e.g. with the Write tool) and pass `--body-file`. Sections: **Summary**, **What's
   included**, **Testing** (with results), **Notes**.
   ```
   gh pr create --title "<title>" --body-file <path-to-body> --base main
   ```

8. **Merge** the PR with the chosen method (default `--merge`; `--squash` or `--rebase` if
   `$ARGUMENTS` requested it), deleting the branch in the same step:
   ```
   gh pr merge <n> --merge --delete-branch
   ```
   `--delete-branch` removes the remote and local feature branch and checks out `main`.

9. **Clean up.** Fast-forward local `main` and prune anything left over:
   ```
   git fetch --prune
   git checkout main && git pull --ff-only
   ```
   Then catch other stale branches: any local branch shown as `[origin/…: gone]` in
   `git branch -vv`, or listed by `git branch --merged main` (excluding `main`), should be deleted
   locally (`git branch -d <b>`) and remotely if it still exists (`gh api -X DELETE
   repos/markwuenschel-dev/perf-lab-api/git/refs/heads/<b>` or `git push origin --delete <b>`).

10. **Report** PR number/URL, merge SHA (`git rev-parse main`), and what was deleted.

**Confirm before merging** if tests fail or the diff looks unexpected. Committing/pushing is the
explicit intent of running `/ship`; merging is outward-facing and hard to reverse, so pause if
anything looks off.
