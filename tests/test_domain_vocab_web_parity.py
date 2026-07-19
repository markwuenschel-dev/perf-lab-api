"""Cross-language drift gate: the web domain picker must mirror the backend vocabulary.

``web/src/perflab/domains.ts`` hand-mirrors the canonical ``app.logic.domain_vocab.DOMAINS``
set and says so in its own header, but nothing bound the two. The domain codes are NOT a
closed enum in ``openapi.json`` (only ``TrainingGoal`` is), so the openapi -> types.gen.ts
gate cannot catch this. Result: adding a domain shipped a green PR while the picker silently
omitted it; removing one left a picker value that ``normalize_domain_at_boundary`` now 400s on.

This test binds them. It parses the ``DOMAIN_OPTIONS`` value literals out of the TS file and
asserts the set equals ``DOMAINS``. The ci.yml python filter also claims
``web/src/perflab/domains.ts`` so a mirror-only edit runs this gate too (AUD-C20).
"""

import re
from pathlib import Path

from app.logic.domain_vocab import DOMAINS

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DOMAINS_TS = _REPO_ROOT / "web" / "src" / "perflab" / "domains.ts"

# Matches the `value: "<canonical>"` field of each DomainOption entry. The label is
# intentionally ignored — only the canonical value crosses the contract boundary.
_VALUE_RE = re.compile(r"""\{\s*value:\s*["']([^"']+)["']""")


def _web_domain_values() -> set[str]:
    assert _DOMAINS_TS.is_file(), f"web domain mirror not found at {_DOMAINS_TS}"
    text = _DOMAINS_TS.read_text(encoding="utf-8")
    values = _VALUE_RE.findall(text)
    # Guard the guard: a regex that silently matched nothing would make the parity
    # assertion pass vacuously. The mirror is never legitimately empty.
    assert len(values) >= len(DOMAINS), (
        f"parsed only {len(values)} domain values from {_DOMAINS_TS.name} "
        f"(expected >= {len(DOMAINS)}) — the DOMAIN_OPTIONS format likely changed; "
        "update _VALUE_RE so this gate keeps biting."
    )
    return set(values)


def test_web_domain_picker_mirrors_canonical_domains() -> None:
    web_values = _web_domain_values()

    missing_in_web = DOMAINS - web_values
    extra_in_web = web_values - DOMAINS

    assert not missing_in_web and not extra_in_web, (
        "web/src/perflab/domains.ts has drifted from app.logic.domain_vocab.DOMAINS.\n"
        f"  In DOMAINS but absent from the picker (would silently omit): {sorted(missing_in_web)}\n"
        f"  In the picker but not canonical (would 400 at the boundary): {sorted(extra_in_web)}\n"
        "Update DOMAIN_OPTIONS to match, or update DOMAINS if the vocabulary really changed."
    )
