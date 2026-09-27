"""Which planned days each equipment kit must support, and what "honest" means (phase 9).

A planned day under a given kit must do one of two honest things:

* **Supported kit:** prescribe an in-domain session the kit can actually perform. That is
  either the planned template, or a stated in-domain replacement.
* **Unsupported kit:** say so. The day is either replaced by another in-domain session
  (``plan:session_replaced``) or explicitly unavailable (``plan:session_unavailable``).

Never generic filler. The invariant (ADR-0072): **equipment adaptation may change the
exercise implementation. It must not silently change the session's training intent or sport
identity.** "Air Squat / Push-up / Lunges" on a strength day, or a GPP template on a
weightlifting day, is filler, not adaptation.

This matrix is AUTHORED product judgement, not derived. It says where an honest same-domain
variant exists: a dumbbell front squat keeps a volume-strength day's intent, but no
bodyweight movement is a snatch.

``unconfigured`` keeps its existing permissive meaning. The athlete never set equipment, so
nothing is filtered, and it is never an "equipment unavailable" case.
"""

from __future__ import annotations

from typing import Final, Literal

#: How a planned day must turn out under a kit.
#: ``fulfil``: the planned template or a stated in-domain replacement, never filler.
#: ``replace``: a stated in-domain replacement is the expected honest outcome (↻).
#: ``unsupported``: stated replacement or explicit unavailability, never filler (✗).
Expect = Literal["fulfil", "replace", "unsupported"]

Kit = Literal["unconfigured", "full_gym", "home", "bodyweight"]
KITS: Final[tuple[Kit, ...]] = ("unconfigured", "full_gym", "home", "bodyweight")

#: The equipment each kit declares. ``None`` means unconfigured (no filter). ``full_gym`` is
#: every tag the catalog uses (resolved against the catalog at evaluation time).
#: ``bodyweight`` is the configured-bodyweight-only athlete (``["bodyweight"]``), which is
#: different from never configuring equipment.
HOME_EQUIPMENT: Final[tuple[str, ...]] = ("dumbbells", "kettlebell", "pullup_bar")
BODYWEIGHT_EQUIPMENT: Final[tuple[str, ...]] = ("bodyweight",)

_ALL: dict[Kit, Expect] = {
    "unconfigured": "fulfil", "full_gym": "fulfil", "home": "fulfil", "bodyweight": "fulfil",
}
_GYM_HOME: dict[Kit, Expect] = {**_ALL, "bodyweight": "unsupported"}
_GYM_ONLY: dict[Kit, Expect] = {**_ALL, "home": "unsupported", "bodyweight": "unsupported"}
_MAX_STRENGTH: dict[Kit, Expect] = {**_ALL, "home": "replace", "bodyweight": "unsupported"}

#: Binding slug (``planned_session_slots``) -> kit -> expected outcome.
KIT_SUPPORT: Final[dict[str, dict[Kit, Expect]]] = {
    # Strength: dumbbell/kettlebell variants keep volume and accessory intent. A max-strength
    # day at home is replaced in-domain, never imitated with light implements.
    "strength_max": _MAX_STRENGTH,
    "strength_volume": _GYM_HOME,
    "strength_accessory": _GYM_HOME,
    # Hypertrophy: effective across free weights and dumbbells with appropriate effort.
    "hypertrophy_upper": _GYM_HOME,
    "hypertrophy_lower": _GYM_HOME,
    "hypertrophy_accessory": _GYM_HOME,
    "hypertrophy_high_volume": _GYM_HOME,
    # Power: ballistic intent. Jumps need nothing; loaded power development needs load.
    "power_development": _GYM_HOME,
    "power_neural_priming": _ALL,
    "power_potentiation": _GYM_ONLY,
    # Sport-specific barbell and equipment days may say "unavailable" rather than morph.
    "powerlifting_sbd": _GYM_ONLY,
    "powerlifting_accessory": _GYM_ONLY,
    "weightlifting_technique": _GYM_ONLY,
    "hyrox_simulation": _GYM_ONLY,
    "hyrox_running_functional": _GYM_ONLY,
    "crossfit_strength_skill": _GYM_ONLY,
    "mixed_metcon": _GYM_ONLY,
    "mixed_modal": _GYM_ONLY,
    "mixed_engine": _GYM_ONLY,
    "mixed_strength_endurance": _GYM_ONLY,
    # Grip: hangs and dumbbell/kettlebell carries are honest grip work at home (observed:
    # grip_main realizes every slot with dumbbells, kettlebell and a pull-up bar).
    "grip_support": _GYM_HOME,
    # Running needs no equipment.
    "running_base": _ALL,
    "running_threshold": _ALL,
    "running_speed": _ALL,
    "running_recovery": _ALL,
    # Bodyweight disciplines and general preparation.
    "calisthenics_skill": _ALL,
    "calisthenics_strength": _ALL,
    "calisthenics_conditioning": _ALL,
    "gymnastics_skill": _ALL,
    "general_gpp": _ALL,
    "general_recovery": _ALL,
    "general_aerobic_strength": _ALL,
    "general_strength_preservation": _ALL,
    "general_conditioning": _ALL,
    "conditioning_metcon": _ALL,
}

#: Planned domains whose days are served from another canonical pool. ``conditioning`` is a
#: secondary style with no pool of its own; it draws the general pool (candidate_library).
POOL_DOMAIN_ALIASES: Final[dict[str, str]] = {"conditioning": "general"}
