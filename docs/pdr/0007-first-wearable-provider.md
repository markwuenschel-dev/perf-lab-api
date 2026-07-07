---
status: accepted
date: 2026-06-20
decided: 2026-07-06
---
# First wearable provider to integrate

Given cloud-API-first wearables
([PDR-0006](0006-wearable-sync-cloud-api-first.md)), which provider ships first —
**Oura** or **Whoop**? This is a product/market call that should follow the actual
user device mix, which we don't have yet.

**Decision (2026-07-06): Oura first.** The deciding factor is bootstrapping our own
first-party data: the primary athlete uses an Oura ring, so shipping Oura immediately
unblocks the real HRV/sleep/RHR stream the shadow subsystems (EKF, MPC, hierarchical
personalization) need to validate against before promotion. Oura also offers a Personal
Access Token, so a single user can connect without standing up a full OAuth app — the
fastest path to data. The guardrail below held: Oura lives behind
`app.integrations.base.WearableAdapter`, so Whoop/Polar are additive.

**Guardrail (held):** the provider layer sits behind a normalizing adapter interface so
the first-provider choice isn't load-bearing — adding a second provider does not reshape
the wellness model. Concrete: `OuraAdapter` normalizes into `NormalizedWellness`, and the
sync service/API/cron never import a concrete provider.
