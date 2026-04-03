"""Prescription `goal` query values — keep in sync with prescriber + web dropdown."""

from typing import Literal

TrainingGoal = Literal[
    "Strength",
    "Hypertrophy",
    "Power",
    "General",
    "OlympicLifts",
    "Powerlifting",
    "MetCon",
    "Calisthenics",
    "Gymnastics",
    "Grip",
    "Running",
    "Sprinting",
    "HalfMarathon",
    "FullMarathon",
]

TRAINING_GOAL_DEFAULT: TrainingGoal = "Strength"
