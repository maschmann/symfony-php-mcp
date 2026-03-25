"""
Tool: read_code_context

Reads a PHP (or any text) file from the project and optionally strips
doc-comment blocks to reduce token usage when passing the code to an LLM.

Security: the resolved path is checked to remain inside project_root,
preventing path-traversal attacks via the MCP interface.
"""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Regex patterns for comment stripping
# ---------------------------------------------------------------------------

# PHPDoc / block comments: /** ... */ and /* ... */
# Using re.DOTALL so `.` matches newlines inside the block.
_RE_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

# Single-line // comments
_RE_LINE_COMMENT = re.compile(r"//.*?$", re.MULTILINE)

# Blank lines left after stripping
_RE_MULTI_BLANK = re.compile(r"\n{3,}")

# Max file size to read (bytes) – prevents accidentally loading huge files
_MAX_FILE_BYTES = 500_000  # 500 KB


def read_code_context(
    project_root: Path,
    file_path: str,
    strip_doc_comments: bool = True,
    strip_line_comments: bool = False,
) -> str:
    """Read a file from the project, optionally stripping comments.

    Args:
        project_root:        Absolute path to the Symfony project root.
        file_path:           Path to the file, relative to *project_root*
                             (e.g. ``src/Controller/UserController.php``).
                             Absolute paths are also accepted if they fall
                             inside *project_root*.
        strip_doc_comments:  Remove ``/** ... */`` and ``/* ... */`` blocks.
                             Defaults to True – these are usually boilerplate
                             and consume many tokens.
        strip_line_comments: Additionally remove ``//`` single-line comments.
                             Defaults to False – inline comments are often
                             useful context for the LLM.

    Returns:
        The (possibly stripped) file content with 1-indexed line numbers,
        plus a brief token-savings report at the top.
    """
    if not file_path.strip():
        return "**Error:** `file_path` must not be empty."

    # ------------------------------------------------------------------ #
    # 1. Resolve and validate path
    # ------------------------------------------------------------------ #
    try:
        resolved = _resolve_path(project_root, file_path)
    except ValueError as exc:
        return f"**Security error:** {exc}"

    if not resolved.exists():
        suggestions = _find_similar_files(project_root, file_path)
        msg = f"**File not found:** `{file_path}` (resolved to `{resolved}`).\n"
        if suggestions:
            msg += "\n**Did you mean one of these?**\n"
            msg += "\n".join(f"- `{s}`" for s in suggestions[:8])
        return msg

    if not resolved.is_file():
        return f"**Error:** `{resolved}` is not a file (it may be a directory)."

    # ------------------------------------------------------------------ #
    # 2. Size guard
    # ------------------------------------------------------------------ #
    size = resolved.stat().st_size
    if size > _MAX_FILE_BYTES:
        return (
            f"**File too large to read:** `{file_path}` is {size:,} bytes "
            f"({size // 1024} KB). Maximum is {_MAX_FILE_BYTES // 1024} KB.\n\n"
            "Use a more specific file path or view the file in sections."
        )

    # ------------------------------------------------------------------ #
    # 3. Read
    # ------------------------------------------------------------------ #
    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"**Error reading file:** {exc}"

    original_chars = len(content)
    original_lines = content.count("\n") + 1

    # ------------------------------------------------------------------ #
    # 4. Strip comments
    # ------------------------------------------------------------------ #
    if strip_doc_comments:
        content = _RE_BLOCK_COMMENT.sub("", content)

    if strip_line_comments:
        content = _RE_LINE_COMMENT.sub("", content)

    # Collapse runs of blank lines left by stripping
    if strip_doc_comments or strip_line_comments:
        content = _RE_MULTI_BLANK.sub("\n\n", content)
        content = content.strip()

    stripped_chars = len(content)
    stripped_lines = content.count("\n") + 1

    # ------------------------------------------------------------------ #
    # 5. Add line numbers
    # ------------------------------------------------------------------ #
    numbered = _add_line_numbers(content)

    # ------------------------------------------------------------------ #
    # 6. Header / summary
    # ------------------------------------------------------------------ #
    rel_path = resolved.relative_to(project_root)
    savings_pct = (
        round((1 - stripped_chars / original_chars) * 100)
        if original_chars > 0
        else 0
    )

    actions = []
    if strip_doc_comments:
        actions.append("block comments stripped")
    if strip_line_comments:
        actions.append("line comments stripped")
    actions_str = ", ".join(actions) if actions else "no stripping"

    header_lines = [
        f"## `{rel_path}`",
        "",
        f"| Property | Value |",
        f"|----------|-------|",
        f"| Original  | {original_lines} lines / {original_chars:,} chars |",
        f"| After processing | {stripped_lines} lines / {stripped_chars:,} chars |",
        f"| Token savings | ~{savings_pct}% ({actions_str}) |",
        "",
        "```php",
        numbered,
        "```",
    ]

    return "\n".join(header_lines)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _resolve_path(project_root: Path, file_path: str) -> Path:
    """Resolve *file_path* relative to *project_root* and validate it.

    Raises ValueError if the resolved path escapes *project_root*.
    """
    candidate = Path(file_path)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (project_root / candidate).resolve()

    # Ensure the path stays within the project root
    try:
        resolved.relative_to(project_root.resolve())
    except ValueError:
        raise ValueError(
            f"Path `{file_path}` resolves to `{resolved}`, "
            f"which is outside the project root `{project_root}`. "
            "Only files within the project may be read."
        )
    return resolved


def _add_line_numbers(content: str) -> str:
    """Prefix each line with a right-aligned line number."""
    lines = content.splitlines()
    width = len(str(len(lines)))
    return "\n".join(f"{i + 1:{width}d} | {line}" for i, line in enumerate(lines))


def _find_similar_files(project_root: Path, file_path: str) -> list[str]:
    """Return relative paths of files with a similar name as *file_path*."""
    target_name = Path(file_path).name
    if not target_name:
        return []

    results: list[str] = []
    # Only search inside src/ and a few other directories to keep it fast
    for search_dir in ("src", "templates", "config", "tests"):
        base = project_root / search_dir
        if not base.is_dir():
            continue
        for candidate in base.rglob(f"*{target_name}*"):
            if candidate.is_file():
                try:
                    results.append(str(candidate.relative_to(project_root)))
                except ValueError:
                    pass
        if len(results) >= 8:
            break
    return results
