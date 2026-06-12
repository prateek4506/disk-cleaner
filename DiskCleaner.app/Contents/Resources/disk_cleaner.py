#!/usr/bin/env python3
"""
disk_cleaner.py — Suggest and (with per-file permission) delete reclaimable junk on macOS.

Scans your home directory for well-known, generally-safe junk categories
(caches, logs, trash, build artifacts, old downloads, .DS_Store), ranks the
findings by size, and asks for explicit confirmation before deleting EACH item.

Nothing is ever deleted without you typing 'y'. By default it only suggests.

Usage:
    python3 disk_cleaner.py            # scan & suggest only (no deletes)
    python3 disk_cleaner.py --delete   # interactively confirm each deletion
    python3 disk_cleaner.py --min-size 50  # only show items >= 50 MB
    python3 disk_cleaner.py --path ~/some/dir   # scan a different root
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

HOME = Path.home()

# Optional AI advisor: ask a model about a file before deciding to delete it.
#
# The advisor is ADVISORY ONLY — it never deletes anything; the user still answers y/N.
# It needs NO API key of its own. It AUTO-DISCOVERS whatever the user already has and uses it,
# preferring the private/local option:
#   1. Ollama   — uses ANY model you've already pulled (local, no account/key). Preferred.
#   2. OpenCode — uses your existing `opencode` login; auto-picks an available model,
#                 preferring a free one (so it avoids quota-capped paid models).
# If neither is available, `?` says so and the normal y/N flow continues.
#
# You can still pin choices with env vars (override auto-discovery):
#   DISKCLEANER_AI_PROVIDER=ollama|opencode   force a single provider
#   DISKCLEANER_OLLAMA_MODEL=<name>           force a specific Ollama model
#   DISKCLEANER_OPENCODE_MODEL=<provider/id>  force a specific OpenCode model
FORCED_PROVIDER = os.environ.get("DISKCLEANER_AI_PROVIDER", "").strip().lower()
ENV_OLLAMA_MODEL = os.environ.get("DISKCLEANER_OLLAMA_MODEL", "").strip()
ENV_OPENCODE_MODEL = os.environ.get(
    "DISKCLEANER_OPENCODE_MODEL", os.environ.get("DISKCLEANER_AI_MODEL", "")).strip()
# Model offered for download if Ollama is installed but has no models at all.
OLLAMA_DEFAULT_PULL = os.environ.get("DISKCLEANER_OLLAMA_MODEL", "llama3.2")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _ollama_models() -> list:
    """Names of models the user has already pulled (empty list if none / ollama absent)."""
    if not _have("ollama"):
        return []
    try:
        out = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return []
    models = []
    for line in out.splitlines()[1:]:           # skip the "NAME  ID  …" header
        name = line.split()[0].strip() if line.split() else ""
        if name:
            models.append(name)
    return models


def _pick_ollama_model() -> str:
    """Choose an Ollama model: env override if pulled, else any already-pulled model, else ''."""
    pulled = _ollama_models()
    if ENV_OLLAMA_MODEL:
        base = ENV_OLLAMA_MODEL.split(":")[0]
        if any(m == ENV_OLLAMA_MODEL or m.split(":")[0] == base for m in pulled):
            return ENV_OLLAMA_MODEL
    return pulled[0] if pulled else ""


def _opencode_models() -> list:
    """Available OpenCode model ids (provider/model), or [] if opencode absent."""
    if not _have("opencode"):
        return []
    try:
        out = subprocess.run(["opencode", "models"], capture_output=True, text=True, timeout=8).stdout
    except Exception:
        return []
    return [ln.strip() for ln in out.splitlines() if "/" in ln and not ln.startswith(" ")]


def _pick_opencode_model() -> str:
    """Choose an OpenCode model: env override → a free model → first available.

    Prefer a 'free' model so we don't pick a paid one that may be quota-capped.
    """
    models = _opencode_models()
    if ENV_OPENCODE_MODEL:
        return ENV_OPENCODE_MODEL          # user knows best; honor verbatim
    if not models:
        return ""
    free = [m for m in models if "free" in m.lower()]
    return free[0] if free else models[0]


def _clean_model_output(raw: str) -> str:
    """Strip TUI banner + ANSI/control noise, leaving just the model's answer."""
    text = raw.replace("\r", "")
    text = _ANSI_RE.sub("", text)                       # CSI sequences: ESC [ … letter
    text = re.sub(r"\x1b\[\?[0-9;]*[a-zA-Z]", "", text)  # private-mode: ESC [ ? … (cursor show/hide, 2026)
    text = re.sub(r"\x1b[=>]", "", text)                # other ESC sequences
    text = text.translate({0x04: None, 0x08: None, 0x07: None, 0x1b: None})
    text = re.sub(r"\^[A-Z@\[\]\\^_]", "", text)        # caret control reps ("^D", "^H")
    text = re.sub(r"[⠀-⣿]", "", text)         # braille spinner glyphs (⠙⠹⠸…)
    lines, seen = [], None
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("> build") or s.startswith(">> "):
            continue
        if s == seen:        # collapse consecutive duplicate lines (TTY redraw artifact)
            continue
        seen = s
        lines.append(s)
    return "\n".join(lines).strip()


def _ask_ollama(prompt: str, model: str) -> str:
    """Local, keyless. Returns the answer text, or '' on failure."""
    proc = subprocess.run(
        ["ollama", "run", model, prompt],
        capture_output=True, text=True, timeout=120, errors="replace",
    )
    return _clean_model_output((proc.stdout or "") + (proc.stderr or ""))


def _ask_opencode(prompt: str, model: str) -> str:
    """Uses the user's existing opencode login. Returns the answer text, or '' on failure."""
    # `script` supplies the pseudo-TTY opencode's `run` needs; -q quiet, no typescript file.
    proc = subprocess.run(
        ["script", "-q", "/dev/null", "opencode", "run", "-m", model, prompt],
        capture_output=True, text=True, timeout=120, errors="replace",
    )
    return _clean_model_output((proc.stdout or "") + (proc.stderr or ""))


def _pull_ollama_model(model: str) -> bool:
    """Stream `ollama pull <model>` so the user sees live download progress. Returns True on success.

    We do NOT capture output here — letting ollama write straight to the terminal shows its
    real progress bars (percent, MB/s, ETA). Ctrl-C cancels the pull, not the whole app.
    """
    print(f"\n  Downloading Ollama model '{model}' (one-time, a few GB)…")
    print("  ── live progress from ollama ──")
    try:
        rc = subprocess.call(["ollama", "pull", model])  # inherits stdout/stderr → live bars
    except KeyboardInterrupt:
        print("\n  Download cancelled.\n")
        return False
    except Exception as e:
        print(f"  Could not start download: {e}\n")
        return False
    print("  ───────────────────────────────")
    if rc == 0:
        print("  ✓ Model ready.\n")
        return True
    print(f"  Download failed (ollama exit {rc}).\n")
    return False


def ask_about_file(c, question: str) -> None:
    """Ask an available advisor about candidate `c`; print the answer. Never deletes."""
    prompt = (
        "You are advising a user whether a file/folder is safe to delete from their Mac. "
        "Be concise (2-4 sentences), factual, and cautious — if unsure, say so.\n\n"
        f"Path: {c.path}\nSize: {human(c.size)}\nCategory: {c.category}\n"
        f"Why it was flagged: {c.reason}\n\n"
        f"User's question: {question}\n\n"
        "Answer in plain text. Do not take any action; only explain."
    )

    # Auto-discover the model each provider will use (env override > what the user already has).
    ollama_model = _pick_ollama_model()
    opencode_model = _pick_opencode_model()

    # Ollama is installed but has NO models pulled → offer the one-time download (live progress),
    # unless the user forced opencode. This is the keyless, private path.
    if (not ollama_model and _have("ollama") and FORCED_PROVIDER != "opencode"):
        try:
            resp = input(
                f"  Ollama is installed but no model is downloaded yet.\n"
                f"  Download '{OLLAMA_DEFAULT_PULL}' now for free, local, private answers? [y/N] "
            ).strip().lower()
        except EOFError:
            resp = "n"
        if resp == "y" and _pull_ollama_model(OLLAMA_DEFAULT_PULL):
            ollama_model = OLLAMA_DEFAULT_PULL

    # Provider chain: (label, chosen-model, runner). Ollama (local/private) preferred.
    providers = [
        ("Ollama (local)", ollama_model, _ask_ollama),
        ("OpenCode", opencode_model, _ask_opencode),
    ]
    if FORCED_PROVIDER in ("ollama", "opencode"):
        providers = [p for p in providers if p[0].lower().startswith(FORCED_PROVIDER)]

    available = [p for p in providers if p[1]]   # only providers with a usable model
    if not available:
        print("  (AI advisor unavailable. Add one — both are free and need no API key for this:)")
        print("    • Ollama (local, private):  https://ollama.com   then:  ollama pull llama3.2")
        print("    • OpenCode:                 https://opencode.ai   then:  opencode auth login\n")
        return

    print("  … asking the assistant (this can take a few seconds)")
    for name, model, runner in available:
        try:
            answer = runner(prompt, model)
        except subprocess.TimeoutExpired:
            print(f"  ({name} timed out — trying the next provider if any.)")
            continue
        except Exception:  # never let the advisor break the cleaner
            continue
        if answer:
            print(f"\n  Assistant ({name} · {model}):")
            for line in answer.splitlines():
                print(f"    {line}")
            print()
            return
    print("  (No answer from any available assistant. Decide using the path/size below.)\n")

# Each category: (label, list of glob-able roots, match-fn). match-fn(path)->bool
# decides whether a discovered path qualifies. We deliberately target
# directories/files that are safe to remove (regenerable caches, trash, build
# artifacts, stale downloads). We never touch app code, documents, or system files.

OLD_DOWNLOAD_DAYS = 90  # downloads untouched this long are flagged

# Directories we will NEVER descend into or suggest, regardless of category.
PROTECTED = {
    HOME / "Library" / "Mobile Documents",   # iCloud Drive
    HOME / ".ssh",
    HOME / ".gnupg",
}


@dataclass
class Candidate:
    path: Path
    size: int
    category: str
    reason: str


def human(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if nbytes < 1024:
            return f"{nbytes:.0f} {unit}" if unit == "B" else f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} PB"


def dir_size(path: Path) -> int:
    total = 0
    try:
        for root, dirs, files in os.walk(path, onerror=lambda e: None):
            for f in files:
                fp = Path(root) / f
                try:
                    if not fp.is_symlink():
                        total += fp.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def path_size(path: Path) -> int:
    try:
        if path.is_symlink():
            return 0
        if path.is_dir():
            return dir_size(path)
        return path.stat().st_size
    except OSError:
        return 0


def is_protected(path: Path) -> bool:
    return any(path == p or p in path.parents for p in PROTECTED)


def days_since_access(path: Path) -> float:
    try:
        atime = path.stat().st_atime
        return (time.time() - atime) / 86400
    except OSError:
        return 0.0


def find_candidates(root: Path) -> list:
    cands = []
    seen = set()

    def add(p: Path, category: str, reason: str):
        rp = p.resolve()
        if rp in seen or is_protected(p) or not p.exists():
            return
        seen.add(rp)
        cands.append(Candidate(p, path_size(p), category, reason))

    # 1. Trash
    add(root / ".Trash", "Trash", "Files in the Trash")

    # 2. User caches
    caches = root / "Library" / "Caches"
    if caches.is_dir():
        for entry in caches.iterdir():
            if entry.is_dir() and not is_protected(entry):
                add(entry, "Cache", "Regenerable app cache")

    # 3. Logs
    logs = root / "Library" / "Logs"
    if logs.is_dir():
        for entry in logs.iterdir():
            add(entry, "Logs", "Application logs")

    # 4. node_modules / build artifacts under common dev roots
    for dev_root in (root / "Documents", root / "Developer", root / "Projects", root / "code", root / "src"):
        if not dev_root.is_dir():
            continue
        for dirpath, dirnames, _ in os.walk(dev_root, onerror=lambda e: None):
            for d in list(dirnames):
                full = Path(dirpath) / d
                if d in ("node_modules", ".next", "dist", "build", "target", ".gradle", "__pycache__", ".pytest_cache"):
                    add(full, "Build artifact", f"{d} (regenerable build output)")
                    dirnames.remove(d)  # don't descend further

    # 5. Old downloads
    downloads = root / "Downloads"
    if downloads.is_dir():
        for entry in downloads.iterdir():
            if is_protected(entry):
                continue
            age = days_since_access(entry)
            if age >= OLD_DOWNLOAD_DAYS:
                add(entry, "Old download", f"Not accessed in {age:.0f} days")

    # 6. Sandboxed app container caches (~/Library/Containers/*/Data/Library/Caches).
    # These are regenerable just like ~/Library/Caches. We target only the inner
    # Caches dir, never the container's app data. We skip a few system agents whose
    # caches macOS rebuilds constantly and aren't worth churning.
    SKIP_CONTAINERS = {"com.apple.mediaanalysisd", "com.apple.photoanalysisd"}
    containers = root / "Library" / "Containers"
    if containers.is_dir():
        for c in containers.iterdir():
            if c.name in SKIP_CONTAINERS or is_protected(c):
                continue
            cache = c / "Data" / "Library" / "Caches"
            if cache.is_dir():
                add(cache, "Container cache", f"Regenerable cache for {c.name}")

    # 7. Xcode / iOS developer junk (regenerable build/index data and old backups).
    dev = root / "Library" / "Developer"
    for sub, why in (
        (dev / "Xcode" / "DerivedData", "Xcode build/index data (regenerable)"),
        (dev / "Xcode" / "Archives", "Old Xcode archives"),
        (dev / "Xcode" / "iOS DeviceSupport", "iOS device symbol caches"),
        (dev / "CoreSimulator" / "Caches", "Simulator caches"),
    ):
        if sub.is_dir():
            add(sub, "Xcode/iOS", why)

    # 8. iOS device backups (often many GB; only flag, user decides).
    backups = root / "Library" / "Application Support" / "MobileSync" / "Backup"
    if backups.is_dir():
        for b in backups.iterdir():
            if b.is_dir():
                add(b, "iOS backup", "iPhone/iPad backup — large, restore-from-iCloud possible")

    # 9. Stray .DS_Store files
    for dirpath, _, files in os.walk(root, onerror=lambda e: None):
        if "Library" in Path(dirpath).parts:
            continue
        for f in files:
            if f == ".DS_Store":
                add(Path(dirpath) / f, ".DS_Store", "macOS Finder metadata")

    return cands


def delete(path: Path) -> bool:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True
    except OSError as e:
        print(f"  ! Failed to delete: {e}", file=sys.stderr)
        return False


def main():
    ap = argparse.ArgumentParser(description="Suggest & confirm-delete macOS junk.")
    ap.add_argument("--path", default=str(HOME), help="Root to scan (default: home)")
    ap.add_argument("--delete", action="store_true", help="Enable interactive deletion")
    ap.add_argument("--min-size", type=float, default=1.0, help="Min size in MB to show (default: 1)")
    args = ap.parse_args()

    root = Path(args.path).expanduser()
    min_bytes = int(args.min_size * 1024 * 1024)

    print(f"Scanning {root} for reclaimable junk...\n")
    cands = [c for c in find_candidates(root) if c.size >= min_bytes]
    cands.sort(key=lambda c: c.size, reverse=True)

    if not cands:
        print("Nothing above the size threshold found. Disk looks clean.")
        return

    total = sum(c.size for c in cands)
    print(f"Found {len(cands)} candidates totalling {human(total)} reclaimable:\n")
    for c in cands:
        print(f"  [{c.category:14}] {human(c.size):>9}  {c.path}")
        print(f"  {'':14}            ↳ {c.reason}")
    print()

    if not args.delete:
        print("Suggestion-only mode. Re-run with --delete to confirm deletions per-file.")
        return

    reclaimed = 0
    print("Deletion mode — you'll be asked about each item.")
    print("  y = delete   N = skip (default)   q = quit   ? = ask the assistant about this file\n")
    for c in cands:
        while True:
            ans = input(f"Delete [{human(c.size)}] {c.path} ? (y/N/q/?) ").strip().lower()
            if ans in ("?", "ask"):
                # Ask a question about THIS file, then loop back and re-prompt the same item.
                q = input("    Your question (e.g. 'what is this?', 'is it safe to delete?'): ").strip()
                if q:
                    ask_about_file(c, q)
                continue
            break  # y / N / q / anything-else → fall through to the decision below

        if ans == "q":
            print("Stopping.")
            break
        if ans == "y":
            if delete(c.path):
                reclaimed += c.size
                print(f"  ✓ Removed ({human(c.size)} freed)")
        else:
            print("  • Skipped")

    print(f"\nDone. Reclaimed {human(reclaimed)}.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
