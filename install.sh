#!/usr/bin/env bash
# Disk Cleaner — curl|bash installer. Clones into ~/.disk-cleaner and symlinks the CLI.
set -euo pipefail

REPO="https://github.com/prateek4506/disk-cleaner"
DEST="${DISKCLEANER_HOME:-$HOME/.disk-cleaner}"
BIN_DIR="${DISKCLEANER_BIN:-/usr/local/bin}"

echo "🧹 Installing Disk Cleaner…"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is required (it ships with macOS). Aborting." >&2
  exit 1
fi

if [ -d "$DEST/.git" ]; then
  echo "  • Updating existing install at $DEST"
  git -C "$DEST" pull --quiet --ff-only
else
  echo "  • Cloning into $DEST"
  rm -rf "$DEST"
  git clone --quiet --depth 1 "$REPO" "$DEST"
fi

chmod +x "$DEST/bin/disk-cleaner"

# Link the CLI onto PATH. Fall back to ~/.local/bin if /usr/local/bin isn't writable.
if [ ! -w "$BIN_DIR" ]; then
  BIN_DIR="$HOME/.local/bin"
  mkdir -p "$BIN_DIR"
fi
ln -sf "$DEST/bin/disk-cleaner" "$BIN_DIR/disk-cleaner"

echo "✅ Installed: $BIN_DIR/disk-cleaner"
case ":$PATH:" in
  *":$BIN_DIR:"*) echo "   Run:  disk-cleaner" ;;
  *) echo "   Add to PATH:  export PATH=\"$BIN_DIR:\$PATH\"   then run: disk-cleaner" ;;
esac
echo "   Tip: press ? at any prompt to ask the AI about a file (needs 'opencode')."
