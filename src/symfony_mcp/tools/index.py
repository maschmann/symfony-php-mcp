"""
Tools: build_index, find_symbol, search_code

build_index   – scan PHP files, extract symbols, persist to .symfony-mcp-index.json
find_symbol   – search the index for classes / interfaces / traits / methods by name
search_code   – live regex/substring search across project files (no index required)
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Optional

from ..config import ServerConfig
from ..indexer import ClassSymbol, MethodSymbol, SymbolIndex

# ---------------------------------------------------------------------------
# build_index
# ---------------------------------------------------------------------------

def build_index(
    index: SymbolIndex,
    config: ServerConfig,
    directories: Optional[list[str]] = None,
    force: bool = False,
) -> str:
    """Scan PHP files and build/update the symbol index.

    The index is written to <project_root>/.symfony-mcp-index.json.
    Add that file to .gitignore if you don't want it committed.
    """
    dirs = directories or _default_scan_dirs(config.project_root)

    if not dirs:
        return (
            "**No scannable directories found.**\n\n"
            "Expected at least one of: `src/`, `app/`, `lib/`.\n"
            f"Project root: `{config.project_root}`"
        )

    t0 = time.monotonic()
    stats = index.build(directories=dirs, force=force)
    elapsed = time.monotonic() - t0

    index.save()

    totals = index.stats()
    lines = [
        f"## Index {'rebuilt' if force else 'updated'} in {elapsed:.2f}s",
        "",
        "### Scan summary",
        f"| Stat | Value |",
        f"|------|-------|",
        f"| Directories scanned | {', '.join(f'`{d}`' for d in dirs)} |",
        f"| Files visited | {stats['scanned']} |",
        f"| Files updated | {stats['updated']} |",
        f"| Files skipped (unchanged) | {stats['skipped']} |",
        f"| Errors | {stats['errors']} |",
        "",
        "### Index totals",
        f"| Stat | Count |",
        f"|------|-------|",
        f"| PHP files indexed | {totals['files']} |",
        f"| Classes / interfaces / traits / enums | {totals['classes']} |",
        f"| Methods | {totals['methods']} |",
        "",
        f"Index saved to `{index.index_path.relative_to(config.project_root)}`.",
    ]

    if stats["errors"]:
        lines += [
            "",
            f"> **{stats['errors']} file(s) failed to parse** — "
            "they may contain syntax errors or be unreadable.",
        ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# find_symbol
# ---------------------------------------------------------------------------

def find_symbol(
    index: SymbolIndex,
    name: str,
    kind: Optional[str] = None,
) -> str:
    """Search the symbol index for a class, interface, trait, enum, or method.

    Returns file paths and line numbers so you can immediately call
    read_code_context with the result.
    """
    if not index.files:
        return (
            "**Index is empty.** Run `build_index` first to scan the project.\n\n"
            "The index only needs to be built once; it updates incrementally after that."
        )

    if not name.strip():
        return "**Error:** `name` must not be empty."

    results = index.find_symbol(name, kind=kind or None)

    if not results:
        totals = index.stats()
        return (
            f"No symbols matched `{name}`"
            + (f" (kind: `{kind}`)" if kind else "")
            + f".\n\n"
            f"The index contains {totals['classes']} classes and "
            f"{totals['methods']} methods across {totals['files']} files.\n\n"
            "**Tips:**\n"
            "- Try a shorter name (e.g. `User` instead of `UserRepository`)\n"
            "- Omit the `kind` filter to search all symbol types\n"
            "- Run `build_index` if the project has changed recently"
        )

    # Separate class-level and method-level results
    class_results = [r for r in results if isinstance(r, ClassSymbol)]
    method_results = [r for r in results if isinstance(r, MethodSymbol)]

    lines: list[str] = [
        f"Found **{len(results)}** symbol(s) matching `{name}`"
        + (f" (kind: `{kind}`)" if kind else ""),
        "",
    ]

    # ---- Class / interface / trait / enum results ----
    if class_results:
        lines += [
            f"### Classes / Interfaces / Traits / Enums ({len(class_results)})",
            "",
            "| Name | Kind | File | Line | Extends | Implements |",
            "|------|------|------|------|---------|------------|",
        ]
        for cls in class_results[:50]:
            ext = ", ".join(cls.extends) or "—"
            impl = ", ".join(cls.implements) or "—"
            mods = " ".join(cls.modifiers)
            kind_str = f"{mods} {cls.kind}".strip()
            lines.append(
                f"| `{cls.name}` | {kind_str} | `{cls.file}:{cls.line}` "
                f"| {cls.line} | `{ext}` | `{impl}` |"
            )

        if len(class_results) > 50:
            lines.append(f"\n> *(showing 50 of {len(class_results)} — narrow your search)*")

        # Detailed view for small result sets
        if len(class_results) <= 3:
            for cls in class_results:
                lines += _format_class_detail(cls)

    # ---- Method results ----
    if method_results:
        lines += [
            "",
            f"### Methods ({len(method_results)})",
            "",
            "| Method | Visibility | File | Line | Route |",
            "|--------|------------|------|------|-------|",
        ]
        for method in method_results[:50]:
            static_flag = " static" if method.is_static else ""
            abstract_flag = " abstract" if method.is_abstract else ""
            visibility = f"{method.visibility}{static_flag}{abstract_flag}"
            route = f"`{method.route_path}`" if method.route_path else "—"
            # Extract file from FQN
            file_info = _fqn_to_file(method.fqn, index)
            lines.append(
                f"| `{method.name}` | {visibility} | {file_info} | {method.line} | {route} |"
            )

        if len(method_results) > 50:
            lines.append(f"\n> *(showing 50 of {len(method_results)})*")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# search_code
# ---------------------------------------------------------------------------

def search_code(
    project_root: Path,
    pattern: str,
    path_glob: str = "**/*.php",
    context_lines: int = 2,
    max_results: int = 50,
) -> str:
    """Live regex/substring search across project files.

    No index required — scans files directly. Fast for typical Symfony projects.

    Args:
        pattern:       Regex pattern or plain substring to search for.
        path_glob:     Glob relative to project_root. Default: **/*.php
                       Examples: src/**/*.php, templates/**/*.twig, **/*.yaml
        context_lines: Lines of context to show around each match (0-5).
        max_results:   Maximum number of matching snippets to return.
    """
    if not pattern.strip():
        return "**Error:** `pattern` must not be empty."

    context_lines = max(0, min(5, context_lines))
    max_results = max(1, min(200, max_results))

    # Compile pattern
    try:
        rx = re.compile(pattern, re.MULTILINE)
    except re.error as exc:
        return f"**Invalid regex pattern:** {exc}\n\nUse a plain string or a valid Python regex."

    # Collect files
    try:
        files = list(project_root.glob(path_glob))
    except Exception as exc:
        return f"**Invalid glob pattern:** {exc}"

    if not files:
        return (
            f"**No files matched** glob `{path_glob}` in `{project_root}`.\n\n"
            "Examples: `src/**/*.php`, `templates/**/*.twig`, `**/*.yaml`"
        )

    matches: list[dict] = []
    files_with_matches: set[str] = set()
    total_match_count = 0

    for php_file in sorted(files):
        if not php_file.is_file():
            continue
        try:
            content = php_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        file_lines = content.splitlines()
        rel = str(php_file.relative_to(project_root))

        for line_match in rx.finditer(content):
            total_match_count += 1
            line_num = content[: line_match.start()].count("\n")  # 0-indexed
            start_ctx = max(0, line_num - context_lines)
            end_ctx = min(len(file_lines), line_num + context_lines + 1)

            if len(matches) < max_results:
                matches.append({
                    "file": rel,
                    "line": line_num + 1,  # 1-indexed
                    "snippet": file_lines[start_ctx:end_ctx],
                    "match_line_offset": line_num - start_ctx,
                })
            files_with_matches.add(rel)

    # ---- Format output ----
    if not matches:
        return (
            f"No matches for `{pattern}` in `{path_glob}` "
            f"({len(files)} files searched)."
        )

    truncated = total_match_count > max_results
    header = (
        f"Found **{total_match_count}** match(es) "
        f"in **{len(files_with_matches)}** file(s)"
        + (f" (showing first {max_results})" if truncated else "")
        + f" — pattern: `{pattern}`"
    )

    lines: list[str] = [header, ""]

    for m in matches:
        lines.append(f"#### `{m['file']}` line {m['line']}")
        lines.append("```php")
        for i, src_line in enumerate(m["snippet"]):
            abs_line = m["line"] - m["match_line_offset"] + i
            prefix = ">>> " if i == m["match_line_offset"] else "    "
            lines.append(f"{prefix}{abs_line:4d} | {src_line}")
        lines.append("```")
        lines.append("")

    if truncated:
        lines.append(
            f"> Showing {max_results} of {total_match_count} matches. "
            "Narrow the pattern or set a higher `max_results`."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _default_scan_dirs(project_root: Path) -> list[str]:
    candidates = ["src", "app/src", "lib", "app"]
    return [d for d in candidates if (project_root / d).is_dir()]


def _fqn_to_file(fqn: str, index: SymbolIndex) -> str:
    """Try to resolve a method FQN like App\Foo::bar to its file path."""
    class_fqn = fqn.rsplit("::", 1)[0] if "::" in fqn else fqn
    for fi in index.files.values():
        for cls in fi.symbols:
            if cls.fqn == class_fqn:
                return f"`{fi.rel_path}`"
    return "*(unknown)*"


def _format_class_detail(cls: ClassSymbol) -> list[str]:
    lines = [
        "",
        f"#### `{cls.fqn}` ({cls.kind})",
        f"- **File:** `{cls.file}:{cls.line}`",
    ]
    if cls.extends:
        lines.append(f"- **Extends:** {', '.join(f'`{e}`' for e in cls.extends)}")
    if cls.implements:
        lines.append(f"- **Implements:** {', '.join(f'`{i}`' for i in cls.implements)}")
    if cls.route_prefix:
        lines.append(f"- **Route prefix:** `{cls.route_prefix}`")
    if cls.attributes:
        lines.append(f"- **Attributes:** {', '.join(f'`#[{a}]`' for a in cls.attributes[:5])}")
    if cls.constants:
        lines.append(f"- **Constants:** {', '.join(f'`{c}`' for c in cls.constants[:10])}")
    if cls.methods:
        pub_methods = [m for m in cls.methods if m.visibility == "public"]
        if pub_methods:
            lines.append(
                f"- **Public methods ({len(pub_methods)}):** "
                + ", ".join(f"`{m.name}()`" for m in pub_methods[:15])
                + (" …" if len(pub_methods) > 15 else "")
            )
    return lines
