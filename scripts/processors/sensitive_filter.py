"""
scripts/processors/sensitive_filter.py - Enhanced sensitive information filtering.

Multi-pass sensitive data masker that combines built-in patterns (API keys,
passwords, tokens, private keys, connection strings) with user-defined patterns
from config.yaml.

Usage:
    # As a module:
    from scripts.processors.sensitive_filter import SensitiveFilter
    f = SensitiveFilter()
    masked_text, what_was_masked = f.mask("Bearer sk-abc123...")

    # CLI: mask stdin or a literal --text argument
    python scripts/processors/sensitive_filter.py --text "some text"

    # Scan the worklog.db for any sensitive data leaks
    python scripts/processors/sensitive_filter.py --scan-db

    # Clean sensitive data from the DB (with --dry-run preview first)
    python scripts/processors/sensitive_filter.py --clean-db [--dry-run]
"""

from __future__ import annotations

import argparse
import io
import re
import sqlite3
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Windows UTF-8 stdout
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    if hasattr(sys.stdout, "buffer") and not getattr(sys.stdout, "_daytracker_wrapped", False):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stdout._daytracker_wrapped = True  # type: ignore[attr-defined]
    if hasattr(sys.stderr, "buffer") and not getattr(sys.stderr, "_daytracker_wrapped", False):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
        sys.stderr._daytracker_wrapped = True  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
_SCRIPTS_DIR = _SCRIPT_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# SensitiveFilter
# ---------------------------------------------------------------------------

class SensitiveFilter:
    """
    Multi-pass sensitive information masker.

    Built-in patterns are always applied regardless of config.  Extra patterns
    (from config.yaml ``sensitive_patterns``) are compiled and layered on top.

    Each pattern entry in BUILTIN_PATTERNS is a tuple of:
        (regex_pattern, replacement_string, label)

    The ``label`` is included in the list returned by :meth:`mask` so callers
    know *what kind* of secret was found without revealing the actual value.
    """

    # ------------------------------------------------------------------
    # Built-in patterns (always applied)
    # ------------------------------------------------------------------
    BUILTIN_PATTERNS: list[tuple[str, str, str]] = [
        # OpenAI API keys
        (r'sk-[a-zA-Z0-9]{20,}', '[OPENAI_KEY]', 'OPENAI_KEY'),
        # Google / Firebase API keys
        (r'AIza[a-zA-Z0-9\-_]{20,}', '[GOOGLE_KEY]', 'GOOGLE_KEY'),
        # GitHub Personal Access Tokens
        (r'ghp_[a-zA-Z0-9]{20,}', '[GITHUB_PAT]', 'GITHUB_PAT'),
        # GitHub OAuth tokens
        (r'gho_[a-zA-Z0-9]{36}', '[GITHUB_OAUTH]', 'GITHUB_OAUTH'),
        # Slack Bot tokens
        (r'xoxb-[0-9\-a-zA-Z]{50,}', '[SLACK_BOT]', 'SLACK_BOT'),
        # Slack User tokens
        (r'xoxp-[0-9\-a-zA-Z]{50,}', '[SLACK_USER]', 'SLACK_USER'),
        # AWS Access Key IDs
        (r'AKIA[0-9A-Z]{16}', '[AWS_ACCESS_KEY]', 'AWS_ACCESS_KEY'),
        # Passwords  (password=..., password: ..., password "...")
        (
            r'(?i)password\s*[=:]\s*["\']?([^\s"\']{4,})["\']?',
            'password=[REDACTED]',
            'PASSWORD',
        ),
        # passwd
        (
            r'(?i)passwd\s*[=:]\s*["\']?([^\s"\']{4,})["\']?',
            'passwd=[REDACTED]',
            'PASSWD',
        ),
        # secrets
        (
            r'(?i)secret\s*[=:]\s*["\']?([^\s"\']{8,})["\']?',
            'secret=[REDACTED]',
            'SECRET',
        ),
        # Generic tokens (token=..., token: ...)
        (
            r'(?i)token\s*[=:]\s*["\']?([a-zA-Z0-9_\-]{20,})["\']?',
            'token=[REDACTED]',
            'TOKEN',
        ),
        # Bearer tokens in Authorization headers
        (
            r'Bearer\s+[a-zA-Z0-9\-._~+/]+=*',
            'Bearer [REDACTED]',
            'BEARER_TOKEN',
        ),
        # PEM private keys (RSA, EC, generic)
        (
            r'-----BEGIN (?:RSA |EC )?PRIVATE KEY-----[\s\S]+?-----END (?:RSA |EC )?PRIVATE KEY-----',
            '[PRIVATE_KEY]',
            'PRIVATE_KEY',
        ),
        # Database / broker connection strings
        (
            r'(?i)(?:mysql|postgresql|mongodb|redis|amqp)://[^\s@]+@[^\s]+',
            '[DB_CONNECTION_STRING]',
            'DB_CONNECTION_STRING',
        ),
    ]

    def __init__(self, extra_patterns: Optional[list[str]] = None) -> None:
        """
        Args:
            extra_patterns: Additional regex patterns (strings only; replacement
                            will always be ``[REDACTED]``).  Typically from
                            ``config.yaml`` ``sensitive_patterns``.
        """
        # Pre-compile built-in patterns
        self._compiled: list[tuple[re.Pattern, str, str]] = []
        for pattern, replacement, label in self.BUILTIN_PATTERNS:
            try:
                self._compiled.append((re.compile(pattern), replacement, label))
            except re.error as exc:
                print(
                    f"[SensitiveFilter] WARNING: could not compile built-in pattern "
                    f"{pattern!r}: {exc}",
                    file=sys.stderr,
                )

        # Extra patterns from config (label = the raw pattern string)
        for raw in (extra_patterns or []):
            try:
                self._compiled.append(
                    (re.compile(raw, re.IGNORECASE), '[REDACTED]', f'CUSTOM:{raw[:40]}')
                )
            except re.error as exc:
                print(
                    f"[SensitiveFilter] WARNING: could not compile config pattern "
                    f"{raw!r}: {exc}",
                    file=sys.stderr,
                )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def mask(self, text: str) -> tuple[str, list[str]]:
        """
        Apply all patterns to *text* and return ``(masked_text, labels_found)``.

        ``labels_found`` is a list of pattern labels (e.g. ``'OPENAI_KEY'``)
        that matched.  The actual secret values are never included.

        Args:
            text: The input string to sanitise.

        Returns:
            A tuple ``(masked_text, labels_found)``.
        """
        if not text:
            return text, []

        masked = text
        found: list[str] = []

        for compiled_re, replacement, label in self._compiled:
            new_text, count = compiled_re.subn(replacement, masked)
            if count:
                masked = new_text
                found.append(label)

        return masked, found

    def scan_text(self, text: str) -> list[dict]:
        """
        Return audit information about sensitive data found in *text*.

        Does **not** modify the text.  Returns a list of dicts:
            ``{pattern: str, label: str, match_preview: str}``

        ``match_preview`` shows only the first 20 characters of each match,
        followed by ``...`` if truncated.

        Args:
            text: The string to scan.

        Returns:
            A list of match info dicts (empty if nothing found).
        """
        if not text:
            return []

        results = []
        for compiled_re, _replacement, label in self._compiled:
            for match in compiled_re.finditer(text):
                raw = match.group(0)
                preview = raw[:20] + ("..." if len(raw) > 20 else "")
                results.append({
                    "label": label,
                    "match_preview": preview,
                })
        return results

    def scan_db(self, db_path: str) -> dict:
        """
        Scan the ``ai_prompts`` table for potential sensitive data leaks.

        Checks ``prompt_text`` and ``response_text`` columns.

        Args:
            db_path: Filesystem path to ``worklog.db``.

        Returns:
            A dict ``{table: [{id, column, label, preview}]}``
            where *table* is the table name (currently ``"ai_prompts"``).
        """
        results: dict[str, list[dict]] = {"ai_prompts": []}

        if not Path(db_path).exists():
            print(f"[SensitiveFilter] DB not found: {db_path}", file=sys.stderr)
            return results

        try:
            conn = sqlite3.connect(db_path, timeout=10)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error as exc:
            print(f"[SensitiveFilter] Cannot open DB: {exc}", file=sys.stderr)
            return results

        try:
            # Check which columns exist
            cursor = conn.execute("PRAGMA table_info(ai_prompts)")
            existing_cols = {row["name"] for row in cursor.fetchall()}
            text_cols = [c for c in ("prompt_text", "response_text") if c in existing_cols]

            if not text_cols:
                return results

            rows = conn.execute(
                f"SELECT id, {', '.join(text_cols)} FROM ai_prompts"
            ).fetchall()

            for row in rows:
                row_id = row["id"]
                for col in text_cols:
                    cell = row[col] or ""
                    findings = self.scan_text(cell)
                    for finding in findings:
                        results["ai_prompts"].append({
                            "id": row_id,
                            "column": col,
                            "label": finding["label"],
                            "preview": finding["match_preview"],
                        })
        except sqlite3.Error as exc:
            print(f"[SensitiveFilter] DB scan error: {exc}", file=sys.stderr)
        finally:
            conn.close()

        return results

    def clean_db(self, db_path: str, dry_run: bool = False) -> int:
        """
        Replace sensitive values in ``ai_prompts`` with their masked equivalents.

        Args:
            db_path: Filesystem path to ``worklog.db``.
            dry_run: If True, print what *would* be changed without writing.

        Returns:
            The number of rows updated (0 in dry-run mode, or if no changes).
        """
        if not Path(db_path).exists():
            print(f"[SensitiveFilter] DB not found: {db_path}", file=sys.stderr)
            return 0

        try:
            conn = sqlite3.connect(db_path, timeout=10)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error as exc:
            print(f"[SensitiveFilter] Cannot open DB: {exc}", file=sys.stderr)
            return 0

        updated = 0
        try:
            # Determine available text columns
            cursor = conn.execute("PRAGMA table_info(ai_prompts)")
            existing_cols = {row["name"] for row in cursor.fetchall()}
            text_cols = [c for c in ("prompt_text", "response_text") if c in existing_cols]

            if not text_cols:
                return 0

            rows = conn.execute(
                f"SELECT id, {', '.join(text_cols)} FROM ai_prompts"
            ).fetchall()

            for row in rows:
                row_id = row["id"]
                updates: dict[str, str] = {}
                for col in text_cols:
                    original = row[col] or ""
                    masked, found = self.mask(original)
                    if found:
                        updates[col] = masked
                        if dry_run:
                            labels = ", ".join(found)
                            print(
                                f"  [DRY-RUN] ai_prompts id={row_id} "
                                f"column={col}: would mask [{labels}]"
                            )

                if updates and not dry_run:
                    set_clause = ", ".join(f"{col}=?" for col in updates)
                    conn.execute(
                        f"UPDATE ai_prompts SET {set_clause} WHERE id=?",
                        [*updates.values(), row_id],
                    )
                    updated += 1

            if not dry_run:
                conn.commit()

        except sqlite3.Error as exc:
            print(f"[SensitiveFilter] DB clean error: {exc}", file=sys.stderr)
        finally:
            conn.close()

        return updated


# ---------------------------------------------------------------------------
# Module-level factory (loads config automatically)
# ---------------------------------------------------------------------------

def _make_filter() -> SensitiveFilter:
    """Create a SensitiveFilter pre-loaded with config.yaml patterns."""
    try:
        from config import Config  # type: ignore
        cfg = Config()
        return SensitiveFilter(extra_patterns=cfg.sensitive_patterns)
    except Exception:
        return SensitiveFilter()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DayTracker sensitive information filter utility"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--text",
        metavar="TEXT",
        help="Mask sensitive data in the given text and print the result.",
    )
    group.add_argument(
        "--scan-db",
        action="store_true",
        help="Scan worklog.db ai_prompts table for sensitive data leaks.",
    )
    group.add_argument(
        "--clean-db",
        action="store_true",
        help="Mask sensitive values in worklog.db ai_prompts (destructive).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --clean-db: show what would change without writing.",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        help="Override path to worklog.db (default: data/worklog.db).",
    )
    return parser


def _get_db_path(override: Optional[str]) -> str:
    if override:
        return override
    try:
        from config import Config  # type: ignore
        return Config().get_db_path()
    except Exception:
        return str(_PROJECT_ROOT / "data" / "worklog.db")


def run_cli() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    sf = _make_filter()

    if args.text:
        masked, found = sf.mask(args.text)
        print("Masked text:")
        print(masked)
        if found:
            print(f"\nPatterns triggered: {', '.join(found)}")
        else:
            print("\nNo sensitive patterns detected.")
        return

    db_path = _get_db_path(getattr(args, "db", None))

    if args.scan_db:
        print(f"Scanning DB: {db_path}")
        findings = sf.scan_db(db_path)
        total = sum(len(v) for v in findings.values())
        if total == 0:
            print("No sensitive data found in ai_prompts.")
        else:
            print(f"Found {total} potential leak(s):\n")
            for table, rows in findings.items():
                for row in rows:
                    print(
                        f"  {table} id={row['id']} column={row['column']}: "
                        f"[{row['label']}] preview={row['preview']!r}"
                    )
        return

    if args.clean_db:
        mode = "DRY-RUN" if args.dry_run else "LIVE"
        print(f"Cleaning DB [{mode}]: {db_path}")
        updated = sf.clean_db(db_path, dry_run=args.dry_run)
        if args.dry_run:
            print("(No changes written — dry-run mode)")
        else:
            print(f"Updated {updated} row(s).")
        return


if __name__ == "__main__":
    run_cli()
