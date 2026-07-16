"""INT-A1 — the production boot guards test the property, not a known-bad sentinel.

The rule these tests enforce:

    A production boot guard must refuse every configuration value that is not
    demonstrably safe, rather than accept every value that is not a known-bad
    sentinel.

Why this file exists. Both `_check_production_secrets` (INT-01) and
`_check_production_cors` (INT-09) originally compared the configured value against one
remembered bad example — `DEFAULT_SECRET_KEY`, `DEV_DEFAULT_ORIGINS`. Each therefore
refused the mistake its author imagined and admitted the one an operator is likely to
make:

  * the key `.env.example` ships is neither the sentinel nor short enough to trip the
    length floor, so the documented `cp .env.example .env` path satisfied a guard whose
    entire purpose is to stop a published key from signing production tokens.
  * the CORS spec's non-origin values are not dev defaults, so they satisfied "an
    explicit origin is pinned" — the most permissive values possible clearing a guard
    that exists to require a restrictive one.

The durable form of the fix is the parametrisation over `.env.example` below: the guard
is pinned to the FILE, not to a copy of its contents, so editing that file cannot quietly
reopen the hole. The same instinct drives the regex decision — production refuses the
whole class rather than grading patterns, because a check that grades them can only ever
clear the shapes its author imagined.

INT-A3 extends the same rule to `DEBUG`, which drove SQLAlchemy `echo` and so decided
whether every INSERT — `hashed_password`, wearable OAuth ciphertext — was written to the
application log. It defaulted to True, so a production boot that merely *omitted* the
variable logged all of it. Same shape as the two above: the unsafe value was the one you
got by not deciding.

Scope note: the root `main.py` second app (INT-A22) is a separate ledger candidate and is
deliberately NOT touched here.

DB-free. Follows the `_settings(..., _env_file=None)` pattern from test_cors_prod_origin.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.core.config import (
    CORS_NON_ORIGINS,
    DEFAULT_SECRET_KEY,
    DEV_DEFAULT_ORIGINS,
    PUBLIC_EXAMPLE_SECRET_KEYS,
    Settings,
)

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"
PROD_ORIGIN = "https://perflab.44-198-76-44.nip.io"

# A strong key of the shape the guard's own message recommends (`openssl rand -hex 32`).
STRONG_KEY = "9f2c1a7e4b8d3056f1ae9c4b7d2085361ca7fe94b380d21c6a5f7e0b93d84c1f"


@contextmanager
def caplog_at(logger_name: str, level: int = logging.WARNING):
    """Capture records from one named logger, immune to global logging state.

    Same helper as test_cors_prod_origin.py — kept local rather than shared, since a
    conftest fixture would couple these two DB-free files to import order.
    """
    logger = logging.getLogger(logger_name)
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.setLevel(level)
    handler.emit = records.append  # type: ignore[method-assign]

    prev = (logger.level, logger.disabled)
    prev_disabled_root = logging.root.manager.disable
    logging.disable(logging.NOTSET)
    logger.setLevel(level)
    logger.disabled = False
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev[0])
        logger.disabled = prev[1]
        logging.disable(prev_disabled_root)


def _settings(**overrides: object) -> Settings:
    """Settings with explicit guard-relevant fields, ignoring the developer's real env."""
    base: dict[str, object] = {
        "ENVIRONMENT": "development",
        "SECRET_KEY": STRONG_KEY,
        "ALLOWED_ORIGINS": ",".join(DEV_DEFAULT_ORIGINS),
        "ALLOWED_ORIGIN_REGEX": "",
        "DEBUG": False,
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _env_example_values() -> dict[str, str]:
    """Every KEY=VALUE pair `.env.example` ships, uncommented."""
    values: dict[str, str] = {}
    for raw in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def test_env_example_is_readable_and_ships_the_guarded_keys() -> None:
    """Guards the guard: if .env.example stops shipping these, the tests below go vacuous."""
    values = _env_example_values()
    assert "SECRET_KEY" in values, f"{ENV_EXAMPLE} no longer ships SECRET_KEY"
    assert "ALLOWED_ORIGINS" in values, f"{ENV_EXAMPLE} no longer ships ALLOWED_ORIGINS"
    assert "DEBUG" in values, f"{ENV_EXAMPLE} no longer ships DEBUG"


def test_public_example_secret_keys_is_pinned_to_the_env_example_file() -> None:
    """THE anti-drift contract, and the reason the runtime constant is not just a longer
    enumeration.

    The guard cannot read `.env.example` at boot — the Dockerfile does not ship it — so the
    published-key set lives in code. That set is only trustworthy if something forces it to
    keep up with the file. This is that something.

    If this fails: add the new .env.example key to PUBLIC_EXAMPLE_SECRET_KEYS. Do not
    delete this test — an enumeration nothing pins to its source is exactly the defect
    INT-A1 fixed.
    """
    example_key = _env_example_values()["SECRET_KEY"]
    assert example_key in PUBLIC_EXAMPLE_SECRET_KEYS, (
        f"{ENV_EXAMPLE} ships SECRET_KEY={example_key!r}, which is not in "
        "PUBLIC_EXAMPLE_SECRET_KEYS — so production would boot on a published key."
    )


# --- INT-01: the secret guard -------------------------------------------------------


def test_no_secret_key_this_repo_ships_as_an_example_may_boot_production() -> None:
    """THE BUG. .env.example is documentation; every value in it is public.

    Parametrised over the file rather than over a copy of its value, so a future edit to
    .env.example cannot silently reopen this hole.
    """
    from app import main

    example_key = _env_example_values()["SECRET_KEY"]
    cfg = _settings(ENVIRONMENT="production", SECRET_KEY=example_key)
    with pytest.raises(RuntimeError):
        main._check_production_secrets(cfg)


@pytest.mark.parametrize(
    "key",
    [
        pytest.param(DEFAULT_SECRET_KEY, id="sentinel"),
        pytest.param("", id="empty"),
        pytest.param("   ", id="whitespace-only"),
        pytest.param("a" * 31, id="one-char-under-the-floor"),
    ],
)
def test_existing_secret_refusals_keep_refusing(key: str) -> None:
    """Regression guard. Widening the property must not narrow the enumeration."""
    from app import main

    cfg = _settings(ENVIRONMENT="production", SECRET_KEY=key)
    with pytest.raises(RuntimeError):
        main._check_production_secrets(cfg)


def test_a_strong_key_still_boots_production() -> None:
    """The positive fixture. A guard that refuses everything is not a guard."""
    from app import main

    cfg = _settings(ENVIRONMENT="production", SECRET_KEY=STRONG_KEY)
    main._check_production_secrets(cfg)


def test_the_example_key_warns_but_never_blocks_outside_production() -> None:
    """Fail fast in prod, warn elsewhere — the established contract. Dev must not break.

    Asserts the warning is actually emitted, not merely that nothing raised: an earlier
    version of this test called the guard and asserted nothing, so it passed against a
    no-op guard. A test that cannot fail is not a test.
    """
    from app import main

    example_key = _env_example_values()["SECRET_KEY"]
    cfg = _settings(ENVIRONMENT="development", SECRET_KEY=example_key)
    with caplog_at("perflab", logging.WARNING) as records:
        main._check_production_secrets(cfg)
    assert any("allows token forgery" in r.getMessage() for r in records), (
        "dev boot must warn about a published key, not stay silent"
    )


# --- INT-09: the CORS guard ---------------------------------------------------------


@pytest.mark.parametrize(
    "origins",
    [
        pytest.param("*", id="bare-wildcard"),
        pytest.param(f"{PROD_ORIGIN},*", id="wildcard-hiding-behind-a-pinned-origin"),
        pytest.param(" * ", id="padded-wildcard"),
        pytest.param("null", id="bare-null"),
        pytest.param(f"{PROD_ORIGIN},null", id="null-hiding-behind-a-pinned-origin"),
        pytest.param(f"{PROD_ORIGIN},NULL", id="uppercase-null"),
    ],
)
def test_a_cors_non_origin_never_boots_production(origins: str) -> None:
    """THE BUG. Neither `*` nor `null` is a dev default, so both satisfied "an explicit
    origin is pinned" — the two most permissive values passing a guard whose purpose is
    to require a restrictive one.

    `*`: Starlette sets `allow_all_origins = "*" in allow_origins`, and with
    allow_credentials=True reflects the caller's Origin back.

    `null`: the origin of a sandboxed iframe or a data: URL. An attacker gets it for free
    with `<iframe sandbox srcdoc="...fetch(api, {credentials:'include'})">`, so allowing
    `null` is allowing them — with credentials.

    The "hiding behind a pinned origin" cases matter most: pinning a real origin does not
    neutralise a non-origin sitting beside it.
    """
    from app import main

    cfg = _settings(ENVIRONMENT="production", ALLOWED_ORIGINS=origins)
    with pytest.raises(RuntimeError):
        main._check_production_cors(cfg)


def test_the_non_origin_set_is_the_cors_specs_closed_set() -> None:
    """Pins WHY this enumeration is legitimate where the secret-key one needed a file.

    The CORS spec defines exactly two magic non-origin values. That set is closed, so
    enumerating it is correct rather than sentinel-shaped. If someone widens this, the
    justification in config.py no longer holds and should be rewritten.
    """
    assert CORS_NON_ORIGINS == {"*", "null"}


@pytest.mark.parametrize(
    "regex",
    [
        pytest.param(".*", id="obviously-broad"),
        pytest.param(r"https://[a-z0-9-]+\.perflab\.internal", id="looks-narrow"),
        pytest.param(r"https://perflab\.44-198-76-44\.nip\.io", id="looks-exactly-pinned"),
    ],
)
def test_no_origin_regex_boots_production_however_narrow_it_looks(regex: str) -> None:
    """Production refuses regex origin matching outright — it does not judge the pattern.

    The parametrisation is the argument. All three are refused, including the last, which
    is a faithful transcription of the real production origin. That is the point: the
    guard does not grade patterns, because "narrow enough" cannot be established from a
    pattern. Any check that tried would clear the shapes its author imagined and stay
    quiet about the rest — the accept-unless-recognised idiom this whole module removes.

    Refusing the whole class costs nothing real: the setting defaults to disabled and
    nothing in the repo or the deployment sets it, while INT-09 already held that a regex
    alone never satisfies the explicit-origin requirement. Production now enforces it.
    """
    from app import main

    cfg = _settings(
        ENVIRONMENT="production",
        ALLOWED_ORIGINS=PROD_ORIGIN,
        ALLOWED_ORIGIN_REGEX=regex,
    )
    with pytest.raises(RuntimeError, match="does not accept regex origin matching"):
        main._check_production_cors(cfg)


def test_an_origin_regex_only_warns_outside_production() -> None:
    """Refusing the class must not break local development, which may still use a regex."""
    from app import main

    cfg = _settings(
        ENVIRONMENT="development",
        ALLOWED_ORIGINS=PROD_ORIGIN,
        ALLOWED_ORIGIN_REGEX=".*",
    )
    with caplog_at("perflab", logging.WARNING) as records:
        main._check_production_cors(cfg)
    assert any("regex origin matching" in r.getMessage() for r in records)


def test_no_allowed_origins_this_repo_ships_as_an_example_may_boot_production() -> None:
    """The .env.example origins are the dev defaults — already refused. Pins it anyway,
    so that if someone edits that file to something permissive, this fails."""
    from app import main

    example_origins = _env_example_values()["ALLOWED_ORIGINS"]
    cfg = _settings(ENVIRONMENT="production", ALLOWED_ORIGINS=example_origins)
    with pytest.raises(RuntimeError):
        main._check_production_cors(cfg)


def test_the_disabled_default_regex_is_what_lets_production_boot() -> None:
    """The counterpart to refusing the class: the shipped default must still boot.

    Refusing every regex is only tenable because the setting is disabled by default. If
    that default ever changes, production stops booting — and this test says why.
    """
    from app import main

    cfg = _settings(ENVIRONMENT="production", ALLOWED_ORIGINS=PROD_ORIGIN)
    assert cfg.ALLOWED_ORIGIN_REGEX == ""
    assert cfg.allowed_origin_regex is None
    main._check_production_cors(cfg)


def test_a_pinned_prod_origin_still_boots() -> None:
    """Regression guard: the INT-09 happy path is untouched."""
    from app import main

    cfg = _settings(
        ENVIRONMENT="production",
        ALLOWED_ORIGINS=",".join((*DEV_DEFAULT_ORIGINS, PROD_ORIGIN)),
    )
    main._check_production_cors(cfg)


# --- INT-A3: the DEBUG guard --------------------------------------------------------


@pytest.mark.parametrize(
    "debug",
    [
        pytest.param(True, id="bool"),
        pytest.param("True", id="the-env-files-spelling"),
        pytest.param("true", id="lowercase"),
        pytest.param("1", id="numeric"),
        pytest.param("yes", id="yes"),
        pytest.param("on", id="on"),
    ],
)
def test_debug_never_boots_production(debug: object) -> None:
    """THE BUG. DEBUG fed SQLAlchemy `echo`, so production logged every statement and its
    bound parameters — the `hashed_password` of every registered user and the wearable
    OAuth ciphertext of every connected athlete, in plaintext, to the application log.

    Parametrised over the spellings pydantic coerces to True rather than over the bool
    alone: the value arrives from the environment as a string, and a guard that only
    refused `True` would admit `DEBUG=yes` — a fresh instance of the exact
    accept-unless-recognised defect this module exists to remove.
    """
    from app import main

    cfg = _settings(ENVIRONMENT="production", DEBUG=debug)
    assert cfg.DEBUG is True, "fixture must actually produce a debug-on config"
    with pytest.raises(RuntimeError, match="DEBUG"):
        main._check_production_debug(cfg)


def test_no_debug_value_this_repo_ships_as_an_example_may_boot_production() -> None:
    """`.env.example` ships DEBUG=True, and the documented setup step is
    `cp .env.example .env`. Pinned to the FILE, like the SECRET_KEY test above, so an edit
    there cannot quietly reopen this.
    """
    from app import main

    example_debug = _env_example_values()["DEBUG"]
    cfg = _settings(ENVIRONMENT="production", DEBUG=example_debug)
    with pytest.raises(RuntimeError, match="DEBUG"):
        main._check_production_debug(cfg)


def test_debug_off_still_boots_production() -> None:
    """The positive fixture. A guard that refuses everything is not a guard."""
    from app import main

    cfg = _settings(ENVIRONMENT="production", DEBUG=False)
    main._check_production_debug(cfg)


def test_debug_warns_but_never_blocks_outside_production() -> None:
    """Fail fast in prod, warn elsewhere. Local dev keeps SQL echo if it asks for it."""
    from app import main

    cfg = _settings(ENVIRONMENT="development", DEBUG=True)
    with caplog_at("perflab", logging.WARNING) as records:
        main._check_production_debug(cfg)
    assert any("DEBUG" in r.getMessage() for r in records), (
        "dev boot must warn that debug logging exposes credentials, not stay silent"
    )


def test_the_shipped_debug_default_is_safe() -> None:
    """The default must be the safe value, and this test is the reason why.

    Every other test here presupposes `ENVIRONMENT=production` is set correctly — the
    guard cannot fire otherwise. But ENVIRONMENT is itself an env var that defaults to
    `development`, so a real deployment that omits *both* variables gets no refusal and no
    guard: just a warning, into a log now filling with password hashes. Two independent
    misconfigurations, one of which is doing nothing at all.

    Making False the default removes the second variable from the blast radius: omitting
    DEBUG is now safe on its own terms, whatever ENVIRONMENT says. The guard stops the
    operator who sets DEBUG=True in production; this stops the one who never set it.
    """
    assert Settings(_env_file=None).DEBUG is False  # type: ignore[arg-type]
