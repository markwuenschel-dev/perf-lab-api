---
status: proposed
date: 2026-06-20
---
# First wearable provider to integrate

Given cloud-API-first wearables
([PDR-0006](0006-wearable-sync-cloud-api-first.md)), which provider ships first —
**Oura** or **Whoop**? This is a product/market call that should follow the actual
user device mix, which we don't have yet.

Deferred to P6; capture device mix from real users (or onboarding) before committing.
No lean recorded on purpose — deciding now would be guessing.

**Guardrail:** build the provider layer behind a normalizing adapter interface so the
first-provider choice isn't load-bearing — adding a second provider should not require
reshaping the wellness model.
