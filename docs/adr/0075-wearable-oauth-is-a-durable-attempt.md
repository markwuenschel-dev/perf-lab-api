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

6. **Time comes from the database, sampled once per statement after the row is locked**, and is
   used both for that statement's guard and for the time it records. Each transition locks its
   row in a separate statement first: an UPDATE samples its clock before waiting for a locked
   row and rechecks its guard with that old sample, so a claim queued behind another
   transaction would otherwise judge expiry as of before the wait. Creation records `expires_at = created_at +
   10 minutes` from the same sample. `claimed_at` and `outcome_at` are ordering timestamps, not
   exact wall-clock observations: the claim records `GREATEST(clock, created_at)` and every
   outcome (success, exchange failure, cancellation, expiry, stale claim) records
   `GREATEST(clock, created_at, claimed_at)`. A database clock that steps backward therefore
   cannot break the time-order CHECK, which stays. Each clamp logs a warning with the attempt id
   and its size, so clock trouble stays visible. Measured on 2026-10-14: the local Docker VM's
   clock stepped backward 0.5–6.4 s about every 27 s.
7. **The finalizing transaction is the uncertainty boundary.** The lock, the connection write,
   the receipt and the commit sit inside one guard. Any failure in it rolls back and answers
   503 `outcome_unknown`, so the client keeps its inputs and retries. Saving a failed exchange
   is guarded the same way: the code is spent either way, so a retry reads whatever landed and
   never exchanges again.

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
- Expiry and staleness compare against the raw database clock, not the clamped times. Their
  elapsed-time guarantees depend on that clock: a backward step lengthens an attempt's lifetime
  and the wait before a claim counts as stale, and repeated steps can lengthen them by more
  than any single step's size.
- The transaction tells a provider's refusal (`exchange_rejected`) from any other failure
  (`exchange_failed`) only through `app.integrations.base.ProviderRejected`, which each adapter
  raises for its own refusals (Oura: a non-200 token reply). It never imports a provider
  (ADR-0044); `tests/test_wearable_provider_seam.py` enforces that for every module in
  `app/services/`. A local adapter failure, such as missing client credentials, is not a
  refusal.
- Two refreshes of the same generation running at once are an acknowledged limitation. Oura's
  single-use refresh tokens make the second fail at the provider; they do not serialize sync.
- The Oura console must register the new redirect URI before P6b ships, and keep the old one
  until P6b's rollback window closes (docs/DEPLOY.md, P6).
