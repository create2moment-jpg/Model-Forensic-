# Task: setup profiles

Find how the codebase distinguishes its **sensor setups** (number of cameras, radars, lidars — e.g. one camera and
five radars, five cameras and five radars, five cameras and one lidar) and **label sources** (ground-truth / manual
labels vs online / auto labels).

1. Find the config keys whose values select the setup (sensor lists or counts, model variants) and the label source
   (dataset names, label types).
2. For each setup, write one `kind: setup` entry whose `when.config` matches the flattened config keys exactly as they
   appear in the brief, and whose `effects.setup` is a short label (`1v5r`, `5v5r`, `5v1L`, `gt`, `online`, …).
3. Add `fact` entries for anything that differs per setup and matters for reading results (e.g. which heads exist).

Use the config keys and values listed in the brief; they are what Model Forensic matches against.
