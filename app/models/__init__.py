"""
app/models/__init__.py

Import all ORM models here so:
  1. Alembic's env.py can discover them via `from app.models import *`
  2. SQLAlchemy's Base.metadata is fully populated before create_all / migrations run
"""

from app.models.user import User, AthleteProfile          # noqa: F401
from app.models.athlete_state import AthleteState         # noqa: F401
from app.models.exercise import Exercise                  # noqa: F401
from app.models.mesocycle import MesocycleBlock, PlannedSession  # noqa: F401
from app.models.weak_point import WeakPoint               # noqa: F401
from app.models.workout_log import WorkoutLog             # noqa: F401
from app.models.benchmark_definition import BenchmarkDefinition  # noqa: F401
from app.models.benchmark_observation import BenchmarkObservation  # noqa: F401
from app.models.derived_metric_definition import DerivedMetricDefinition  # noqa: F401
from app.models.derived_metric_snapshot import DerivedMetricSnapshot  # noqa: F401
from app.models.observation_mapping import ObservationMapping  # noqa: F401
