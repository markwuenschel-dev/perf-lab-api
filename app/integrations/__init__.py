"""Wearable provider integrations (Phase 2).

Each provider adapter normalizes its native daily data into the canonical
``NormalizedWellness`` shape (see ``base``) so the rest of the app — the sync
service, the wellness sink, the readiness pipeline — is provider-agnostic. Oura is
the first provider (PDR-0007); Whoop/Polar can be added without touching callers.
"""
