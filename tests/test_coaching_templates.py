"""Structured coaching template JSON loads and registry mapping."""

from app.logic.coaching_template_registry import (
    get_structured_template_by_id,
    load_structured_templates,
)
from app.schemas.coaching_template import StructuredCoachingTemplate


def test_bundled_loads_five_templates():
    tpls = load_structured_templates()
    assert len(tpls) == 5
    ids = {t.template_id for t in tpls}
    assert ids == {
        "tmpl_olift_pendlay_style_v1",
        "tmpl_run_hinshaw_style_v1",
        "tmpl_pl_531_style_v1",
        "tmpl_pl_juggernaut_style_v1",
        "tmpl_gymnastics_progression_v1",
    }


def test_get_by_id():
    t = get_structured_template_by_id("tmpl_pl_531_style_v1")
    assert t is not None
    assert isinstance(t, StructuredCoachingTemplate)
    assert t.domain == "powerlifting"
