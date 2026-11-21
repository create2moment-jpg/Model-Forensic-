# ADR-0005: Dimensions are part of the metric key

Status: accepted

## Context
Forensics questions are usually about slices: range, class, modality, occlusion, layer. Hard-coding
per-slice logic per metric does not scale and misses hidden regressions.

## Decision
`MetricKey = (name, split, dims)` with a text form `split/name[k=v,...]`. Analyses localize
regressions generically over any dimension. Connectors map backend naming conventions into this form.

## Consequences
+ Slice localization and "hidden regression under a flat aggregate" work for any metric and any dimension.
− Teams must log (or map) slice metrics in this convention; a name-mapping layer is planned.
