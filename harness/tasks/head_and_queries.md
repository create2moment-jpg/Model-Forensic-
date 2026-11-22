# Task: head, decoder layers and queries

Find how the detection head turns features into boxes, so the per-layer and per-query series can be read.

1. Queries: how many, how they are initialised (learned embeddings, anchors / reference points, from a proposal
   network or from the previous frame), how many are **propagated** from the previous frame (top-k by score,
   memory length), and how propagated queries are motion-compensated.
2. Decoder: number of layers, per layer the order of self-attention, cross-attention (to which features: image
   tokens, radar / lidar tokens, memory) and FFN; whether reference points are refined layer by layer; whether
   weights are shared across layers.
3. Outputs per layer: does every layer predict boxes (auxiliary predictions), which layer's output is used at
   inference, how the `layer=` dimension in the logged names maps to the decoder layers (0 = first or last?).
4. Denoising queries (noised ground truth added in training): how many groups, noise scale, attention mask, and the
   logged names of their losses.
5. What per-query information exists in the code and could be logged or is logged (score, matched / unmatched,
   matched class, propagated vs new, query age, reference point position, distance to ego), so it can be compared
   between runs.

Write `fact` entries (with code refs), `caveat` entries (e.g. "layer index 0 is the last layer"; "denoising losses
are training-only"). Longer explanation with the decoder diagram: `<inbox>/docs/<codebase>/head_and_queries.md`.
