---
description: Commit, push, open a detailed PR, merge it, and delete merged branches (local + GitHub)
argument-hint: "[optional: PR title or notes; 'squash'/'rebase' to override merge method]"
---

Run the full ship flow for the current working tree of **perf-lab-web** (the Vite + React +
Tailwind v4 frontend). Default merge method is **merge commit**; if `$ARGUMENTS` contains
`squash` or `rebase`, use that instead. Any other text in `$ARGUMENTS` is a hint for the PR
title/description.

This repo lives in WSL where **git over SSH fails** and remote-tracking refs go stale, and
**`gh` is not installed** — so use the REST API for PR/merge/cleanup and the token-over-HTTPS
mechanism below for every network op (local refs lie; trust the API). The repo is
`markwuenschel-dev/perf-lab-web`. See the `git-push-mechanism` memory for full context.

⚠️ **Merging to `main` auto-deploys production via Netlify.** Treat a merge as a deploy.

## Steps

1. **Survey.** `git status`, `git branch -vv`, and the real remote state via the REST API
   (`GET /repos/markwuenschel-dev/perf-lab-web/...`). Read the diff so the commits and PR body
   are accurate.

2. **Branch.** If on `main` (the default branch), create a `feat/…` / `fix/…` / `docs/…` /
   `chore/…` branch first — never commit straight to `main`. If already on a feature branch,
   stay on it.

3. **Don't commit junk.** Build artifacts / caches (`dist/`, `node_modules/`, Vite caches,
   coverage) belong in `.gitignore`, not in a commit. Never stage `.env` or
   `.claude/settings.local.json` (they hold the token). Add ignores rather than staging them.

4. **Commit** in logical, Conventional-Commit-style groups (`feat(web): …`, `fix: …`,
   `docs: …`, `chore: …`), one concern per commit, with a body explaining the *why*. If
   `src/types.ts` or API contracts changed, note the backend sync in the body
   (see `docs/SYNC_WITH_BACKEND.md`). End each message with:
   `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

5. **Verify before merge — don't merge red.** Run the frontend gates:
   ```bash
   npx tsc -b        # type-check (catches frontend↔backend contract drift)
   npm run lint      # 3 pre-existing react-refresh errors in shadcn badge/button/tabs are
                     # known & out of scope — introduce NO new ones
   npm run build     # production build must be green
   ```

6. **Push** the branch (token-over-HTTPS, **Basic** auth). The token comes from the `GH_TOKEN`
   env var, falling back to `.env`:
   ```bash
   TOKEN="${GH_TOKEN:-$(grep -m1 '^GH_TOKEN=' .env | cut -d= -f2- | tr -d '\r\n')}"
   B64=$(printf 'x-access-token:%s' "$TOKEN" | base64 -w0)
   git -c http.extraheader="AUTHORIZATION: Basic $B64" push \
     https://github.com/markwuenschel-dev/perf-lab-web.git HEAD:<branch>
   ```

7. **Open a detailed PR** via REST (`POST /repos/markwuenschel-dev/perf-lab-web/pulls`,
   `Authorization: Bearer $TOKEN`) — sections: Summary, What's included, Testing (with the
   `tsc`/`lint`/`build` results from step 5), Notes (call out the Netlify deploy and any backend
   contract changes). Build the JSON payload in python to avoid escaping.

8. **Merge** the PR via REST (`PUT /repos/markwuenschel-dev/perf-lab-web/pulls/<n>/merge`) with
   the chosen `merge_method` (`merge` by default; `squash`/`rebase` if requested). **Never** pass
   anything that bypasses branch protection / required checks — if a merge is blocked, stop and
   report; don't force it.

9. **Clean up:** delete the merged remote branch
   (`DELETE /repos/markwuenschel-dev/perf-lab-web/git/refs/heads/<branch>`), then sync local:
   ```bash
   git checkout main && git pull --ff-only
   git branch -d <branch>
   git fetch -p              # prune the stale remote-tracking ref
   git branch --merged main # catch any other stale merged branches → remove local + remote
   ```

10. **Report** PR number/URL, merge SHA, what was deleted, and remind that the merge triggered a
    Netlify production deploy.

**Never** echo the token or write it anywhere outside `.env` / `.claude/settings.local.json`.
Confirm before merging if tests fail or the diff looks unexpected.
