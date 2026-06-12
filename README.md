# 🧹 Disk Cleaner — the cleaner that *explains* before it deletes

A macOS disk-cleanup tool with one thing the others don't: **press `?` on any file and an AI
tells you what it is and whether it's safe to delete — before you decide.**

No more guessing whether `~/Library/Containers/com.apple.Safari/Data/Library/Caches` is safe
to nuke. Ask it.

```
Delete [1.9 GB] /Users/you/Library/Caches/com.apple.Safari ? (y/N/q/?) ?
    Your question: is it safe to delete?

  Assistant:
    This is Safari's browser cache — temporary web content (images, scripts) it
    stores to speed up repeat visits. Completely safe to delete; Safari recreates
    what it needs as you browse. Pages may load slightly slower once, no data loss.

Delete [1.9 GB] /Users/you/Library/Caches/com.apple.Safari ? (y/N/q/?) y
  ✓ Removed (1.9 GB freed)
```

---

## Why this exists

Every Mac cleaner shows you a wall of files and a "Clean" button. None of them tell you
**what you're about to delete** in plain English. This one does — and it **never deletes
anything without you typing `y`**.

- 🔍 Scans your home folder for genuinely-safe junk (caches, logs, trash, build artifacts,
  `.DS_Store`, stale downloads), ranked by size.
- 🤖 **`?` = ask the AI** about any file (path, size, category, why it was flagged + your
  question). Advisory only — it explains, you decide.
- 🔒 **Safe by default.** Suggestion-only unless you pass `--delete`. Per-file confirmation.
  Protected system paths are skipped.

## Install

**Homebrew** (recommended):
```bash
brew install prateek4506/tap/disk-cleaner
```

**One-liner**:
```bash
curl -fsSL https://raw.githubusercontent.com/prateek4506/disk-cleaner/main/install.sh | bash
```

**From source**:
```bash
git clone https://github.com/prateek4506/disk-cleaner
cd disk-cleaner && ./bin/disk-cleaner
```

## Usage

```bash
disk-cleaner                 # scan & suggest (no deletions)
disk-cleaner --delete        # interactively confirm each item
disk-cleaner --min-size 100  # only show items ≥ 100 MB
disk-cleaner --path ~/dev    # scan a specific folder
```

At each prompt: `y` delete · `N` skip (default) · `q` quit · **`?` ask the AI**.

## The AI advisor (optional)

The `?` feature uses [OpenCode](https://opencode.ai) to answer questions about a file.

- **It's optional.** Without `opencode` installed, `?` just says the advisor is unavailable —
  everything else works normally.
- **It's free.** Defaults to OpenCode's free Zen model (`opencode/deepseek-v4-flash-free`),
  so no paid quota is needed.
- **Pick your model** with `DISKCLEANER_AI_MODEL`:
  ```bash
  DISKCLEANER_AI_MODEL="opencode-go/deepseek-v4-flash" disk-cleaner --delete
  ```
- **It never acts.** The model only explains; the cleaner only deletes when you type `y`.

## Requirements

- macOS, Python 3 (ships with macOS)
- Optional: [`opencode`](https://opencode.ai) on `PATH` for the `?` advisor

## Safety

- Nothing is deleted without an explicit `y` per file.
- Default mode is suggestion-only.
- Known system/protected paths are never offered.
- The AI advisor is read-only — it cannot delete or modify anything.

## License

MIT
