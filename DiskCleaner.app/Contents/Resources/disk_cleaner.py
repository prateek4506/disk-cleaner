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

OLD_DOWNLOAD_DAYS = 90          # SMALL downloads untouched this long are flagged
BIG_DOWNLOAD_MB = 50           # downloads at/over this size are flagged REGARDLESS of age
                               # (e.g. a .dmg you installed today and no longer need)
INSTALLER_EXTS = {".dmg", ".pkg", ".iso", ".zip", ".tar", ".gz", ".tgz", ".xip"}
BIG_FILE_MB = 5                # "largest files" scan: items under this are deprioritized
LOW_PRIORITY = 9_999          # sort key bump so sub-5MB items rank last
LEFTOVER_COLD_DAYS = 30       # an app's leftover data must be untouched this long to flag —
                              # an INSTALLED app touches its support files, so recent access
                              # means it's not really orphaned (guards bundle-id mismatches)

# Directories we will NEVER descend into or suggest, regardless of category.
PROTECTED = {
    HOME / "Library" / "Mobile Documents",   # iCloud Drive
    HOME / ".ssh",
    HOME / ".gnupg",
}

# Roots that are NEVER user-deletable — skipped by the whole-disk scan so we never
# surface (let alone offer to delete) something that would break macOS.
SYSTEM_SKIP = [
    "/System", "/Library/Apple", "/private/var/db", "/private/var/folders",
    "/usr", "/bin", "/sbin", "/cores", "/dev", "/Volumes", "/.Spotlight-V100",
    "/.fseventsd", "/private/var/vm",
]


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


def _disp_path(path) -> str:
    """Show the path with ~ for home, for compactness."""
    s = str(path)
    home = str(HOME)
    return "~" + s[len(home):] if s.startswith(home) else s


def _trunc_left(s: str, width: int) -> str:
    """Truncate from the LEFT (keep the meaningful tail) with a leading ellipsis."""
    return s if len(s) <= width else "…" + s[-(width - 1):]


def print_candidate_table(cands, ai_hint_top=0, interactive=False) -> None:
    """Render the candidates as a bordered table: #, Size, Category, Item (+ reason row).

    If ai_hint_top > 0 and an AI advisor is available, the N biggest rows are marked with a
    '*' so the user knows exactly where pressing ? pays off most. Enrichment is on-demand
    (the ? key), so the scan stays instant — the marker just guides attention. The "press ?"
    legend is only shown when `interactive` is True (i.e. a y/N/?/q prompt actually follows);
    otherwise there is nothing to press ? at, so we omit it."""
    hint_rows = ai_hint_top if (ai_hint_top and interactive and _ai_active_model()) else 0
    n_w = max(len("#"), len(str(len(cands))))
    if hint_rows:
        n_w += 1  # room for the '*' AI marker in the # column
    size_w = max(len("Size"), max((len(human(c.size)) for c in cands), default=4))
    cat_w = max(len("Category"), max((len(c.category) for c in cands), default=8))
    # Fit the Item column to the terminal width, with sensible bounds.
    term_w = shutil.get_terminal_size((100, 24)).columns
    fixed = n_w + size_w + cat_w + 13  # borders + padding between the 4 columns
    item_w = max(24, min(70, term_w - fixed))

    def row(a, b, c, d):
        return f"  │ {a:>{n_w}} │ {b:>{size_w}} │ {c:<{cat_w}} │ {d:<{item_w}} │"

    bar_t = f"  ┌{'─'*(n_w+2)}┬{'─'*(size_w+2)}┬{'─'*(cat_w+2)}┬{'─'*(item_w+2)}┐"
    bar_m = f"  ├{'─'*(n_w+2)}┼{'─'*(size_w+2)}┼{'─'*(cat_w+2)}┼{'─'*(item_w+2)}┤"
    bar_b = f"  └{'─'*(n_w+2)}┴{'─'*(size_w+2)}┴{'─'*(cat_w+2)}┴{'─'*(item_w+2)}┘"
    # The reason line spans the Size+Category+Item columns (incl. their 2 inner separators
    # "│" and the surrounding spaces): size_w+cat_w+item_w + 2*3 padding + 2 separators.
    reason_w = size_w + cat_w + item_w + 6

    print(bar_t)
    print(row("#", "Size", "Category", "Item"))
    print(bar_m)
    for i, c in enumerate(cands, 1):
        num = f"{i}*" if (hint_rows and i <= hint_rows) else str(i)
        print(row(num, human(c.size), _trunc_left(c.category, cat_w), _trunc_left(_disp_path(c.path), item_w)))
        reason = _trunc_left("↳ " + c.reason, reason_w)
        print(f"  │ {'':>{n_w}} │ {reason:<{reason_w}} │")
    print(bar_b)
    if hint_rows:
        print(f"   * = your {hint_rows} biggest items — press ? on these to have the AI "
              f"explain what they are.")


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


# Bundle IDs we never treat as "orphaned" even if no .app is found: Apple's own and
# common frameworks/agents that legitimately live in ~/Library without a user-facing app.
_NEVER_ORPHAN_PREFIXES = ("com.apple.", "group.com.apple.", "com.crashlytics", "com.google.")


def installed_bundle_ids() -> set:
    """Bundle identifiers of every app currently installed, so we can tell which
    ~/Library leftovers belong to apps that are GONE. Reads CFBundleIdentifier from each
    .app's Info.plist across the standard application locations."""
    ids = set()
    app_roots = [Path("/Applications"), HOME / "Applications",
                 Path("/System/Applications"), Path("/Applications/Utilities")]
    for root in app_roots:
        if not root.is_dir():
            continue
        try:
            entries = list(root.glob("*.app")) + list(root.glob("*/*.app"))
        except OSError:
            continue
        for app in entries:
            plist = app / "Contents" / "Info.plist"
            try:
                out = subprocess.run(
                    ["defaults", "read", str(plist), "CFBundleIdentifier"],
                    capture_output=True, text=True, timeout=5,
                ).stdout.strip()
            except Exception:
                out = ""
            if out:
                ids.add(out.lower())
    return ids


def _looks_like_bundle_id(name: str) -> bool:
    """Folder/file named like a reverse-DNS bundle id (e.g. 'com.spotify.client')."""
    return bool(re.fullmatch(r"[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+){1,}", name)) and "." in name


def days_since_access(path: Path) -> float:
    try:
        atime = path.stat().st_atime
        return (time.time() - atime) / 86400
    except OSError:
        return 0.0


def days_since_touched(path: Path) -> float:
    """How long since this path was last accessed OR modified — whichever is more recent.
    macOS often disables strict atime, so we also consider mtime; a recently-MODIFIED
    folder clearly belongs to a live app even if atime looks stale."""
    try:
        st = path.stat()
        newest = max(st.st_atime, st.st_mtime)
        return (time.time() - newest) / 86400
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

    # 5. Downloads. A LARGE download is flagged regardless of age — e.g. a .dmg/.pkg you
    # installed today and no longer need. SMALL downloads are only flagged when stale.
    downloads = root / "Downloads"
    if downloads.is_dir():
        for entry in downloads.iterdir():
            if is_protected(entry):
                continue
            sz = path_size(entry)
            age = days_since_access(entry)
            is_installer = entry.suffix.lower() in INSTALLER_EXTS
            if sz >= BIG_DOWNLOAD_MB * 1024 * 1024 or (is_installer and sz > 0):
                why = "Installer/archive — often safe to delete after use" if is_installer \
                      else f"Large download ({human(sz)})"
                add(entry, "Download", why)
            elif age >= OLD_DOWNLOAD_DAYS:
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

    # 9. Leftovers from UNINSTALLED apps. When you drag an app to the Trash, macOS leaves
    # its support data behind in ~/Library, keyed by bundle id (e.g. com.spotify.client).
    # We flag those folders ONLY when no installed .app claims that bundle id — so data for
    # apps you still have is never touched. Apple/system ids are always left alone.
    installed = installed_bundle_ids()
    leftover_roots = [
        (root / "Library" / "Application Support", "support data"),
        (root / "Library" / "Caches", "cache"),
        (root / "Library" / "Logs", "logs"),
        (root / "Library" / "Preferences", "preferences"),
        (root / "Library" / "Saved Application State", "saved window state"),
        (root / "Library" / "Containers", "sandbox container"),
        (root / "Library" / "HTTPStorages", "web storage"),
    ]
    for base, kind in leftover_roots:
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            if is_protected(entry):
                continue
            # Preferences are ".plist" files; everything else is a folder named by bundle id.
            name = entry.stem if entry.suffix == ".plist" else entry.name
            # "<bundleid>.savedState" for saved state; strip the suffix to get the id.
            name = name[:-len(".savedState")] if name.endswith(".savedState") else name
            if not _looks_like_bundle_id(name):
                continue
            nl = name.lower()
            if nl in installed or any(nl.startswith(p) for p in _NEVER_ORPHAN_PREFIXES):
                continue
            # Age guard: a still-installed app keeps touching its support files. If this
            # data was accessed/modified recently, the app is effectively live — skip it,
            # even if the bundle id didn't match (handles MAS/container naming quirks).
            if days_since_touched(entry) < LEFTOVER_COLD_DAYS:
                continue
            cold = days_since_touched(entry)
            add(entry, "App leftover",
                f"{kind} for '{name}' — no matching app found, untouched {cold:.0f}d "
                f"(verify before deleting)")

    # 10. Stray .DS_Store files
    for dirpath, _, files in os.walk(root, onerror=lambda e: None):
        if "Library" in Path(dirpath).parts:
            continue
        for f in files:
            if f == ".DS_Store":
                add(Path(dirpath) / f, ".DS_Store", "macOS Finder metadata")

    return cands


def _skip_dir(path: Path) -> bool:
    """True if a directory must not be descended into during the whole-disk scan."""
    s = str(path)
    if any(s == sk or s.startswith(sk + "/") for sk in SYSTEM_SKIP):
        return True
    if is_protected(path):
        return True
    # Don't descend into bundles/packages — a .app/.framework is one logical unit, and
    # surfacing individual files inside it is noise (and deleting them breaks the app).
    if path.suffix.lower() in (".app", ".framework", ".bundle", ".plugin", ".kext", ".photoslibrary"):
        return True
    return False


def find_largest_files(root: Path, top_n: int = 40) -> list:
    """Walk the filesystem and return the biggest INDIVIDUAL files as candidates, largest
    first. Opt-in (--scan-disk) because a full walk is slow. Heavily guarded: never descends
    into SYSTEM_SKIP / protected paths or app bundles, silently skips permission-denied dirs,
    follows no symlinks. Files under BIG_FILE_MB are deprioritized (the user asked for the
    truly large items). Pure discovery — these are just 'big files', not known-safe junk, so
    the reason makes clear the user must judge each one."""
    biggest = []   # list of (size, Path)
    min_bytes = 1 * 1024 * 1024   # ignore sub-1MB entirely; not worth surfacing
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False,
                                                onerror=lambda e: None):
        here = Path(dirpath)
        # Prune subdirectories we must not enter (modifying dirnames in-place prunes the walk).
        dirnames[:] = [d for d in dirnames if not _skip_dir(here / d)]
        for fn in filenames:
            fp = here / fn
            try:
                st = fp.lstat()
            except OSError:
                continue
            if not os.path.isfile(fp) or os.path.islink(fp):
                continue
            sz = st.st_size
            if sz >= min_bytes:
                biggest.append((sz, fp))

    biggest.sort(key=lambda t: t[0], reverse=True)
    out = []
    for sz, fp in biggest[:top_n]:
        small = sz < BIG_FILE_MB * 1024 * 1024
        note = " (small — low priority)" if small else ""
        out.append(Candidate(fp, sz, "Large file",
                             f"One of the biggest files on disk{note} — review before deleting"))
    return out


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


def trash(path: Path) -> bool:
    """Move a file to the macOS Trash (RECOVERABLE) rather than deleting it.
    Used for photos — irreplaceable personal data — so we NEVER hard-delete here: if the
    file can't be safely moved to the Trash, we leave it untouched and report failure.

    We move the file into ~/.Trash ourselves (a plain filesystem move, which needs no
    Automation/TCC permission, unlike scripting Finder). Names are made collision-safe the
    same way Finder does ("name 2.jpg"). Only handles items on the home volume; cross-volume
    items would need that volume's .Trashes and are skipped rather than risked."""
    try:
        if not path.exists():
            return False
        trash_dir = HOME / ".Trash"
        trash_dir.mkdir(exist_ok=True)
        dest = trash_dir / path.name
        i = 1
        while dest.exists():
            dest = trash_dir / f"{path.stem} {i}{path.suffix}"
            i += 1
        shutil.move(str(path), str(dest))
        return True
    except Exception as e:
        print(f"  ! Could not move to Trash (left in place): {path} — {e}", file=sys.stderr)
        return False


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".heic", ".heif", ".tiff", ".tif",
              ".bmp", ".webp", ".raw", ".cr2", ".nef", ".arw", ".dng"}


def _file_hash(path: Path, chunk=1 << 20) -> str:
    """SHA-256 of a file's bytes, streamed so large photos don't blow up memory."""
    import hashlib
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(chunk), b""):
                h.update(block)
    except OSError:
        return ""
    return h.hexdigest()


def find_duplicate_photos(root: Path) -> list:
    """Find groups of BYTE-IDENTICAL image files (exact copies). Returns a list of groups,
    each a list of Paths that share identical content, largest-group/largest-file first.

    Exact-match only (sha256) — zero false positives. To stay fast we first bucket by
    (size, extension) and only hash files whose size collides with another, so we never
    hash a file that can't possibly have a duplicate."""
    by_size = {}
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False,
                                                onerror=lambda e: None):
        here = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not _skip_dir(here / d)]
        for fn in filenames:
            fp = here / fn
            if fp.suffix.lower() not in IMAGE_EXTS:
                continue
            try:
                st = fp.lstat()
            except OSError:
                continue
            if os.path.islink(fp) or not os.path.isfile(fp) or st.st_size == 0:
                continue
            by_size.setdefault(st.st_size, []).append(fp)

    groups = []
    for size, paths in by_size.items():
        if len(paths) < 2:            # unique size → can't be an exact dup; skip hashing
            continue
        by_hash = {}
        for p in paths:
            digest = _file_hash(p)
            if digest:
                by_hash.setdefault(digest, []).append(p)
        for digest, dups in by_hash.items():
            if len(dups) > 1:
                groups.append(sorted(dups, key=lambda p: str(p)))
    # Biggest wasted space first: (file size * extra copies).
    groups.sort(key=lambda g: g[0].stat().st_size * (len(g) - 1), reverse=True)
    return groups


def _ai_active_model() -> str:
    """Return a short 'provider · model' label if an AI advisor is ready now, else ''."""
    if FORCED_PROVIDER != "opencode":
        m = _pick_ollama_model()
        if m:
            return f"Ollama · {m}  (local & private)"
    if FORCED_PROVIDER != "ollama":
        m = _pick_opencode_model()
        if m:
            return f"OpenCode · {m}"
    return ""


def print_ai_banner() -> None:
    """At startup, tell the user whether the AI advisor is live — and if not, make the
    case for adding Ollama (private, free, keyless). The cleaner works fully without it."""
    active = _ai_active_model()
    if active:
        print("  🤖 AI advisor: ON — " + active)
        print("     Press ? at any prompt to ask whether a file is safe to delete.\n")
        return

    # Ollama is installed but no model is pulled — they're one command away, so don't tell
    # them to install what they already have. Show the short "just pull a model" nudge.
    if _have("ollama") and FORCED_PROVIDER != "opencode":
        print("  ───────────────────────────────────────────────────────────────")
        print("   🤖  Ollama is installed — pull one model and the ? advisor turns on:")
        print(f"         ollama pull {OLLAMA_DEFAULT_PULL}     (~2 GB, one-time)")
        print("   Or press ? on any file later and the cleaner offers to download it for you.")
        print("   …meanwhile the built-in engine works perfectly without it.")
        print("  ───────────────────────────────────────────────────────────────\n")
        return

    # Nothing installed — show the full attractive pitch (built-in engine still works great).
    # Borderless (left rule only) so wide emoji glyphs can't break box alignment.
    print("  ───────────────────────────────────────────────────────────────")
    print("   🧹  The built-in engine already finds safe junk to delete.")
    print("       Add a local AI model and it gets smarter — press ? on any")
    print("       file to ask \"what is this / is it safe?\" in plain English.")
    print()
    print("   Why run AI locally with Ollama?")
    print("     🔒  Private    your file paths never leave this Mac")
    print("     💸  Free       no account, no API key, no bill, no limits")
    print("     ✈️   Offline    runs locally with no internet (after setup)")
    print("     🧹  Deletable  it's just files — try it, remove it anytime")
    print("                    (ollama rm <model>) with zero leftovers")
    print()
    print("   Get started (one-time):")
    print("     brew install ollama && ollama pull llama3.2     (~2 GB)")
    print("   …or just keep going — the cleaner works perfectly without it.")
    print("  ───────────────────────────────────────────────────────────────\n")


def run_find_dupes(root: Path, do_delete: bool) -> None:
    """Find and (optionally) clean up byte-identical duplicate photos. Extra-careful mode:
    these are personal photos, so we only ever Trash extra copies (recoverable), keep at
    least one, and never touch anything without explicit per-group confirmation."""
    print(f"  📷 Photo duplicate finder — scanning {_disp_path(root)} for identical copies…")
    print("     (Exact byte-for-byte matches only. Your photos are precious, so extra copies")
    print("      go to the Trash — recoverable — and one copy of each is always kept.)\n")

    groups = find_duplicate_photos(root)
    if not groups:
        print("  No duplicate photos found. ✓")
        return

    wasted = sum(g[0].stat().st_size * (len(g) - 1) for g in groups)
    print(f"  Found {len(groups)} sets of duplicates — about {human(wasted)} of wasted space "
          f"in redundant copies.\n")

    for i, g in enumerate(groups, 1):
        sz = g[0].stat().st_size
        print(f"  ── Set {i}/{len(groups)} — {len(g)} identical copies, {human(sz)} each ──")
        for j, p in enumerate(g):
            tag = "  (keep this one)" if j == 0 else ""
            print(f"     [{j+1}] {_disp_path(p)}{tag}")

        if not do_delete:
            print()
            continue

        # The first copy (alphabetical) is the default keeper; user can pick another or skip.
        ans = input(f"     Trash the other {len(g)-1} and keep [1]? "
                    f"(y / number to keep / N skip / q quit) ").strip().lower()
        if ans == "q":
            print("  Stopping.")
            return
        keep_idx = 0
        if ans.isdigit() and 1 <= int(ans) <= len(g):
            keep_idx = int(ans) - 1
        elif ans != "y":
            print("     • Skipped\n")
            continue
        freed = 0
        for j, p in enumerate(g):
            if j == keep_idx:
                continue
            if trash(p):
                freed += sz
                print(f"     ✓ Trashed {_disp_path(p)}")
        print(f"     kept {_disp_path(g[keep_idx])} · freed {human(freed)}\n")

    if not do_delete:
        print("  Review-only. Re-run with --find-dupes --delete to Trash extra copies "
              "(one is always kept).")


def main():
    ap = argparse.ArgumentParser(description="Suggest & confirm-delete macOS junk.")
    ap.add_argument("--path", default=str(HOME), help="Root to scan (default: home)")
    ap.add_argument("--delete", action="store_true", help="Enable interactive deletion")
    ap.add_argument("--min-size", type=float, default=10.0,
                    help="Min size in MB to show (default: 10). Use --min-size 1 to also see "
                         "the many tiny regenerable caches.")
    ap.add_argument("--scan-disk", action="store_true",
                    help="Also scan the WHOLE disk for the largest files (slower). "
                         "System/protected paths are always skipped.")
    ap.add_argument("--find-dupes", action="store_true",
                    help="Find duplicate PHOTOS (byte-identical copies) instead of junk. "
                         "Review-only; deletes go to the Trash (recoverable), never hard-deleted.")
    args = ap.parse_args()

    root = Path(args.path).expanduser()
    min_bytes = int(args.min_size * 1024 * 1024)

    # Photo de-dupe is a distinct, extra-careful mode (personal data) — handle and return.
    if args.find_dupes:
        run_find_dupes(root, do_delete=args.delete)
        return

    print_ai_banner()
    print(f"Scanning {root} for reclaimable junk...\n")
    all_found = find_candidates(root)
    cands = [c for c in all_found if c.size >= min_bytes]
    # Track what the size threshold hid, so we can offer it without burying the real wins.
    hidden = [c for c in all_found if c.size < min_bytes]

    # Opt-in whole-disk pass: surface the biggest individual files anywhere (guarded against
    # system/protected paths). Dedup against junk we already found by resolved path.
    if args.scan_disk:
        disk_root = Path("/")
        print(f"Scanning the whole disk ({disk_root}) for the largest files — this can "
              f"take a minute…\n")
        seen = {c.path.resolve() for c in cands}
        for big in find_largest_files(disk_root):
            if big.size >= min_bytes and big.path.resolve() not in seen:
                cands.append(big)

    # Sort largest-first, but keep deprioritized sub-BIG_FILE_MB "Large file" items last.
    cands.sort(key=lambda c: (
        0 if not (c.category == "Large file" and c.size < BIG_FILE_MB * 1024 * 1024) else LOW_PRIORITY,
        -c.size,
    ))

    def hidden_note() -> str:
        if not hidden:
            return ""
        htotal = sum(c.size for c in hidden)
        return (f"  (+{len(hidden)} smaller items under {args.min_size:g} MB, {human(htotal)} "
                f"total — see them with --min-size 1)")

    if not cands:
        if hidden:
            print(f"No items ≥ {args.min_size:g} MB. {len(hidden)} smaller ones exist "
                  f"({human(sum(c.size for c in hidden))}) — see them with --min-size 1.")
        else:
            print("Nothing reclaimable found. Disk looks clean.")
        return

    total = sum(c.size for c in cands)
    print(f"Found {len(cands)} candidates totalling {human(total)} reclaimable:\n")
    # Whether a y/N/?/q prompt will follow: always in --delete, or if the user accepts the
    # offer below. We decide that first so the table's "press ?" legend is honest.
    going_interactive = args.delete
    if not going_interactive:
        try:
            resp = input("Review these and delete the ones you choose now? [y/N] ").strip().lower()
        except EOFError:
            resp = "n"
        going_interactive = resp == "y"
        print()

    print_candidate_table(cands, ai_hint_top=10, interactive=going_interactive)
    note = hidden_note()
    if note:
        print(note)
    print()

    if not going_interactive:
        print("No problem — nothing was deleted. Re-run anytime; add --delete to go straight "
              "to the prompts.")
        return

    run_interactive_deletion(cands)


def run_interactive_deletion(cands) -> None:
    """Walk the candidates, asking y/N/q/? for each. Deletes only on an explicit 'y'."""
    reclaimed = 0
    print("You'll be asked about each item. Your options at each prompt:\n")
    print("  ┌─────┬──────────────────────────────────────────────────────────┐")
    print("  │ key │ what it does                                              │")
    print("  ├─────┼──────────────────────────────────────────────────────────┤")
    print("  │  y  │ delete this item                                         │")
    print("  │  N  │ skip it and move on  (default — just press Enter)        │")
    print("  │  q  │ quit — stop here and exit (already-deleted items stay)   │")
    print("  │  ?  │ AI explains what this is & whether it's safe to delete   │")
    print("  └─────┴──────────────────────────────────────────────────────────┘\n")
    for c in cands:
        while True:
            ans = input(f"Delete [{human(c.size)}] {c.path} ? (y/N/q/?) ").strip().lower()
            if ans in ("?", "ask"):
                # Answer the implicit question immediately — no follow-up prompt. The user
                # pressed ? to learn "what is this and is it safe to delete?"; just answer it,
                # then re-prompt the same item for the y/N/q decision.
                ask_about_file(c, "What is this, and is it safe to delete?")
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
