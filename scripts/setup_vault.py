"""
scripts/setup_vault.py - Set up the Obsidian vault for DayTracker.

Creates the vault directory structure and copies vault-templates/ contents.
Saves vault_path to config.yaml.

Usage:
    python scripts/setup_vault.py --vault-path "C:/path/to/vault"
    python scripts/setup_vault.py            # interactive prompt
    python -m scripts.setup_vault --vault-path "C:/path/to/vault"
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


# Vault subdirectories to create
VAULT_SUBDIRS = [
    "Daily",
    "Projects",
    "AI-Sessions",
    "Templates",
]


# ---------------------------------------------------------------------------
# Core setup function
# ---------------------------------------------------------------------------

def setup_vault(vault_path: str | Path) -> bool:
    """
    Create the vault directory structure and copy templates.

    Parameters
    ----------
    vault_path:
        Absolute (or expandable) path where the vault should be created.

    Returns
    -------
    bool
        True if setup completed successfully, False on error.
    """
    vault = Path(vault_path).expanduser().resolve()

    # --- Create vault root ---
    try:
        vault.mkdir(parents=True, exist_ok=True)
        print(f"[setup_vault] Vault root: {vault}")
    except OSError as exc:
        print(f"[setup_vault] ERROR: Cannot create vault at {vault}: {exc}", file=sys.stderr)
        return False

    # --- Create subdirectories ---
    for subdir in VAULT_SUBDIRS:
        sub_path = vault / subdir
        try:
            sub_path.mkdir(exist_ok=True)
            print(f"[setup_vault]   Created: {sub_path.name}/")
        except OSError as exc:
            print(
                f"[setup_vault] WARNING: Could not create {subdir}/: {exc}",
                file=sys.stderr,
            )

    # --- Copy vault-templates/ contents ---
    project_root = Path(__file__).resolve().parent.parent
    templates_src = project_root / "vault-templates"

    if templates_src.exists() and templates_src.is_dir():
        _copy_templates(templates_src, vault)
    else:
        print(
            f"[setup_vault] INFO: vault-templates/ not found at {templates_src} "
            "- skipping template copy.",
        )

    # --- Save vault_path to config.yaml ---
    _save_vault_path_to_config(str(vault), project_root)

    # --- Success message ---
    print()
    print("=" * 60)
    print("[setup_vault] Vault setup complete!")
    print(f"  Vault location: {vault}")
    print()
    print("Next steps:")
    print("  1. Open Obsidian")
    print('  2. Click "Open folder as vault"')
    print(f'  3. Select: {vault}')
    print("  4. Install recommended plugins:")
    print("       Dataview, Templater, Periodic Notes, Local REST API")
    print("  5. Run: python scripts/init_db.py")
    print("  6. Run: python scripts/watcher_daemon.py")
    print("=" * 60)

    return True


# ---------------------------------------------------------------------------
# Template copying
# ---------------------------------------------------------------------------

def _copy_templates(src_dir: Path, vault: Path) -> None:
    """
    Recursively copy vault-templates/ contents to the vault.
    Skips files that already exist (does not overwrite).
    """
    copied = 0
    skipped = 0

    for src_file in src_dir.rglob("*"):
        # Skip .obsidian/ inside vault-templates (user-specific settings)
        rel_parts = src_file.relative_to(src_dir).parts
        if ".obsidian" in rel_parts:
            continue

        rel_path = src_file.relative_to(src_dir)
        dest_file = vault / rel_path

        if src_file.is_dir():
            dest_file.mkdir(parents=True, exist_ok=True)
            continue

        if dest_file.exists():
            print(f"[setup_vault]   Skip (exists): {rel_path}")
            skipped += 1
        else:
            try:
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dest_file)
                print(f"[setup_vault]   Copied: {rel_path}")
                copied += 1
            except OSError as exc:
                print(
                    f"[setup_vault] WARNING: Could not copy {rel_path}: {exc}",
                    file=sys.stderr,
                )

    print(
        f"[setup_vault] Templates: {copied} copied, {skipped} skipped (already exist)."
    )


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------

def _save_vault_path_to_config(vault_path: str, project_root: Path) -> None:
    """Write vault_path into config.yaml using the Config class."""
    try:
        # Import here so this module can be used even before sys.path is set
        sys.path.insert(0, str(project_root))
        from scripts.config import Config  # type: ignore

        cfg = Config(project_root=project_root)
        cfg.save_vault_path(vault_path)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[setup_vault] WARNING: Could not save vault_path to config.yaml: {exc}",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set up the DayTracker Obsidian vault.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  python scripts/setup_vault.py --vault-path "C:/Users/me/Obsidian/DayTracker"\n'
            "  python scripts/setup_vault.py          # interactive prompt"
        ),
    )
    parser.add_argument(
        "--vault-path",
        metavar="PATH",
        help="Absolute path where the vault should be created.",
        default=None,
    )
    args = parser.parse_args()

    vault_path = args.vault_path

    # Interactive fallback
    if not vault_path:
        print("[setup_vault] No --vault-path provided.")
        vault_path = input(
            "  Enter the full path where you want to create your Obsidian vault\n"
            '  (e.g. C:/Users/yourname/Obsidian/DayTracker): '
        ).strip()

    if not vault_path:
        print("[setup_vault] ERROR: No vault path provided. Aborting.", file=sys.stderr)
        sys.exit(1)

    ok = setup_vault(vault_path)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
