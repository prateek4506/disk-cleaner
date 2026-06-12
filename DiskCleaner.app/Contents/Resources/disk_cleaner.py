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
# It needs NO API key of its own; it uses whatever local provider you have, in this order:
#   1. Ollama   — fully local, no account, no key, private (https://ollama.com). RECOMMENDED.
#   2. OpenCode  — uses your existing `opencode` login (https://opencode.ai).
# If neither is available, `?` says so and the normal y/N flow continues.
#
# Override the model per provider with env vars:
#   DISKCLEANER_OLLAMA_MODEL   (default: llama3.2)
#   DISKCLEANER_OPENCODE_MODEL (default: opencode/deepseek-v4-flash-free)
OLLAMA_MODEL = os.environ.get("DISKCLEANER_OLLAMA_MODEL", "llama3.2")
OPENCODE_MODEL = os.environ.get("DISKCLEANER_OPENCODE_MODEL",
                                os.environ.get("DISKCLEANER_AI_MODEL", "opencode/deepseek-v4-flash-free"))
# Force a single provider with DISKCLEANER_AI_PROVIDER=ollama|opencode (default: auto = try both).
FORCED_PROVIDER = os.environ.get("DISKCLEANER_AI_PROVIDER", "").strip().lower()
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _ollama_ready() -> bool:
    """True only if ollama is installed AND the target model is already pulled.

    Without this guard, `ollama run <model>` would silently auto-DOWNLOAD a multi-GB
    model mid-prompt — a minutes-long hang on first use. We require the model to exist
    locally so the advisor falls through to the next provider instantly instead.
    """
    if not _have("ollama"):
        return False
    try:
        out = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return False
    base = OLLAMA_MODEL.split(":")[0]
    # Match the model name at the start of any line (ollama lists "name:tag  id  size …").
    return any(line.split(":")[0].strip() == base or line.startswith(OLLAMA_MODEL)
               for line in out.splitlines())


def _clean_model_output(raw: str) -> str:
    """Strip TUI banner + ANSI/control noise, leaving just the model's answer."""
    text = _ANSI_RE.sub("", raw).replace("\r", "")
    text = text.translate({0x04: None, 0x08: None, 0x07: None})
    # `script` may emit caret representations of control chars (literal "^D", "^H").
    text = re.sub(r"\^[A-Z@\[\]\\^_]", "", text)
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("> build") or s.startswith(">> "):
            continue
        lines.append(s)
    return "\n".join(lines).strip()


def _ask_ollama(prompt: str) -> str:
    """Local, keyless. Returns the answer text, or '' on failure."""
    proc = subprocess.run(
        ["ollama", "run", OLLAMA_MODEL, prompt],
        capture_output=True, text=True, timeout=120, errors="replace",
    )
    return _clean_model_output((proc.stdout or "") + (proc.stderr or ""))


def _ask_opencode(prompt: str) -> str:
    """Uses the user's existing opencode login. Returns the answer text, or '' on failure."""
    # `script` supplies the pseudo-TTY opencode's `run` needs; -q quiet, no typescript file.
    proc = subprocess.run(
        ["script", "-q", "/dev/null", "opencode", "run", "-m", OPENCODE_MODEL, prompt],
        capture_output=True, text=True, timeout=120, errors="replace",
    )
    return _clean_model_output((proc.stdout or "") + (proc.stderr or ""))


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

    # Build the provider chain. Each entry: (name, available?, runner).
    providers = [
        ("Ollama (local)", _ollama_ready(), _ask_ollama),
        ("OpenCode", _have("opencode"), _ask_opencode),
    ]
    if FORCED_PROVIDER in ("ollama", "opencode"):
        providers = [p for p in providers if p[0].lower().startswith(FORCED_PROVIDER)]

    available = [p for p in providers if p[1]]
    if not available:
        print("  (AI advisor unavailable. Install one — both are free and need no API key for this:)")
        print("    • Ollama (local, private):  https://ollama.com   then:  ollama pull llama3.2")
        print("    • OpenCode:                 https://opencode.ai   then:  opencode auth login\n")
        return

    print("  … asking the assistant (this can take a few seconds)")
    for name, _ok, runner in available:
        try:
            answer = runner(prompt)
        except subprocess.TimeoutExpired:
            print(f"  ({name} timed out — trying the next provider if any.)")
            continue
        except Exception:  # never let the advisor break the cleaner
            continue
        if answer:
            print(f"\n  Assistant ({name}):")
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
