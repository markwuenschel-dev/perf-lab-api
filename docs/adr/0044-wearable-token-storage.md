---
status: accepted
date: 2026-07-06
---
# Wearable token storage and OAuth identity handling

Phase 2 (Oura wearable sync, [PDR-0006](../pdr/0006-wearable-sync-cloud-api-first.md),
[PDR-0007](../pdr/0007-first-wearable-provider.md)) introduces the first long-lived
third-party credentials the app holds: OAuth access/refresh tokens and Personal Access
Tokens. Three decisions were needed.

## 1. Tokens encrypted at rest (Fernet)

`WearableConnection.access_token_enc` / `refresh_token_enc` store **Fernet ciphertext**,
never plaintext. A DB dump must not leak live wearable credentials. The key lives in
`APP_ENCRYPTION_KEY` (an env variable — e.g. `/opt/stack/infra/env/perf-lab-api.env` on
the EC2 stack, see `docs/DEPLOY.md`; previously a Railway variable before hosting moved
to EC2 2026-07-10); `app.core.crypto` is the only module that
encrypts/decrypts, and `app.services.wearable_service` the only caller. Encryption is
lazy/opt-in — the key is required only when a connection is stored or synced, so the rest
of the app boots without it. `cryptography` is already present transitively via
`python-jose[cryptography]`, so no new dependency.

## 2. OAuth identity rides a signed `state` token

The OAuth callback (`GET /v1/integrations/oura/callback`) is a browser redirect and
carries **no `Authorization` header**, so `get_current_user` can't run there. Instead
`get_authorize_url` mints a short-lived (10 min) JWT — signed with the app `SECRET_KEY`,
`purpose="oura_oauth"` — as the OAuth `state`, and the callback verifies it to recover the
user id. This doubles as CSRF protection (an attacker can't forge `state`). Reuses the
existing `jose` infra; the distinct `purpose` claim prevents confusion with access tokens.

## 3. Ingestion reuses the canonical wellness sink

The adapter normalizes provider data into `NormalizedWellness`, and sync writes it through
the **existing** `readiness_service.upsert_wellness_sample` (idempotent on
`(user_id, date, source)`, `source="oura"`). No parallel ingestion path — readiness,
baselines, and the shadow loops pick the data up unchanged
([ADR-0026](0026-readiness-combine-rule.md)).

**Guardrails:**
- Never serialize token columns in an API response (`WearableConnectionOut` omits them).
- Provider specifics stay behind `WearableAdapter` ([PDR-0007](../pdr/0007-first-wearable-provider.md)).
- Rotating `APP_ENCRYPTION_KEY` invalidates stored tokens (users must reconnect); document
  before any rotation.

## Known limitation

`compute_readiness` uses the single latest wellness sample across all sources, so on a day
with both a manual check-in and an Oura pull only one row feeds readiness (Oura carries
HRV/sleep/RHR but not soreness/mood). Multi-source merge is deferred; revisit if the split
proves lossy.
