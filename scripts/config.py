"""
scripts/config.py - DayTracker configuration loader.

Loads config.yaml from the project root.
If config.yaml is missing, copies config.example.yaml -> config.yaml and
prints setup guidance.
Supports .env overrides for sensitive values.

Usage:
    from scripts.config import Config
    c = Config()
    vault = c.get_vault_path()
    db    = c.get_db_path()
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Helper: find project root
# ---------------------------------------------------------------------------

def _find_project_root() -> Path:
    """
    Walk upward from the current working directory to find the project root.
    The project root is identified by the presence of config.yaml or
    config.example.yaml.  Falls back to the directory that contains the
    scripts/ package if neither marker is found.
    """
    # Start from this file's parent's parent (i.e. the repo root)
    candidate = Path(__file__).resolve().parent.parent
    return candidate


# ---------------------------------------------------------------------------
# Config class
# ---------------------------------------------------------------------------

class Config:
    """
    Loads and exposes DayTracker configuration values.

    Priority (highest first):
    1. Environment variables / .env file
    2. config.yaml values

    Example:
        c = Config()
        print(c.get_vault_path())
        print(c.get_db_path())
    """

    def __init__(self, project_root: str | Path | None = None) -> None:
        self._root = Path(project_root) if project_root else _find_project_root()
        self._data: dict[str, Any] = {}
        self._load_env()
        self._load_yaml()

    # ------------------------------------------------------------------
    # Internal loaders
    # ------------------------------------------------------------------

    def _load_env(self) -> None:
        """Load .env file if present (silently skip if python-dotenv missing)."""
        env_path = self._root / ".env"
        try:
            from dotenv import load_dotenv  # type: ignore
            if env_path.exists():
                load_dotenv(env_path)
        except ImportError:
            pass  # python-dotenv not installed; env vars only from OS

    def _load_yaml(self) -> None:
        """Load config.yaml.  If absent, copy from config.example.yaml."""
        config_path = self._root / "config.yaml"
        example_path = self._root / "config.example.yaml"

        if not config_path.exists():
            if example_path.exists():
                shutil.copy(example_path, config_path)
                print(
                    "[DayTracker] config.yaml not found.\n"
                    f"  Copied config.example.yaml -> {config_path}\n"
                    "  Please open config.yaml and set your vault_path, "
                    "watch_roots, and other values.\n"
                    "  Then re-run this script.",
                    file=sys.stderr,
                )
            else:
                print(
                    "[DayTracker] WARNING: Neither config.yaml nor "
                    "config.example.yaml found. Using defaults.",
                    file=sys.stderr,
                )
            self._data = {}
            return

        try:
            import yaml  # type: ignore
            with open(config_path, "r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
            self._data = loaded
        except ImportError:
            print(
                "[DayTracker] WARNING: pyyaml is not installed. "
                "Run: pip install pyyaml",
                file=sys.stderr,
            )
            self._data = {}
        except Exception as exc:  # noqa: BLE001
            print(
                f"[DayTracker] WARNING: Failed to parse config.yaml: {exc}",
                file=sys.stderr,
            )
            self._data = {}

    # ------------------------------------------------------------------
    # Low-level accessor
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """
        Return a top-level config value.
        Environment variables override YAML values.
        The env var name is the key uppercased, e.g. VAULT_PATH.
        """
        env_key = key.upper()
        env_val = os.environ.get(env_key)
        if env_val is not None:
            return env_val
        return self._data.get(key, default)

    def get_nested(self, *keys: str, default: Any = None) -> Any:
        """Return a nested config value by successive key lookup."""
        node = self._data
        for k in keys:
            if not isinstance(node, dict):
                return default
            node = node.get(k, None)
            if node is None:
                return default
        return node

    # ------------------------------------------------------------------
    # Typed properties / methods
    # ------------------------------------------------------------------

    @property
    def vault_path(self) -> str:
        return self.get("vault_path", "")

    @property
    def watch_roots(self) -> list[str]:
        val = self.get("watch_roots", [])
        if isinstance(val, list):
            return val
        if isinstance(val, str):
            return [val]
        return []

    @property
    def exclude_patterns(self) -> list[str]:
        val = self.get("exclude_patterns", [])
        if isinstance(val, list):
            return val
        return []

    @property
    def claude_history_path(self) -> str:
        return self.get("claude_history_path", "")

    @property
    def daily_summary_time(self) -> str:
        return self.get("daily_summary_time", "23:55")

    @property
    def sensitive_patterns(self) -> list[str]:
        val = self.get("sensitive_patterns", [])
        if isinstance(val, list):
            return val
        return []

    @property
    def obsidian_api(self) -> dict[str, Any]:
        val = self._data.get("obsidian_api", {})
        if not isinstance(val, dict):
            return {}
        # Allow env overrides for api_key
        api_key_env = os.environ.get("OBSIDIAN_API_KEY")
        if api_key_env:
            val = dict(val)
            val["api_key"] = api_key_env
        return val

    # ------------------------------------------------------------------
    # Public helper methods
    # ------------------------------------------------------------------

    def get_vault_path(self) -> str:
        """
        Return the configured vault_path.
        Raises RuntimeError with a clear message if vault_path is not set.
        """
        vp = self.vault_path.strip()
        if not vp:
            raise RuntimeError(
                "[DayTracker] vault_path is not set in config.yaml.\n"
                "  Run: python scripts/setup_vault.py --vault-path "
                '"C:/path/to/your/vault"'
            )
        return vp

    def get_claude_history_path(self) -> str:
        """
        Return the configured claude_history_path, or auto-detect
        ~/.claude/projects/ if the config value is empty.
        """
        configured = self.claude_history_path.strip()
        if configured:
            return configured

        # Auto-detect
        home = Path.home()
        candidates = [
            home / ".claude" / "projects",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)

        # Return the conventional path even if it doesn't exist yet
        return str(home / ".claude" / "projects")

    def get_db_path(self) -> str:
        """Return the absolute path to worklog.db inside the data/ directory."""
        return str(self._root / "data" / "worklog.db")

    def get_project_root(self) -> str:
        """Return the absolute path to the project root."""
        return str(self._root)

    def save_vault_path(self, vault_path: str) -> None:
        """
        Persist vault_path into config.yaml.
        Updates only the vault_path key; preserves all other content.
        """
        config_path = self._root / "config.yaml"
        try:
            import yaml  # type: ignore
        except ImportError:
            print(
                "[DayTracker] ERROR: pyyaml is required to save config. "
                "Run: pip install pyyaml",
                file=sys.stderr,
            )
            return

        # Read current content
        current: dict[str, Any] = {}
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as fh:
                    current = yaml.safe_load(fh) or {}
            except Exception:  # noqa: BLE001
                pass

        current["vault_path"] = vault_path

        try:
            with open(config_path, "w", encoding="utf-8") as fh:
                yaml.dump(current, fh, default_flow_style=False, allow_unicode=True)
            # Update in-memory cache too
            self._data = current
            print(f"[DayTracker] vault_path saved to {config_path}")
        except Exception as exc:  # noqa: BLE001
            print(
                f"[DayTracker] ERROR: Could not write config.yaml: {exc}",
                file=sys.stderr,
            )

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Config(root={self._root}, "
            f"vault_path={self.vault_path!r}, "
            f"watch_roots={self.watch_roots!r})"
        )
