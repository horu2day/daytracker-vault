"""
scripts/install_git_hook.py - Install DayTracker post-commit hook in git repos.

Walks all git repositories found under config.watch_roots (up to depth 2)
and installs (or removes) a post-commit hook that calls git_commit.py after
each commit.

The hook snippet added to .git/hooks/post-commit:
    # --- DayTracker post-commit hook ---
    DAYTRACKER_ROOT="<project_root>"
    python "$DAYTRACKER_ROOT/scripts/collectors/git_commit.py" --repo "$(pwd)" &
    # --- END DayTracker ---

The script is idempotent: re-running on an already-hooked repo is safe.

Usage:
    python scripts/install_git_hook.py [--dry-run]
    python scripts/install_git_hook.py [--uninstall] [--dry-run]
"""

from __future__ import annotations

import io
import os
import stat
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# UTF-8 stdout on Windows (guard against double-wrapping when imported)
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    if hasattr(sys.stdout, "buffer") and not getattr(sys.stdout, "_daytracker_wrapped", False):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stdout._daytracker_wrapped = True  # type: ignore[attr-defined]
    if hasattr(sys.stderr, "buffer") and not getattr(sys.stderr, "_daytracker_wrapped", False):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
        sys.stderr._daytracker_wrapped = True  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.config import Config  # noqa: E402

# ---------------------------------------------------------------------------
# Hook markers (used to identify our injected block)
# ---------------------------------------------------------------------------
HOOK_BEGIN_MARKER = "# --- DayTracker post-commit hook ---"
HOOK_END_MARKER = "# --- END DayTracker ---"

HOOK_SHEBANG = "#!/bin/sh"


def _build_hook_snippet(project_root: str) -> str:
    """
    Build the shell snippet to inject into the post-commit hook.

    Uses forward-slash paths so the script works in Git Bash on Windows.
    The git_commit.py call is backgrounded (&) so it does not block the commit.

    Parameters
    ----------
    project_root:
        Absolute path to the DayTracker project root.

    Returns
    -------
    str
        Multi-line shell snippet (including begin/end markers).
    """
    # Use forward slashes for cross-platform git-bash compatibility
    root = str(project_root).replace("\\", "/")
    return (
        f"{HOOK_BEGIN_MARKER}\n"
        f'DAYTRACKER_ROOT="{root}"\n'
        f'python "$DAYTRACKER_ROOT/scripts/collectors/git_commit.py" --repo "$(pwd)" &\n'
        f"{HOOK_END_MARKER}\n"
    )


# ---------------------------------------------------------------------------
# Git repo discovery
# ---------------------------------------------------------------------------

def find_git_repos(watch_roots: list[str], max_depth: int = 2) -> list[Path]:
    """
    Walk watch_roots and return paths of directories that contain a .git/ subdir.

    Parameters
    ----------
    watch_roots:
        List of root directories to scan.
    max_depth:
        Maximum subdirectory depth to search (1 = only watch_roots themselves,
        2 = one level of sub-directories, etc.).

    Returns
    -------
    list[Path]
        Sorted list of unique repo root paths.
    """
    repos: list[Path] = []

    def _walk(directory: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            for entry in directory.iterdir():
                if not entry.is_dir():
                    continue
                # Skip hidden dirs and common noise
                if entry.name.startswith(".") and entry.name != ".":
                    continue
                git_dir = entry / ".git"
                if git_dir.exists():
                    repos.append(entry.resolve())
                else:
                    _walk(entry, depth + 1)
        except PermissionError:
            pass

    for root_str in watch_roots:
        root = Path(root_str)
        if not root.exists():
            print(
                f"[install_git_hook] WARNING: watch_root does not exist: {root}",
                file=sys.stderr,
            )
            continue
        # Also check if the watch_root itself is a repo
        if (root / ".git").exists():
            repos.append(root.resolve())
        else:
            _walk(root, 1)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[Path] = []
    for r in repos:
        key = str(r).lower()
        if key not in seen:
            seen.add(key)
            unique.append(r)

    return sorted(unique)


# ---------------------------------------------------------------------------
# Install / uninstall helpers
# ---------------------------------------------------------------------------

def _hook_already_installed(hook_content: str) -> bool:
    """Return True if the DayTracker block is already present."""
    return HOOK_BEGIN_MARKER in hook_content


def _remove_daytracker_block(hook_content: str) -> str:
    """
    Remove the DayTracker-injected lines from hook content.

    Removes everything between HOOK_BEGIN_MARKER and HOOK_END_MARKER
    (inclusive) plus any blank line immediately following the block.
    """
    lines = hook_content.splitlines(keepends=True)
    result: list[str] = []
    in_block = False
    skip_next_blank = False

    for line in lines:
        stripped = line.rstrip("\r\n")
        if stripped == HOOK_BEGIN_MARKER:
            in_block = True
            continue
        if stripped == HOOK_END_MARKER:
            in_block = False
            skip_next_blank = True  # skip the trailing blank line if any
            continue
        if in_block:
            continue
        if skip_next_blank and stripped == "":
            skip_next_blank = False
            continue
        skip_next_blank = False
        result.append(line)

    return "".join(result)


def install_hook(repo_path: Path, project_root: str, dry_run: bool = False) -> str:
    """
    Install the DayTracker post-commit hook in a single repository.

    Parameters
    ----------
    repo_path:
        Path to the repository root (contains .git/).
    project_root:
        Absolute path to the DayTracker project root.
    dry_run:
        If True, print what would be done without making changes.

    Returns
    -------
    str
        One of: "installed", "already_installed", "updated", "error"
    """
    hook_dir = repo_path / ".git" / "hooks"
    hook_file = hook_dir / "post-commit"

    snippet = _build_hook_snippet(project_root)

    try:
        # Read existing hook (if any)
        existing_content = ""
        if hook_file.exists():
            existing_content = hook_file.read_text(encoding="utf-8", errors="replace")

        if _hook_already_installed(existing_content):
            if dry_run:
                print(f"[install_git_hook][dry-run] Already installed: {repo_path}")
            return "already_installed"

        # Build new content
        if not existing_content:
            # Create fresh hook with shebang
            new_content = f"{HOOK_SHEBANG}\n\n{snippet}"
        else:
            # Append to existing hook (after a blank line)
            # Ensure existing content ends with newline
            if not existing_content.endswith("\n"):
                existing_content += "\n"
            new_content = existing_content + "\n" + snippet

        if dry_run:
            print(f"[install_git_hook][dry-run] Would install hook in: {repo_path}")
            print(f"  Hook file: {hook_file}")
            print(f"  Snippet:\n    " + snippet.replace("\n", "\n    ").rstrip())
            return "installed"

        # Write hook
        hook_dir.mkdir(parents=True, exist_ok=True)
        hook_file.write_text(new_content, encoding="utf-8")

        # Make executable on Unix (chmod +x)
        if sys.platform != "win32":
            current = hook_file.stat().st_mode
            hook_file.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        print(f"[install_git_hook] Installed hook: {repo_path}")
        return "installed"

    except OSError as exc:
        print(
            f"[install_git_hook] ERROR: Could not write hook for {repo_path}: {exc}",
            file=sys.stderr,
        )
        return "error"


def uninstall_hook(repo_path: Path, dry_run: bool = False) -> str:
    """
    Remove the DayTracker block from a repository's post-commit hook.

    Parameters
    ----------
    repo_path:
        Path to the repository root (contains .git/).
    dry_run:
        If True, print what would be done without making changes.

    Returns
    -------
    str
        One of: "removed", "not_installed", "empty_removed", "error"
    """
    hook_file = repo_path / ".git" / "hooks" / "post-commit"

    if not hook_file.exists():
        if dry_run:
            print(f"[install_git_hook][dry-run] No hook file: {repo_path}")
        return "not_installed"

    try:
        content = hook_file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(
            f"[install_git_hook] ERROR: Could not read hook for {repo_path}: {exc}",
            file=sys.stderr,
        )
        return "error"

    if not _hook_already_installed(content):
        if dry_run:
            print(f"[install_git_hook][dry-run] Not installed: {repo_path}")
        return "not_installed"

    new_content = _remove_daytracker_block(content)

    if dry_run:
        print(f"[install_git_hook][dry-run] Would uninstall from: {repo_path}")
        return "removed"

    try:
        if new_content.strip() in ("", HOOK_SHEBANG):
            # Hook is now empty (only had our block); remove the file
            hook_file.unlink()
            print(f"[install_git_hook] Removed empty hook file: {repo_path}")
            return "empty_removed"
        else:
            hook_file.write_text(new_content, encoding="utf-8")
            print(f"[install_git_hook] Uninstalled from hook: {repo_path}")
            return "removed"
    except OSError as exc:
        print(
            f"[install_git_hook] ERROR: Could not update hook for {repo_path}: {exc}",
            file=sys.stderr,
        )
        return "error"


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------

def run(
    uninstall: bool = False,
    dry_run: bool = False,
    config: Optional[Config] = None,
) -> None:
    """
    Walk watch_roots, find git repos, and install/uninstall hooks.

    Parameters
    ----------
    uninstall:
        If True, remove DayTracker hooks instead of installing.
    dry_run:
        If True, only print what would be done.
    config:
        Optional Config instance. If None, loads from config.yaml.
    """
    if config is None:
        config = Config(project_root=PROJECT_ROOT)

    watch_roots = config.watch_roots
    if not watch_roots:
        print(
            "[install_git_hook] WARNING: No watch_roots configured. "
            "Set watch_roots in config.yaml.",
            file=sys.stderr,
        )
        return

    project_root = str(PROJECT_ROOT)
    action = "Uninstalling" if uninstall else "Installing"
    mode_str = " (dry-run)" if dry_run else ""
    print(f"[install_git_hook] {action} hooks{mode_str}...")
    print(f"[install_git_hook] DayTracker root: {project_root}")
    print(f"[install_git_hook] Scanning watch_roots: {watch_roots}")

    repos = find_git_repos(watch_roots)
    if not repos:
        print("[install_git_hook] No git repositories found under watch_roots.")
        return

    print(f"[install_git_hook] Found {len(repos)} git repo(s):")
    for r in repos:
        print(f"  - {r}")

    # Counters
    counts: dict[str, int] = {
        "installed": 0,
        "already_installed": 0,
        "updated": 0,
        "removed": 0,
        "not_installed": 0,
        "empty_removed": 0,
        "error": 0,
    }

    for repo in repos:
        if uninstall:
            result = uninstall_hook(repo, dry_run=dry_run)
        else:
            result = install_hook(repo, project_root, dry_run=dry_run)
        counts[result] = counts.get(result, 0) + 1

    # Summary
    print()
    if uninstall:
        print(
            f"[install_git_hook] Done. "
            f"Removed: {counts['removed'] + counts['empty_removed']}, "
            f"Not present: {counts['not_installed']}, "
            f"Errors: {counts['error']}"
        )
    else:
        print(
            f"[install_git_hook] Done. "
            f"Installed: {counts['installed']}, "
            f"Already present: {counts['already_installed']}, "
            f"Errors: {counts['error']}"
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "DayTracker: install or remove post-commit hooks in all git "
            "repositories under configured watch_roots."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/install_git_hook.py\n"
            "  python scripts/install_git_hook.py --dry-run\n"
            "  python scripts/install_git_hook.py --uninstall\n"
            "  python scripts/install_git_hook.py --uninstall --dry-run"
        ),
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove DayTracker hook from all repos instead of installing.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making any changes.",
    )
    args = parser.parse_args()

    try:
        run(uninstall=args.uninstall, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001
        print(f"[install_git_hook] FATAL: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
