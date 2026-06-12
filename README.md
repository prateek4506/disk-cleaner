# Disk Cleaner

A small macOS disk-cleanup tool. It scans your home folder for safe-to-remove items
(caches, trash, stale temp files), then — in interactive delete mode — asks you about each
one before removing anything.

## Run

```bash
# Suggestion-only (no deletion):
python3 DiskCleaner.app/Contents/Resources/disk_cleaner.py

# Interactive deletion (confirm each item):
python3 DiskCleaner.app/Contents/Resources/disk_cleaner.py --delete
```

Or launch the app bundle (`DiskCleaner.app`), which opens a Terminal window and runs the
script in `--delete` mode.

## Per-file prompt

For each candidate you're asked:

```
Delete [1.9 GB] /Users/you/Library/Caches/com.apple.Safari ? (y/N/q/?)
```

- `y` — delete it
- `N` — skip (default)
- `q` — quit
- `?` — **ask the assistant about this file**, then decide

## AI advisor (`?`)

Pressing `?` lets you ask a plain-English question about the file ("what is this?",
"is it safe to delete?") before deciding. The tool sends the file's path, size, category,
and why it was flagged — plus your question — to a cheap local model via
[OpenCode](https://opencode.ai) and prints a concise answer, then re-asks `y/N` for the
same file.

- **Advisory only.** The assistant never deletes anything; you still confirm `y/N`.
- **Fails safe.** If OpenCode isn't installed, times out, or returns nothing, the tool
  says so and the normal `y/N` flow continues — the advisor can never break the cleaner.
- **Model.** Defaults to `opencode/deepseek-v4-flash-free` (OpenCode Zen free tier).
  Override with the `DISKCLEANER_AI_MODEL` environment variable:

  ```bash
  DISKCLEANER_AI_MODEL="opencode-go/deepseek-v4-flash" \
    python3 DiskCleaner.app/Contents/Resources/disk_cleaner.py --delete
  ```

## Requirements

- Python 3
- (Optional) [`opencode`](https://opencode.ai) on `PATH` for the `?` advisor. Without it,
  `?` reports that the advisor is unavailable and everything else works normally.
