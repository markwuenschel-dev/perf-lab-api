#!/usr/bin/env bash
# One-command deploy of perf-lab-api to the shared AWS EC2 box (docs/DEPLOYMENT.md has details).
#
#   ./scripts/deploy.sh                 # deploy latest main
#   ./scripts/deploy.sh <sha>           # roll back / deploy a specific commit or tag
#   DRY_RUN=1 ./scripts/deploy.sh       # print the remote script instead of running it
#
# The manual loop: ssh -> git pull the BUILD SOURCE -> compose rebuild -> logs, plus an
# alembic-head check and a public-URL health check at the end. A deploy costs nothing per run:
# the pull and the docker build happen on the box (flat EC2 bill), and DNS is not involved.
#
# Two dirs on the box (see docs/DEPLOYMENT.md + the deployment memory):
#   /opt/stack/perf-lab-api  — the git checkout that is the BUILD SOURCE (must be advanced first,
#                              or `build` re-bakes stale code: a silent no-op).
#   /opt/stack/infra         — the docker-compose "stack" (project `stack`); build/run from here.
# Migrations run in the image CMD (`alembic upgrade head` before uvicorn), so the rebuild+up
# brings the schema to head; the alembic check below just proves it landed.
set -euo pipefail

REF="${1:-main}"
KEY="${SSH_KEY:-$HOME/.ssh/shared-box.pem}"
BOX="${BOX:-ubuntu@44.198.76.44}"
URL="${URL:-https://perflab.44-198-76-44.nip.io}"
TAIL="${TAIL:-40}"

# The box's clone is deploy-only (never edited in place), so main can be hard-synced to origin.
# Any other ref — a rollback SHA, a tag — is checked out detached; `git checkout main` restores.
if [ "$REF" = "main" ]; then
  SYNC="git checkout -q main && git reset --hard origin/main"
else
  SYNC="git checkout -q --detach '$REF'"
fi

REMOTE=$(cat <<EOF
set -eu
cd /opt/stack/perf-lab-api
git fetch -q origin
$SYNC
echo "deploying \$(git rev-parse --short HEAD): \$(git log -1 --format=%s)"
cd /opt/stack/infra
sudo docker compose up -d --build perf-lab-api
sudo docker compose logs --tail=$TAIL perf-lab-api || echo "(log tail failed - check manually; deploy itself already succeeded)"
# Boot runs 'alembic upgrade head' before uvicorn; give it a moment, then prove the head landed.
# (Checking too early can catch the OLD head mid-migration — a race, not a failure.)
sleep 8
echo "alembic current:"
sudo docker compose exec -T perf-lab-api alembic current || echo "(alembic check failed - verify manually)"
EOF
)

if [ -n "${DRY_RUN:-}" ]; then
  printf '%s\n' "$REMOTE"
  exit 0
fi

[ -f "$KEY" ] || { echo "SSH key not found: $KEY" >&2; exit 1; }
# tr guards against CRLF sneaking into the stream (e.g. a CRLF checkout of this file) — a stray \r
# makes bash on the box see 'perf-lab-api\r' and compose reports "no such service".
printf '%s\n' "$REMOTE" | ssh -i "$KEY" "$BOX" "tr -d '\r' | bash -s"

# Prove the public URL serves the new build — logs alone don't show what Caddy is fronting.
code=$(curl -fsSL -o /dev/null -w '%{http_code}' --max-time 30 "$URL/ping") ||
  { echo "deployed, but the health check against $URL/ping failed" >&2; exit 1; }
echo "$URL/ping -> HTTP $code"
