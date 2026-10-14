---
status: accepted
date: 2026-10-13
---
# Connecting a wearable over OAuth is a durable attempt, bound to its browser by PKCE

## Context

Until P6, Oura OAuth carried the athlete's identity through the provider redirect in a signed
JWT `state` (`wearable_service.sign_state`). The token was stateless, so it could be replayed
until it expired, and it was bound to neither the browser nor the attempt. The redirect went to an
unauthenticated API route (`GET /v1/integrations/oura/callback`), which exchanged the code and
upserted the connection in one step. Nothing recorded the attempt, so a duplicate or failed
callback could not be told apart from an earlier success. A transport error reached the athlete
as a JSON 500 instead of a message.

Oura documents PKCE with `S256` and still requires the client secret at the token endpoint
(cloud.ouraring.com/docs/authentication).

## Decision

1. **An attempt is a row** (`oauth_authorizations`, a061). Its inputs never change: owner,
   provider, the sha256 of `state`, the PKCE challenge, the redirect URI it was sent with,
   creation time and expiry. Its status moves only `pending → claimed → succeeded | failed`
   or `pending → failed`, and a terminal attempt is final. The database enforces this: a CHECK
   fixes each status's columns, and a trigger enforces the transitions and immutability. The
   one change a terminal row accepts is its live `connection_id` going NULL when the connection
   is deleted.
2. **A receipt describes its attempt, never the present.** A success keeps the connection id
   and the `generation` it linked (`receipt_*`, with no FK, so a disconnect cannot change them).
   Whether the athlete is connected now comes only from `GET /connection`.
3. **`wearable_connections.generation`** counts successful links of a row, from 1. Every link
   advances it, both OAuth and PAT. A refresh never does, and writes only while the generation
   it read is unchanged. A connection deleted and recreated is a new row at 1.
4. **The redirect target is the SPA**, `WEB_APP_URL + /settings/oura-callback`, derived rather
   than configured (E2). The SPA completes the attempt with an authenticated `POST /complete`,
   which proves possession of the attempt's PKCE verifier. The old API callback never exchanges
   a code again.
5. **Released in two slices (R2).** P6a is schema plus rollback compatibility: a061, a rejection
   of PKCE requests it cannot honour, and a web handler for callbacks it cannot finish. P6b is
   the transaction, with no migration. Rolling P6b back is `deploy.sh <P6a SHA>` alone: no
   downgrade, no deletion and no env change. `OURA_REDIRECT_URI` remains on the box as P6a's
   configuration.

### The four crossings between releases

| | who arrives | at | outcome |
|---|---|---|---|
| ① | a legacy (JWT) consent in flight at cutover | P6b's old `/callback` | GET → 303 `/settings?oura=error&reason=interrupted`; other methods 410; no exchange, nothing logged |
| ② | a cached P6a tab clicking Connect | P6b `/authorize` without a challenge | 400 "reload, then Connect Oura again"; never a flow without PKCE |
| ③ | a P6b consent in flight at rollback | P6a's SPA on `/settings/oura-callback` | "Oura sign-in was interrupted. Connect Oura again."; URL and verifier cleared; no call |
| ④ | a cached P6b tab after rollback | P6a `/authorize` with a challenge | 400 "reload, then Connect Oura again"; never silently started without PKCE |

## Consequences

- During a P6b → P6a rollback, the legacy writer replaces credentials without advancing
  `generation`. Generation tracking is not promised across that interval. The attempt history
  and generations already recorded survive it.
- The a061 downgrade refuses while any attempt exists or any connection is past generation 1.
  It is never part of a routine rollback.
- Two refreshes of the same generation running at once are an acknowledged limitation. Oura's
  single-use refresh tokens make the second fail at the provider; they do not serialize sync.
- The Oura console must register the new redirect URI before P6b ships, and keep the old one
  until P6b's rollback window closes (docs/DEPLOY.md, P6).
