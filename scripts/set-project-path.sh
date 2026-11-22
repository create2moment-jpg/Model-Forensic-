#!/usr/bin/env bash
# Set PROJECT_PATH (your project's root folder) for bash and zsh, so relative paths given to Model Forensic
# (e.g. `mf inspect outputs/_azure_artefacts`) resolve from the project root wherever you run `mf`.
#
#   scripts/set-project-path.sh                 # default: the folder that contains this Model Forensic folder
#   scripts/set-project-path.sh /path/to/project
#
# Writes one marked block to ~/.bashrc and ~/.zshrc (created if missing); running it again replaces the block.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
target="${1:-$(dirname "$here")}"
if [ ! -d "$target" ]; then
  echo "not a folder: $target" >&2
  exit 2
fi
target="$(cd "$target" && pwd)"
begin="# >>> model-forensic PROJECT_PATH >>>"
end="# <<< model-forensic PROJECT_PATH <<<"
for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
  touch "$rc"
  tmp="$(mktemp)"
  awk -v b="$begin" -v e="$end" '$0==b{skip=1} !skip{print} $0==e{skip=0}' "$rc" > "$tmp"
  {
    cat "$tmp"
    echo "$begin"
    printf 'export PROJECT_PATH=%q\n' "$target"
    echo "$end"
  } > "$rc"
  rm -f "$tmp"
  echo "PROJECT_PATH=$target → $rc"
done
echo "Open a new terminal (or: source ~/.bashrc / source ~/.zshrc), then check with: mf paths"
