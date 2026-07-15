# AGENTS.md

Operating guide for **any** AI coding agent working in this repo (Claude Code, Codex,
Grok, Cursor, etc.). The human initiates pushes/merges; when something is ambiguous or
blocked, stop and ask rather than forcing it.

## Git & PR workflow

1. **Never commit directly to `main`.** Branch first:
   `git checkout -b <type>/<short-desc>` (`feat/…`, `fix/…`, `chore/…`, `docs/…`).
2. **Commit style:** Conventional Commits (`feat(web): …`, `fix: …`, `docs: …`).
   End every commit message with your agent's `Co-Authored-By:` trailer.
3. **Open a PR** with a descriptive title and a detailed body (summary, what changed,
   verification, test plan).
4. **Auto-merge + clean up is the DEFAULT** once a PR is opened (no need to ask again):
   - Enable auto-merge with a **merge commit**, set to delete the branch on merge.
   - With `gh`: `gh pr merge <pr> --merge --auto --delete-branch`
     (drop `--auto` and merge immediately if the PR is already mergeable / has no
     required checks).
   - Without `gh` (e.g. when only the REST API + a token are available): merge via
     `PUT /repos/{owner}/{repo}/pulls/{n}/merge` with `{"merge_method":"merge"}`,
     then delete the remote branch via
     `DELETE /repos/{owner}/{repo}/git/refs/heads/{branch}`.
5. **On a successful merge, delete the branch on GitHub *and* locally**, and sync `main`:
   ```
   git checkout main && git pull --ff-only
   git branch -d <branch>      # local
   git fetch -p                # prune the stale remote-tracking ref
   ```
   (The remote branch is deleted by `--delete-branch` / the API call above.)
6. **Never pass `--admin`** (or otherwise bypass branch protection / required checks)
   **unless the human explicitly says so for that specific merge.** If a merge is blocked
   by branch protection or failing checks, stop and report — do not force it.

## Auth (push / PR / merge)

- Credentials come from a **`GH_TOKEN`** (and `GITHUB_TOKEN`) environment variable,
  supplied via local agent config that is **never committed** (for Claude Code:
  `.claude/settings.local.json` → `env`).
- git is configured globally (`credential.helper`) to read that token automatically, so
  `git push`/`fetch` and REST API calls work without prompts.
- **Never print, echo, or commit the token**, and never write it into a tracked file.
  Prefer a fine-grained, repo-scoped, expiring PAT.

## Build & verify (run before pushing)

- `npx tsc -b` — type-check (catches frontend↔backend contract drift)
- `pnpm run lint` — 3 pre-existing `react-refresh` errors in generated shadcn UI files
  (`badge`/`button`/`tabs`) are known and out of scope; introduce no new ones
- `pnpm run build` — the production build must be green

## Project conventions

- **Tailwind v4, CSS-first:** design tokens live in `src/index.css` `@theme inline` —
  there is **no** `tailwind.config.js`.
- `src/types.ts` manually mirrors the FastAPI backend (`perf-lab-api`) schemas — keep
  them in sync (see `docs/SYNC_WITH_BACKEND.md`).
- All HTTP goes through `src/api/perfLabClient.ts`.
- Merging to `main` does **not** auto-deploy (Railway auto-deploy is retired). Production
  is a manual deploy of a self-hosted **EC2** docker-compose stack — this frontend is
  built into the `backend-with-frontend` Docker image (embeds the SPA at `/static`,
  same-origin) and shipped via `./scripts/deploy.ps1` / `.sh`. See
  [`../docs/DEPLOY.md`](../docs/DEPLOY.md) for the full runbook.
