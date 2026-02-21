"""
scripts/obsidian/writer.py - Helper utilities for writing Obsidian vault notes.

Provides two key functions:
    write_note()    - Write a note file to the vault (with overwrite control)
    update_section() - Replace content under a specific ## heading

Usage:
    from scripts.obsidian.writer import write_note, update_section
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

# Windows console UTF-8 (only wrap if running as __main__)
if sys.platform == "win32" and __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def write_note(
    vault_path: str,
    relative_path: str,
    content: str,
    overwrite: bool = False,
) -> bool:
    """
    Write a note to the Obsidian vault.

    Parameters
    ----------
    vault_path:
        Absolute path to the vault root directory.
    relative_path:
        Path relative to the vault root (e.g. "Daily/2026-02-21.md").
    content:
        Full markdown content to write.
    overwrite:
        If False (default), skip writing if the file already exists.
        If True, overwrite an existing file completely.

    Returns
    -------
    bool
        True if the file was written, False if skipped (already exists).
    """
    target = Path(vault_path) / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and not overwrite:
        return False

    target.write_text(content, encoding="utf-8")
    return True


def update_section(
    vault_path: str,
    relative_path: str,
    section_header: str,
    new_content: str,
) -> bool:
    """
    Replace the content under a specific ## heading in an existing note.

    Finds the first occurrence of `section_header` (e.g. "## 타임라인") and
    replaces everything from that heading up to (but not including) the next
    heading of the same or higher level with `new_content`.

    If the section is not found, appends it to the end of the file.
    If the file does not exist, creates it with just the section content.

    Parameters
    ----------
    vault_path:
        Absolute path to the vault root directory.
    relative_path:
        Path relative to the vault root.
    section_header:
        The exact heading text to find, e.g. "## 타임라인".
    new_content:
        The full replacement content for that section (including the header line).

    Returns
    -------
    bool
        True if the file was written/updated, False on error.
    """
    target = Path(vault_path) / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)

    # Determine heading level from the header marker
    header_match = re.match(r"^(#{1,6})\s", section_header)
    if not header_match:
        print(
            f"[writer] WARNING: section_header '{section_header}' does not start "
            "with a Markdown heading marker (#).",
            file=sys.stderr,
        )
        return False
    level = len(header_match.group(1))  # number of '#' chars

    # Build regex: match section_header line then everything until next heading
    # of same or higher level (fewer or equal '#' symbols), or end of file.
    # The replacement heading must match at the start of a line.
    escaped_header = re.escape(section_header)
    # Pattern: the header line, then any content that doesn't start a same/higher heading
    same_or_higher = "#" * level  # e.g. "##" for level 2 means stop at ## or #
    # We stop at a heading line that has 1..level '#' characters
    stop_pattern = r"(?=^#{1," + str(level) + r"} )"

    section_pattern = re.compile(
        r"^" + escaped_header + r".*?(?=" + stop_pattern + r"|\Z)",
        re.MULTILINE | re.DOTALL,
    )

    if not target.exists():
        # Create new file with just this section
        target.write_text(new_content + "\n", encoding="utf-8")
        return True

    existing = target.read_text(encoding="utf-8")

    if section_pattern.search(existing):
        # Ensure new_content ends with exactly one newline before the next section.
        # Use re.escape on the replacement to avoid interpreting backslash sequences
        # in the content (e.g. Windows paths like C:\MYCLAUDE_PROJECT contain \B, \M).
        replacement_raw = new_content.rstrip("\n") + "\n\n"
        # Split at match boundary manually to avoid re.sub replacement escaping issues
        match = section_pattern.search(existing)
        if match:
            updated = existing[:match.start()] + replacement_raw + existing[match.end():]
        else:
            updated = existing
    else:
        # Section not found: append to end
        updated = existing.rstrip("\n") + "\n\n" + new_content + "\n"

    target.write_text(updated, encoding="utf-8")
    return True


def read_note(vault_path: str, relative_path: str) -> str | None:
    """
    Read a note from the vault.  Returns None if it does not exist.
    """
    target = Path(vault_path) / relative_path
    if not target.exists():
        return None
    return target.read_text(encoding="utf-8")
