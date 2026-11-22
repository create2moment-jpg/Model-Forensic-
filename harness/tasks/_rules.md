## Output contract (mandatory)

Answer with **YAML only**, no prose around it:

```yaml
entries:
  - id: K-short-stable-id          # S-… for kind: setup
    kind: fact                      # constraint | fact | setup | caveat | code_ref
    statement: One sentence a reviewer can check.
    why: Why it matters for reading runs; what would change it.
    when:                           # omit what does not apply; all given conditions must hold
      setup: [1v5r]                 # labels defined by setup entries
      dataset_contains: [manual]    # substrings of dataset names
      stage_contains: [finetune]    # substrings of pipeline stage names
      config: {"*key.glob": "value-glob"}   # flattened config keys as in the brief
    effects:
      ignore_metrics: ["*velocity*"]   # metric globs (names as in the brief) to set aside
      ignore_classes: []
      caveat: Short text shown wherever this applies.
      setup: ""                        # only for kind: setup
    source:
      code_refs: ["path/to/file.py:123"]   # required: where in the code this is decided
      evidence: what you read
    confidence: high                # low | medium | high
```

Rules: cite `file:line` for every entry; never invent config keys or metric names (use the brief's or the code's
exact names); prefer few precise entries; if something cannot be determined from the code, leave it out.
