"""Cross-module coherence of the engine_state version constants (PA-02).

Two constants govern the ``engine_state`` schema version, and they live in different
modules with nothing linking them:

* ``state_bridge.ENGINE_STATE_SCHEMA_VERSION`` — the version a *writer* stamps onto a
  freshly persisted payload (via ``default_engine_state_dict`` and the write path).
* ``engine_state_codec.MAX_READ_VERSION`` — the newest version the strict *reader*
  (``decode_engine_state``) understands; anything higher raises
  ``UnsupportedFutureEngineStateVersion``.

The danger is a one-sided bump: raise ``ENGINE_STATE_SCHEMA_VERSION`` to write v3 without
raising ``MAX_READ_VERSION``, and the strict codec rejects every freshly written row as a
"future version" — a self-inflicted outage on the next deploy. Nothing caught that today
because the two constants are unrelated integers in unrelated files.

The invariant is **not** equality. The codec docstring documents a readers-deploy-first
rollout ("deploy readers before enabling writers of the new version"), which deliberately
runs the reader *ahead* of the writer for a window. So the safety property is one-sided:
the writer must never get ahead of the strict reader.

    ENGINE_STATE_SCHEMA_VERSION <= MAX_READ_VERSION

Pure tests — no database, no fixtures. Scope note: this pins the *strict* codec against
the writer. The permissive ``state_bridge._migrate_engine_state`` path is out of scope
here (its future-version handling is PA-01's slice).
"""

import pytest

from app.engine import engine_state_codec
from app.engine.engine_state_codec import (
    MAX_READ_VERSION,
    UnsupportedFutureEngineStateVersion,
    decode_engine_state,
)
from app.engine.state_bridge import (
    ENGINE_STATE_SCHEMA_VERSION,
    default_engine_state_dict,
)


def test_writer_version_never_exceeds_strict_reader():
    """The version a writer stamps must always be readable by the strict codec.

    Red-capable: bump ``ENGINE_STATE_SCHEMA_VERSION`` to write a new version without
    bumping ``MAX_READ_VERSION`` and this fails, before the mismatch reaches production
    and the strict reader starts rejecting fresh writes.
    """
    assert ENGINE_STATE_SCHEMA_VERSION <= MAX_READ_VERSION, (
        f"writer stamps v{ENGINE_STATE_SCHEMA_VERSION} but the strict codec only reads "
        f"up to v{MAX_READ_VERSION}; bump MAX_READ_VERSION (and add an upgrade branch) "
        f"before ENGINE_STATE_SCHEMA_VERSION, or the strict reader will reject fresh rows"
    )


def test_default_written_payload_decodes_through_strict_codec():
    """The concrete coherence: what the writer produces, the strict reader accepts.

    Exercises the real path rather than the abstract constants — a freshly written
    default payload must decode cleanly, not raise a future-version error.
    """
    decoded = decode_engine_state(default_engine_state_dict())
    assert decoded.version == MAX_READ_VERSION


def test_guard_is_red_capable_when_writer_gets_ahead(monkeypatch: pytest.MonkeyPatch):
    """Prove the coherence check would actually catch a bad bump.

    Simulate the dangerous state — the strict reader one version behind the writer —
    by lowering ``MAX_READ_VERSION`` below what ``default_engine_state_dict`` stamps, and
    confirm the strict codec then refuses the freshly written payload. This is the exact
    failure ``test_default_written_payload_decodes_through_strict_codec`` guards against.
    """
    monkeypatch.setattr(engine_state_codec, "MAX_READ_VERSION", ENGINE_STATE_SCHEMA_VERSION - 1)
    with pytest.raises(UnsupportedFutureEngineStateVersion):
        decode_engine_state(default_engine_state_dict())
