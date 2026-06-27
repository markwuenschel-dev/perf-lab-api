// Goal-specific training load and baseline anchor definitions.
//
// For each TrainingGoal this module defines:
//   - what "training load" means for that goal
//   - primary capacity anchor (what can the athlete currently do?)
//   - load-tolerance anchor (how much stress can they recover from?)
//   - risk/tissue anchor (what limits safe progression?)
//   - best retest metric (what to measure periodically to recalibrate)
//
// A single capacity test is never sufficient for most goals.  Performance Lab
// distinguishes capacity, recoverable-load tolerance, tissue/risk tolerance,
// and retest metrics because each feeds a different part of the control loop.

export type TrainingGoal =
  | "General"
  | "Strength"
  | "Hypertrophy"
  | "Power"
  | "Powerlifting"
  | "OlympicLifts"
  | "Calisthenics"
  | "Gymnastics"
  | "Grip"
  | "MetCon"
  | "Running"
  | "Sprinting"
  | "HalfMarathon"
  | "FullMarathon";

export type GoalLoadDefinition = {
  goal: TrainingGoal;
  goalSpecificTrainingLoad: string;
  primaryCapacityAnchor: string;
  loadToleranceAnchor: string;
  riskOrTissueAnchor: string;
  bestRetestMetric: string;
};

const DEFINITIONS: GoalLoadDefinition[] = [
  {
    goal: "General",
    goalSpecificTrainingLoad:
      "Balanced weekly recoverable stress across strength, aerobic work, mobility, " +
      "conditioning, and basic movement quality. Emphasize minimum effective dose, " +
      "consistency, and broad resilience rather than maximizing one performance quality.",
    primaryCapacityAnchor:
      "Basic fitness profile: resting HR, bodyweight or waist, max push-ups or " +
      "pull-ups, 5K or 12-minute run, and simple mobility screen.",
    loadToleranceAnchor:
      "Current weekly activity level, sessions per week, average session duration, " +
      "recent consistency, and ability to recover from mixed training.",
    riskOrTissueAnchor:
      "Injury history, current pain flags, movement restrictions, low-back/knee/" +
      "shoulder tolerance, and general soreness recovery.",
    bestRetestMetric:
      "Repeatable mixed fitness check: 12-minute run or 5K, push-up/pull-up test, " +
      "loaded carry, and mobility screen.",
  },
  {
    goal: "Strength",
    goalSpecificTrainingLoad:
      "Heavy force exposure: hard sets at moderate-to-high intensity, proximity to " +
      "failure, inter-set recovery, neural fatigue, joint/tendon stress, and " +
      "movement-specific strength practice.",
    primaryCapacityAnchor:
      "Estimated or tested 1RM in the main movement patterns: squat, hinge, press, " +
      "and pull. 1RM anchors force capacity but does not reveal recoverable load.",
    loadToleranceAnchor:
      "Recent working sets at RPE 7–9, weekly hard-set tolerance, training age, " +
      "frequency tolerance, and ability to repeat heavy exposures without " +
      "performance collapse.",
    riskOrTissueAnchor:
      "Pain-sensitive joints, tendon irritation, lumbar/hip/knee/shoulder tolerance, " +
      "technical breakdown under load, and bar-speed or rep-quality decay.",
    bestRetestMetric:
      "Estimated 1RM from submax sets, top set plus backoff performance, bar-speed " +
      "trend, or formal 1RM test when appropriate.",
  },
  {
    goal: "Hypertrophy",
    goalSpecificTrainingLoad:
      "Local muscle-specific effective volume: hard sets per muscle, proximity to " +
      "failure, ROM quality, exercise stability, novelty, soreness cost, and local " +
      "muscular fatigue. Prefer hard sets near failure over raw volume load.",
    primaryCapacityAnchor:
      "Bodyweight, measurements/photos, and baseline working loads or rep strength " +
      "for major hypertrophy movements.",
    loadToleranceAnchor:
      "Muscle-group-specific weekly set tolerance, soreness recovery time, session " +
      "frequency tolerance, and ability to progress volume without joint pain.",
    riskOrTissueAnchor:
      "Joint irritation by exercise, tendon tolerance, ROM limitations, excessive " +
      "soreness, and connective-tissue response to high local volume.",
    bestRetestMetric:
      "Circumference/photo trend, rep PRs in stable hypertrophy movements, " +
      "bodyweight trend, and muscle-specific volume tolerance.",
  },
  {
    goal: "Power",
    goalSpecificTrainingLoad:
      "High-velocity, high-quality explosive exposure with low fatigue contamination: " +
      "jumps, throws, Olympic derivatives, sprints, acceleration work, and neural " +
      "readiness. Power load is capped by quality decay, not total volume.",
    primaryCapacityAnchor:
      "Vertical jump, broad jump, med-ball throw, sprint split, or bar-speed/peak-power test.",
    loadToleranceAnchor:
      "Number of high-quality explosive reps tolerated before output drops, " +
      "repeatability across sets, and ability to recover CNS freshness.",
    riskOrTissueAnchor:
      "Tendon/hamstring load, landing quality, jump-height loss, sprint-time decay, " +
      "bar-speed loss, and current fatigue state at testing.",
    bestRetestMetric:
      "Vertical jump, broad jump, flying sprint, med-ball throw, or bar-speed test " +
      "under standardized freshness.",
  },
  {
    goal: "Powerlifting",
    goalSpecificTrainingLoad:
      "Competition-specific squat/bench/deadlift stress: intensity, volume, frequency, " +
      "peaking fatigue, lift-specific technical practice, and neural/tissue cost. SBD " +
      "total anchors capacity but does not reveal recoverable load.",
    primaryCapacityAnchor:
      "Squat + bench + deadlift total, preferably from tested or estimated 1RMs.",
    loadToleranceAnchor:
      "Lift-specific frequency tolerance, recent hard sets, weekly tonnage/hard-set " +
      "tolerance, and recovery between exposures.",
    riskOrTissueAnchor:
      "Technical breakdown point, sticking-point pattern, lumbar/hip/knee/shoulder/" +
      "elbow stress, bar-speed decay, and pain flags.",
    bestRetestMetric:
      "Estimated 1RM for SBD, projected total, top-set performance, rep PRs, and " +
      "technical consistency at heavy submax loads.",
  },
  {
    goal: "OlympicLifts",
    goalSpecificTrainingLoad:
      "Skill-heavy explosive barbell exposure: technical reps, snatch/C&J intensity, " +
      "classical-lift exposure, pulls/squats, receiving positions, overhead/catch stress, " +
      "and CNS fatigue.",
    primaryCapacityAnchor:
      "Snatch 1RM, clean & jerk 1RM, and snatch-to-C&J ratio. A low ratio may " +
      "reflect technical limitation, mobility, or receiving instability — not just " +
      "pulling deficit.",
    loadToleranceAnchor:
      "Technical consistency at submaximal loads, weekly classical-lift exposure, " +
      "pull/squat support tolerance, and readiness to perform crisp reps.",
    riskOrTissueAnchor:
      "Overhead stability, wrist/elbow/shoulder stress, catch-position tolerance, " +
      "mobility limitations, fear or instability in receiving positions, and CNS fatigue.",
    bestRetestMetric:
      "Snatch, clean & jerk, technical complex at fixed percentage, bar-path " +
      "consistency, and snatch/C&J ratio.",
  },
  {
    goal: "Calisthenics",
    goalSpecificTrainingLoad:
      "Relative-strength and leverage-based bodyweight stress: reps, added load, " +
      "leverage difficulty, ROM standard, skill progression, and connective-tissue cost.",
    primaryCapacityAnchor:
      "Max strict pull-ups, dips/push-ups, current hardest controlled progression, " +
      "and added-load capacity where relevant.",
    loadToleranceAnchor:
      "Weekly pulling/pushing/straight-arm volume tolerated, progression frequency, " +
      "recovery between high-tension bodyweight sessions, and total support/hang volume.",
    riskOrTissueAnchor:
      "Leverage level, ROM standard, elbow/wrist/shoulder tolerance, tendon irritation, " +
      "and connective-tissue adaptation lag. Tissue tolerance is separate from muscle capacity.",
    bestRetestMetric:
      "Max strict reps, weighted pull-up/dip estimate, progression quality, controlled " +
      "hold time, or standardized skill progression test.",
  },
  {
    goal: "Gymnastics",
    goalSpecificTrainingLoad:
      "Skill-density and joint-tolerance load: holds, supports, handstands, levers, ring " +
      "work, landings, straight-arm strength, and connective-tissue stress. Tissue " +
      "tolerance may be the limiting factor more often than metabolic fatigue.",
    primaryCapacityAnchor:
      "Skill inventory plus strict support/hold capacity: handstand hold, L-sit, ring " +
      "support, hollow/arch quality, lever progression.",
    loadToleranceAnchor:
      "Support volume, landing contacts, weekly straight-arm exposure, skill-practice " +
      "density, and tolerance to frequent low-level practice.",
    riskOrTissueAnchor:
      "Wrist extension stress, elbow straight-arm stress, shoulder end-range stress, " +
      "finger tendon stress, landing stress, and support-volume tissue cost.",
    bestRetestMetric:
      "Handstand hold/quality, L-sit duration, ring support duration, lever progression, " +
      "landing quality, and controlled skill standard.",
  },
  {
    goal: "Grip",
    goalSpecificTrainingLoad:
      "Direct hand/forearm/finger stress: hangs, carries, crush work, pinch work, support " +
      "holds, finger flexor work, wrist/forearm work, and tendon recovery cost. Support, " +
      "crush, pinch, finger flexor, wrist, and forearm work are not interchangeable.",
    primaryCapacityAnchor:
      "Max dead hang time, farmer carry load/time, pinch hold, or dynamometer grip strength.",
    loadToleranceAnchor:
      "Weekly grip exposure tolerated by modality, recovery between grip sessions, " +
      "carry/hang volume, and interference with pulling or barbell work.",
    riskOrTissueAnchor:
      "Finger flexor tendon stress, elbow irritation, wrist/forearm strain, skin " +
      "tolerance, and modality-specific overload risk.",
    bestRetestMetric:
      "Dead hang, farmer carry, pinch test, dynamometer, or sport-specific grip benchmark.",
  },
  {
    goal: "MetCon",
    goalSpecificTrainingLoad:
      "Mixed-modal density: work rate, heart-rate strain, lactate/metabolic fatigue, " +
      "movement interference, local muscular endurance, eccentric damage, skill " +
      "bottlenecks, and recovery cost. A single benchmark score can hide the limiting " +
      "system — decompose by metabolic density, movement interference, eccentric damage, " +
      "skill bottleneck, and heat/cardiorespiratory strain.",
    primaryCapacityAnchor:
      "Repeatable 10–20 minute mixed-modal benchmark, assault bike/row test, or " +
      "standardized circuit score.",
    loadToleranceAnchor:
      "Current weekly conditioning volume, ability to repeat dense sessions, recovery " +
      "after high-lactate work, and tolerance to mixed movement stress.",
    riskOrTissueAnchor:
      "Movement bottleneck, eccentric damage, heat/cardiorespiratory strain, local " +
      "muscular failure points, skill limitation, and tissue cost from specific movements.",
    bestRetestMetric:
      "Repeatable benchmark WOD/circuit, row/bike time trial, or mixed-modal " +
      "work-capacity test.",
  },
  {
    goal: "Running",
    goalSpecificTrainingLoad:
      "Distance × intensity distribution × pace/HR strain × impact/tissue stress × " +
      "accumulated aerobic/metabolic fatigue. 5K alone is not enough — distinguish " +
      "performance capacity from durability capacity.",
    primaryCapacityAnchor:
      "5K time, threshold pace, easy pace at known HR/RPE, or aerobic-drift test.",
    loadToleranceAnchor:
      "Current weekly mileage, longest recent run, sessions per week, recent consistency, " +
      "and ability to absorb impact volume.",
    riskOrTissueAnchor:
      "Prior injury, current pain, impact tolerance, knee/ankle/hip tissue load trend, " +
      "footwear/surface sensitivity, and HR drift under easy running.",
    bestRetestMetric:
      "5K/10K time trial, aerobic drift test, threshold pace test, or standardized " +
      "easy-run HR/RPE test.",
  },
  {
    goal: "Sprinting",
    goalSpecificTrainingLoad:
      "Acceleration and max-velocity exposure: sprint meters at high speed, full-rest " +
      "quality reps, starts, flying sprints, plyometric support, CNS load, and " +
      "hamstring/tendon stress.",
    primaryCapacityAnchor: "10m, 30m, and/or flying 10–20m sprint time.",
    loadToleranceAnchor:
      "High-speed meters tolerated per week, number of quality reps before speed drops, " +
      "recovery between exposures, and ability to maintain mechanics.",
    riskOrTissueAnchor:
      "Hamstring history, Achilles/calf/tendon load, sprint-time decay, technical " +
      "breakdown, surface/spike exposure, and CNS fatigue.",
    bestRetestMetric:
      "10m split, 30m sprint, flying 10/20m, or repeat sprint quality test.",
  },
  {
    goal: "HalfMarathon",
    goalSpecificTrainingLoad:
      "Weekly mileage, long-run load, threshold work, aerobic durability, pace/HR drift, " +
      "fueling basics, and impact tolerance. Durability often matters more than peak " +
      "test performance.",
    primaryCapacityAnchor:
      "Recent 5K/10K time, threshold pace, easy pace at known HR/RPE, or recent " +
      "half-marathon result.",
    loadToleranceAnchor:
      "Current weekly mileage, longest recent run, recent consistency, long-run " +
      "durability, and ability to recover from threshold work.",
    riskOrTissueAnchor:
      "Injury history, cardiac drift/pace decay, knee/ankle/hip tissue stress, fueling " +
      "tolerance for longer efforts, and accumulated impact load.",
    bestRetestMetric:
      "10K time trial, threshold pace test, aerobic drift test, long-run pace/HR " +
      "durability check, or half-marathon race/test.",
  },
  {
    goal: "FullMarathon",
    goalSpecificTrainingLoad:
      "Chronic mileage tolerance, long-run durability, fueling tolerance, aerobic " +
      "efficiency, pace discipline, musculoskeletal durability, and cumulative structural " +
      "load. Durability capacity may matter more than raw performance capacity.",
    primaryCapacityAnchor:
      "Recent half-marathon or 10K time, marathon history, easy pace at HR/RPE, and " +
      "threshold estimate.",
    loadToleranceAnchor:
      "Current weekly mileage, longest recent run, weeks of consistent running, long-run " +
      "recovery, and weekly mileage consistency.",
    riskOrTissueAnchor:
      "Fueling tolerance, injury history, pace/HR drift, musculoskeletal durability, " +
      "accumulated tissue load, and soreness recovery after long runs.",
    bestRetestMetric:
      "Half-marathon, 10K, marathon-pace long run, aerobic drift test, fueling " +
      "rehearsal, or long-run durability check.",
  },
];

export const GOAL_LOAD_DEFINITIONS: Record<string, GoalLoadDefinition> =
  Object.fromEntries(DEFINITIONS.map((d) => [d.goal, d]));

export function getGoalLoadDefinition(goal: string): GoalLoadDefinition | undefined {
  return GOAL_LOAD_DEFINITIONS[goal];
}
