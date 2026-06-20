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
