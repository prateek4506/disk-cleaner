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

The `?` feature answers questions using a model on **your** machine. **It needs no API key
from you _or_ the app.** It **auto-discovers** whatever you already have and uses it,
preferring the private/local option:

1. **[Ollama](https://ollama.com) — preferred (local & private).** Uses **any model you've
   already pulled** — no specific model required. If Ollama is installed but you have *no*
   models yet, `?` offers a one-time download and shows the live progress; it never triggers
   a surprise download on its own.
   ```bash
   brew install ollama && ollama pull llama3.2   # one-time (any model works)
   ```

2. **[OpenCode](https://opencode.ai)** — uses your existing `opencode` login. Auto-picks an
   **available model, preferring a free one** (so it won't pick a paid model that may be
   quota-limited).

### Why run AI locally? (Ollama)

A disk cleaner pokes around your personal files, so where the AI runs matters:

- 🔒 **Private** — your file paths never leave your Mac. No cloud service ever sees what's
  on your disk.
- 💸 **Free & keyless** — no account, no API key, no credit card, no bill. Ever.
- ✈️ **Offline** — once the model is pulled, the `?` advisor answers locally on a plane or a
  locked-down network. (Only the one-time `brew install` / `ollama pull` needs internet.)
- ♾️ **No limits** — ask about as many files as you like; no quotas or throttling.
- 🧹 **Deletable** — the model is just files. Try it, and if it's not for you, reclaim the
  space in one command — no account to cancel, no leftovers:
  ```bash
  ollama rm llama3.2        # remove the model (~2 GB back)
  brew uninstall ollama     # remove the runtime too (~50 MB)
  ```

A local 3B model is less sharp than a frontier cloud model, but *"is this cache safe to
delete?"* is a narrow, well-understood question — it's more than good enough. And the
built-in heuristics work great with no AI at all.

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
