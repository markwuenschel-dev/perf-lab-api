---
status: accepted
date: 2026-06-19
---
# Wearable sync is cloud-API providers first

Wearable integration targets **cloud-API providers** (Oura / Whoop / Polar) via
server-side OAuth + a nightly pull, with manual entry as the universal fallback. Apple
Health and Garmin are **deferred**: the web-only stack can't read HealthKit without a
native shell, so they're out of scope until there's a native client.

We rejected a device-SDK / on-device-first approach for the current web stack.

**Guardrail:** wearable work targets server-side cloud APIs + manual fallback; do not
take on Apple Health / Garmin until a native client exists.

## Amendment, 2026-09-28: Apple Watch through a phone-pushed ingest seam

The athlete asked for Apple Watch data in the morning check-in. The guardrail's premise
still holds: a web app cannot read HealthKit. The amendment is that the client that reads it
does not have to be ours. An **iOS Shortcut** (built into iOS) reads HRV, resting heart rate
and sleep from Health each morning and pushes them to us.

- **The seam is generic, not a "Shortcut endpoint".** `POST /v1/wellness/ingest` accepts a
  personal ingest token that is user-bound, stored only as a SHA-256, revocable, and able
  to do nothing but write wellness. Health Auto Export or a future native client can post
  to the same endpoint unchanged.
- **Idempotent** on (athlete, date, source), so a retrying automation replaces the day's
  row instead of duplicating it.
- **Device authority, decided by the athlete, not by accuracy.** When a cloud-synced device
  (Oura) has a same-day reading, it is used; otherwise the Apple Watch's is. Resolution is
  per signal. A pushed device still outranks a hand-entered value on measured signals, and
  never outranks the athlete on soreness, mood or stress
  (`app/logic/wellness_source_authority.py`, `PUSHED_DEVICE_SOURCES`). A per-athlete source
  preference may replace this rule later.
- **Visible staleness.** Settings shows each token's last successful push and flags one
  older than 36 h. A failed Apple sync never blocks the check-in.

Still deferred: a native iOS app (only if the product needs one for reasons beyond wellness
ingestion), and Garmin. The recipe is in `docs/guides/apple-watch-shortcut.md`.
