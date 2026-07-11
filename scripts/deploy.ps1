#!/usr/bin/env pwsh
# One-command deploy of perf-lab-api to the shared AWS EC2 box (docs/DEPLOYMENT.md has details).
#
#   ./scripts/deploy.ps1                # deploy latest main
#   ./scripts/deploy.ps1 -Ref <sha>     # roll back / deploy a specific commit or tag
#   ./scripts/deploy.ps1 -DryRun        # print the remote script instead of running it
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

param(
  [string]$Ref = "main",
  [string]$SshKey = (Join-Path $HOME ".ssh/shared-box.pem"),
  [string]$BoxHost = "ubuntu@44.198.76.44",
  [string]$Url = "https://perflab.44-198-76-44.nip.io",
  [int]$Tail = 40,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# The box's clone is deploy-only (never edited in place), so main can be hard-synced to origin.
# Any other ref — a rollback SHA, a tag — is checked out detached; `git checkout main` restores.
$sync = if ($Ref -eq "main") {
  "git checkout -q main && git reset --hard origin/main"
} else {
  "git checkout -q --detach '$Ref'"
}

$remote = @"
set -eu
cd /opt/stack/perf-lab-api
git fetch -q origin
$sync
echo "deploying `$(git rev-parse --short HEAD): `$(git log -1 --format=%s)"
cd /opt/stack/infra
sudo docker compose up -d --build perf-lab-api
sudo docker compose logs --tail=$Tail perf-lab-api || echo "(log tail failed - check manually; deploy itself already succeeded)"
# Boot runs 'alembic upgrade head' before uvicorn; give it a moment, then prove the head landed.
# (Checking too early can catch the OLD head mid-migration — a race, not a failure.)
sleep 8
echo "alembic current:"
sudo docker compose exec -T perf-lab-api alembic current || echo "(alembic check failed - verify manually)"
"@

if ($DryRun) { Write-Host $remote; exit 0 }

if (-not (Test-Path $SshKey)) { throw "SSH key not found: $SshKey" }
# PowerShell's pipe to a native command appends CRLF to the FINAL line (and a CRLF checkout of this
# file would put \r on every line), so bash on the box would see 'perf-lab-api\r' — "no such
# service". Strip CRs on the remote side before bash reads the script.
$remote | ssh -i $SshKey $BoxHost "tr -d '\r' | bash -s"
if ($LASTEXITCODE -ne 0) { throw "deploy failed (ssh exit $LASTEXITCODE)" }

# Prove the public URL serves the new build — logs alone don't show what Caddy is fronting.
$code = & curl.exe -fsSL -o NUL -w "%{http_code}" --max-time 30 "$Url/ping"
if ($LASTEXITCODE -ne 0) { throw "deployed, but the health check against $Url/ping failed" }
Write-Host "$Url/ping -> HTTP $code"
