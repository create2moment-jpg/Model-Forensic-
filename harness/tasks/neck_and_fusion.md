# Task: neck and fusion

Find how the backbone outputs become the features the head reads.

1. The neck per modality (FPN / second-FPN / channel mapper …): which backbone levels it takes, output channels,
   scales (strides) it produces — match the `scale=` / `level=` dimensions in the brief's names.
2. Fusion: where modalities meet (early / feature-level / in the decoder's cross-attention / late), the module and
   its config keys; how a missing or dropped modality is handled (zeros, masks, learned token, modality dropout and
   its probability).
3. Temporal fusion: memory / queue of previous frames, ego-motion alignment, how many frames, what is detached.
4. Positional encodings per modality (3D position embedding, camera rays, radar point features) and where they are
   added.
5. Which logged series observe the neck / fusion (features per `module=` / `scale=`, attention statistics) and what a
   healthy vs a collapsed value looks like (one scale's features going to zero, one modality dominating).

Write `fact` entries with config keys and code refs, `caveat` entries for misleading series. Longer explanation:
`<inbox>/docs/<codebase>/neck_and_fusion.md`.
