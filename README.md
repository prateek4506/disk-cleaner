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

The `?` feature answers questions about a file using a model on **your** machine. **It needs
no API key from you _or_ the app** — it uses whichever of these you have, in this order:

1. **[Ollama](https://ollama.com) — recommended.** Fully local, private, no account, no key.
   ```bash
   # one-time setup:
   brew install ollama && ollama serve   # (or the Ollama app)
   ollama pull llama3.2
   ```
   The advisor only uses Ollama if the model is already pulled, so it never triggers a
   surprise multi-GB download mid-prompt.

2. **[OpenCode](https://opencode.ai)** — uses your existing `opencode` login
   (`opencode auth login`). Free Zen model by default.

If **neither** is installed, `?` tells you how to add one and the normal `y/N` flow continues —
the advisor can never break the cleaner.

**Configure** (all optional):
```bash
DISKCLEANER_AI_PROVIDER=ollama      # force one provider (ollama|opencode); default tries both
DISKCLEANER_OLLAMA_MODEL=llama3.2   # which Ollama model
DISKCLEANER_OPENCODE_MODEL=opencode/deepseek-v4-flash-free
```

**It never acts.** The model only explains; the cleaner only deletes when you type `y`.

> Note: there is **no shared/embedded API key** — the AI runs entirely on your own machine
> via your own local provider. Nothing about your files is sent anywhere unless *you* have
> configured a provider that does so.

## Requirements

- macOS, Python 3 (ships with macOS)
- Optional, for the `?` advisor: **[Ollama](https://ollama.com)** (local, keyless) *or*
  **[`opencode`](https://opencode.ai)**. Without either, everything except `?` still works.

## Safety

- Nothing is deleted without an explicit `y` per file.
- Default mode is suggestion-only.
- Known system/protected paths are never offered.
- The AI advisor is read-only — it cannot delete or modify anything.

## License

MIT
