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
   (`GET /repos/markwuenschel-dev/perf-lab-web/...`). **Read the full diff** —
   `git diff main...HEAD` plus `git log main..HEAD` — it is the *source material* for the
   commit messages and the PR body. Note every touched file and the user-facing effect of
   each change; you will enumerate these in the PR, so do not skim.

2. **Branch.** If on `main` (the default branch), create a `feat/…` / `fix/…` / `docs/…` /
   `chore/…` branch first — never commit straight to `main`. If already on a feature branch,
   stay on it.

3. **Don't commit junk.** Build artifacts / caches (`dist/`, `node_modules/`, Vite caches,
   coverage) belong in `.gitignore`, not in a commit. Never stage `.env` or
   `.claude/settings.local.json` (they hold the token). Add ignores rather than staging them.

4. **Commit** in logical, Conventional-Commit-style groups (`feat(web): …`, `fix: …`,
   `docs: …`, `chore: …`), one concern per commit. Each message has a **multi-line body**
   that states *what* changed and *why* in specifics (file/area + effect) — never a bare
   subject line, never "updates code". If `src/types.ts` or API contracts changed, note the
   backend sync in the body (see `docs/SYNC_WITH_BACKEND.md`). End each message with:
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

7. **Open a PR with a detailed, evidence-based body** via REST
   (`POST /repos/markwuenschel-dev/perf-lab-web/pulls`, `Authorization: Bearer $TOKEN`).
   Build the JSON payload in python to avoid escaping.

   **This is the step that matters most — never open a thin PR.** No one-line bodies, no
   empty/placeholder sections, no "updates the code". Drive *every* section from the real
   `git diff main...HEAD` and the commit log read in step 1 — describe what the diff actually
   does, not what you remember doing.

   **Title:** Conventional-Commit style and *specific* — name the area and the actual change
   (e.g. `feat(web): wire Log Workout to simulate-dose + log-workout`). Never generic
   (`update`, `changes`, `wip`).

   **Body — fill every section; drop one only if genuinely N/A and say "n/a — <reason>":**

   ```markdown
   ## Summary
   2–4 sentences: the problem this solves, the outcome, and why now.

   ## What changed
   One bullet per meaningful change, grouped by file/area, concrete and enumerated from
   the diff (file → what changed → effect). No "misc" or "various".

   ## Why / context
   Motivation, constraints, and any design decision or trade-off. Link related docs/issues
   (e.g. `docs/SYNC_WITH_BACKEND.md`) and prior PRs.

   ## Testing
   The actual results from step 5 — `tsc -b`, `npm run lint` (note the 3 known shadcn
   errors), `npm run build` — plus any manual verification performed.

   ## Notes / risk
   - Merging `main` triggers a **Netlify production deploy**.
   - Backend contract / type sync, if `src/types.ts` or API calls changed.
   - Known blockers, follow-ups, and how to roll back (revert the merge commit).

   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   ```

   Scale the *length* to the change (a one-file chore is shorter than a feature), but every
   PR still explains **what** and **why** with specifics pulled from the diff.

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
