"""
app/models/__init__.py

Central place to import all models so Alembic and the app can discover them.
"""

# Re-export Base so `from app.models import Base` works everywhere
from app.core.db import Base  # noqa: F401
from app.models.athlete_state import AthleteState  # noqa: F401
from app.models.benchmark_definition import BenchmarkDefinition  # noqa: F401
from app.models.benchmark_observation import BenchmarkObservation  # noqa: F401
from app.models.capacity_floor_shadow import CapacityFloorShadowLog  # noqa: F401
from app.models.derived_metric_definition import DerivedMetricDefinition  # noqa: F401
from app.models.derived_metric_snapshot import DerivedMetricSnapshot  # noqa: F401
from app.models.dose_routing_shadow import DoseRoutingShadowLog  # noqa: F401
from app.models.ekf_shadow import EkfShadowLog  # noqa: F401
from app.models.exercise import Exercise  # noqa: F401
from app.models.experiment import ExperimentAssignment  # noqa: F401
from app.models.macrocycle import Macrocycle  # noqa: F401
from app.models.mesocycle import MesocycleBlock, PlannedSession  # noqa: F401
from app.models.mpc_shadow import MpcShadowLog  # noqa: F401
from app.models.objective import Objective  # noqa: F401
from app.models.observation_mapping import ObservationMapping  # noqa: F401
from app.models.personalization_shadow import PersonalizationShadowLog  # noqa: F401
from app.models.planning_override import PlanningOverride  # noqa: F401
from app.models.recovery_shadow import RecoveryShadowLog  # noqa: F401
from app.models.strength_decline_candidate import StrengthDeclineCandidate  # noqa: F401
from app.models.strength_decline_shadow import StrengthDeclineShadow  # noqa: F401
from app.models.telemetry import (  # noqa: F401
    CandidateDecisionLog,
    OutcomeEvent,
    PainReport,
    PrescriptionDecision,
    SessionFeedback,
)

# Import all model classes (this triggers registration with Base.metadata)
from app.models.user import AthleteProfile, User  # noqa: F401
from app.models.weak_point import WeakPoint  # noqa: F401
from app.models.wearable_connection import WearableConnection  # noqa: F401
from app.models.wellness import DailyCheckin, WellnessSample  # noqa: F401
from app.models.workout_log import WorkoutLog  # noqa: F401
from app.models.workout_set_log import WorkoutSetLog  # noqa: F401
