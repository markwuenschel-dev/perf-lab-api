"""OAuth secrets never reach the server's logs, checked through a real uvicorn server (ADR-0075).

An in-process ``caplog`` sees handler logs only. Uvicorn writes its access log line, query
string included, for every request; an OAuth return carries the code and state in that query
before any handler or browser cleanup runs. So this starts uvicorn in a subprocess, sends it the
returns a browser would, plus duplicate, percent-encoded and undecodable parameter names and an
exchange failure whose exception text echoes the code, and reads everything the process wrote.
"""
import base64
import hashlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from conftest import TEST_DATABASE_URL

from app.core.log_redaction import redact_target

ROOT = Path(__file__).resolve().parents[1]
VERIFIER = "SENTINELVERIFIER" + "v" * 40
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()

_LAUNCH = r'''
import sys, uvicorn
from app.services import wearable_service
from app.integrations.base import TokenBundle

class Echoing:
    provider = "oura"
    def build_authorize_url(self, state, *, code_challenge, redirect_uri):
        return f"https://fake.oura/authorize?state={state}&code_challenge={code_challenge}"
    async def exchange_code(self, code, *, code_verifier, redirect_uri):
        raise RuntimeError(f"provider echoed {code} {code_verifier}")
    async def refresh_tokens(self, refresh_token):
        raise RuntimeError("unused")
    async def fetch_daily_wellness(self, access_token, start, end):
        return []

wearable_service._adapter = lambda provider="oura": Echoing()
uvicorn.run("app.main:app", host="127.0.0.1", port=int(sys.argv[1]), log_level="debug")
'''


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize("target, expected", [
    ("/a?code=x&state=y&keep=1", "/a?code=[redacted]&state=[redacted]&keep=1"),
    ("/a?CODE=x&%63ode=y&code=z", "/a?CODE=[redacted]&%63ode=[redacted]&code=[redacted]"),
    ("/a?Code+=x", "/a?Code+=[redacted]"),
    ("/a?%FF%FE=x&keep=1", "/a?[query suppressed]"),
    ("/a", "/a"),
    ("/a?", "/a?"),
])
def test_redact_target(target: str, expected: str) -> None:
    assert redact_target(target) == expected


def test_no_oauth_secret_reaches_the_server_logs(async_db, tmp_path) -> None:
    port = _free_port()
    log = tmp_path / "server.log"
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL, "ENVIRONMENT": "development",
           "WEB_APP_URL": "https://perflab.test", "PYTHONUNBUFFERED": "1"}
    with log.open("wb") as out:
        proc = subprocess.Popen([sys.executable, "-c", _LAUNCH, str(port)], cwd=ROOT, env=env,
                                stdout=out, stderr=subprocess.STDOUT)
        base = f"http://127.0.0.1:{port}"
        try:
            with httpx.Client(base_url=base, timeout=10) as client:
                for _ in range(100):
                    try:
                        if client.get("/ping").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.2)
                else:
                    pytest.fail("server did not start:\n" + log.read_text(errors="replace"))

                email = "redaction@test.com"
                assert client.post("/auth/register", json={"email": email, "password": "pw-123456"}).status_code == 201
                tok = client.post("/auth/token", data={"username": email, "password": "pw-123456"}).json()["access_token"]
                auth = {"Authorization": f"Bearer {tok}"}

                # Both callback paths, as a browser returning from Oura hits them.
                client.get("/settings/oura-callback?code=SENTINEL-A&state=SENTINEL-B")
                client.get("/v1/integrations/oura/callback?code=SENTINEL-C&state=SENTINEL-D")
                client.post("/v1/integrations/oura/callback?code=SENTINEL-E&state=SENTINEL-F")
                # Duplicate, case-folded, percent-encoded and undecodable names.
                client.get("/settings/oura-callback?code=ok&code=SENTINEL-G&%63ode=SENTINEL-H"
                           "&STATE=SENTINEL-I&error_description=SENTINEL-J&keep=visible")
                client.get("/settings/oura-callback?%FF%FE=SENTINEL-K&code=SENTINEL-L")

                # An exchange failure whose exception text carries the code and the verifier.
                r = client.get("/v1/integrations/oura/authorize", headers=auth,
                               params={"code_challenge": CHALLENGE, "code_challenge_method": "S256"})
                assert r.status_code == 200, r.text
                state = parse_qs(urlsplit(r.json()["authorize_url"]).query)["state"][0]
                r = client.post("/v1/integrations/oura/complete", headers=auth,
                                json={"state": state, "code": "SENTINEL-M", "code_verifier": VERIFIER})
                assert r.status_code == 409 and r.json()["detail"]["reason"] == "exchange_failed"
        finally:
            proc.terminate()
            proc.wait(timeout=20)

    text = log.read_text(errors="replace")
    # The access log really ran (so its silence about secrets means something) ...
    assert "/settings/oura-callback?code=[redacted]&state=[redacted]" in text
    assert "keep=visible" in text
    assert "/settings/oura-callback?[query suppressed]" in text
    assert "OAuth exchange failed for attempt" in text
    # ... and no secret is anywhere in what the process wrote.
    assert "SENTINEL" not in text, [line for line in text.splitlines() if "SENTINEL" in line]
    assert state not in text
