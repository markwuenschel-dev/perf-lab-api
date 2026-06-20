---
status: accepted
date: 2026-06-20
---
# Keep auth outside `/v1`

Auth routes live at `/auth/*`; modern domain routes live under `/v1`. The token
endpoint uses OAuth2 password-form conventions and is consumed differently from the
versioned training API, so it is not versioned alongside it.

**Guardrail:** do not document or move auth to `/v1/auth/*` unless the router is
actually relocated.
