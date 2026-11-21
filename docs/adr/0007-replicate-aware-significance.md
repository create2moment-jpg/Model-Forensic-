# ADR-0007: Replicate-aware significance for comparisons

Status: accepted

## Context
Most comparisons are single-seed; treating every delta above a threshold as a regression produces
noise-chasing.

## Decision
Runs sharing `tags.group` are replicates. With ≥2 baseline replicates, a delta is meaningful only if
|z| ≥ 2 against the replicate standard deviation AND the relative change exceeds half the metric's
practical threshold. Otherwise fixed per-metric thresholds apply and findings carry an explicit
limitation and lower confidence.

## Consequences
+ Findings state their significance basis; reviewers can tell evidence from noise.
− Requires teams to tag seed groups; the UI will offer "mark as replicates".
