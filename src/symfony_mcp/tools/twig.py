"""
Tool: analyze_twig

Locates a Twig template by name and extracts its structural metadata:
  - extends parent
  - included templates
  - imported macro files
  - defined blocks
  - top-level variables referenced

No PHP execution required – this is pure filesystem analysis.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


# Regex patterns for Twig syntax constructs
_RE_EXTENDS = re.compile(
    r"""\{%-?\s*extends\s+['"]([^'"]+)['"]\s*-?%\}""",
    re.IGNORECASE,
)
_RE_INCLUDE = re.compile(
    r"""\{%-?\s*include\s+['"]([^'"]+)['"]\s*(?:[^%]*)?\s*-?%\}""",
    re.IGNORECASE,
)
_RE_INCLUDE_FUNC = re.compile(
    r"""include\(\s*['"]([^'"]+)['"]\s*\)""",
    re.IGNORECASE,
)
_RE_IMPORT = re.compile(
    r"""\{%-?\s*import\s+['"]([^'"]+)['"]\s+as\s+(\w+)\s*-?%\}""",
    re.IGNORECASE,
)
_RE_FROM_IMPORT = re.compile(
    r"""\{%-?\s*from\s+['"]([^'"]+)['"]\s+import\s+([^%]+?)\s*-?%\}""",
    re.IGNORECASE,
)
_RE_EMBED = re.compile(
    r"""\{%-?\s*embed\s+['"]([^'"]+)['"]\s*""",
    re.IGNORECASE,
)
_RE_BLOCK_DEF = re.compile(
    r"""\{%-?\s*block\s+(\w+)\s*-?%\}""",
    re.IGNORECASE,
)
_RE_VARIABLE = re.compile(
    r"""\{\{-?\s*([a-zA-Z_][a-zA-Z0-9_]*)""",
)


def analyze_twig(project_root: Path, template_name: str) -> str:
    """Find *template_name* in the project and return a Markdown analysis.

    Args:
        project_root:  Absolute path to the Symfony project root.
        template_name: Template name as used in Twig (e.g. ``user/show.html.twig``)
                       or a bare filename (e.g. ``show.html.twig``).
                       Partial matches are supported.
    """
    if not template_name.strip():
        return "**Error:** `template_name` must not be empty."

    # ------------------------------------------------------------------ #
    # 1. Locate the template file
    # ------------------------------------------------------------------ #
    found_files = _find_template(project_root, template_name)

    if not found_files:
        search_roots = _template_search_dirs(project_root)
        return (
            f"**Template not found:** `{template_name}`\n\n"
            f"Searched in:\n"
            + "".join(f"- `{d}`\n" for d in search_roots)
            + "\n**Tips:**\n"
            "- Use a partial name (e.g. `show` instead of `user/show.html.twig`)\n"
            "- Template directories other than `templates/` are searched too\n"
            "- Bundle templates (in `vendor/`) are not scanned by default"
        )

    if len(found_files) > 1:
        file_list = "\n".join(
            f"- `{f.relative_to(project_root)}`" for f in found_files
        )
        return (
            f"**Multiple templates matched `{template_name}`** "
            f"({len(found_files)} files). "
            "Use a more specific name.\n\n"
            f"{file_list}"
        )

    tpl_path = found_files[0]

    # ------------------------------------------------------------------ #
    # 2. Read content
    # ------------------------------------------------------------------ #
    try:
        content = tpl_path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"**Error reading template:** {exc}"

    rel_path = tpl_path.relative_to(project_root)
    lines: list[str] = [f"## Template: `{rel_path}`\n"]

    # ------------------------------------------------------------------ #
    # 3. Extends
    # ------------------------------------------------------------------ #
    extends_matches = _RE_EXTENDS.findall(content)
    lines.append("### Inheritance")
    if extends_matches:
        lines.append(f"Extends: `{extends_matches[0]}`")
        if len(extends_matches) > 1:
            lines.append(
                "> Note: multiple `extends` tags found – only the first is valid at runtime."
            )
    else:
        lines.append("Standalone template (no `{% extends %}`).")
    lines.append("")

    # ------------------------------------------------------------------ #
    # 4. Includes
    # ------------------------------------------------------------------ #
    includes = _dedupe(
        _RE_INCLUDE.findall(content) + _RE_INCLUDE_FUNC.findall(content)
    )
    embeds = _RE_EMBED.findall(content)

    if includes or embeds:
        lines.append("### Included / Embedded Templates")
        lines.append("| Type | Template |")
        lines.append("|------|----------|")
        for tpl in sorted(includes):
            lines.append(f"| include | `{tpl}` |")
        for tpl in sorted(embeds):
            lines.append(f"| embed | `{tpl}` |")
        lines.append("")

    # ------------------------------------------------------------------ #
    # 5. Macro imports
    # ------------------------------------------------------------------ #
    imports = _RE_IMPORT.findall(content)
    from_imports = _RE_FROM_IMPORT.findall(content)

    if imports or from_imports:
        lines.append("### Macro Imports")
        lines.append("| File | Alias / Macros |")
        lines.append("|------|----------------|")
        for tpl, alias in imports:
            lines.append(f"| `{tpl}` | `{alias}` (full import) |")
        for tpl, macros in from_imports:
            macro_list = ", ".join(m.strip() for m in macros.split(","))
            lines.append(f"| `{tpl}` | `{macro_list}` |")
        lines.append("")

    # ------------------------------------------------------------------ #
    # 6. Blocks
    # ------------------------------------------------------------------ #
    blocks = _dedupe(_RE_BLOCK_DEF.findall(content))
    if blocks:
        lines.append("### Defined Blocks")
        lines.append(", ".join(f"`{b}`" for b in sorted(blocks)))
        lines.append("")

    # ------------------------------------------------------------------ #
    # 7. Top-level variables
    # ------------------------------------------------------------------ #
    raw_vars = _RE_VARIABLE.findall(content)
    # Filter out Twig built-ins and macro aliases
    macro_aliases = {alias for _, alias in imports}
    twig_builtins = {
        "loop", "app", "block", "parent", "attribute", "constant",
        "cycle", "date", "dump", "include", "max", "min", "not",
        "random", "range", "source", "template_from_string",
        "true", "false", "null", "none",
    }
    variables = sorted(
        set(raw_vars) - twig_builtins - macro_aliases
    )
    if variables:
        lines.append("### Template Variables (referenced in `{{ }}` expressions)")
        lines.append(
            "> These are potential variables that should be passed from the controller."
        )
        lines.append(", ".join(f"`{v}`" for v in variables))
        lines.append("")

    # ------------------------------------------------------------------ #
    # 8. File stats
    # ------------------------------------------------------------------ #
    total_lines = content.count("\n") + 1
    lines.append("### File Info")
    lines.append(f"| Key | Value |")
    lines.append(f"|-----|-------|")
    lines.append(f"| Path | `{rel_path}` |")
    lines.append(f"| Lines | {total_lines} |")
    lines.append(f"| Size | {len(content.encode())} bytes |")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _template_search_dirs(project_root: Path) -> list[Path]:
    """Return candidate template directories for the project."""
    candidates = [
        project_root / "templates",
        project_root / "app" / "Resources" / "views",  # Symfony 3.x
    ]
    # Also include templates inside src/ bundles
    src = project_root / "src"
    if src.is_dir():
        for bundle_dir in src.rglob("Resources/views"):
            candidates.append(bundle_dir)
    return [d for d in candidates if d.is_dir()]


def _find_template(project_root: Path, template_name: str) -> list[Path]:
    """Search template directories for files matching *template_name*."""
    search_dirs = _template_search_dirs(project_root)
    results: list[Path] = []

    for search_dir in search_dirs:
        for twig_file in search_dir.rglob("*.twig"):
            # Match by full relative-to-search-dir path or by filename
            rel = str(twig_file.relative_to(search_dir))
            if (
                template_name in rel
                or template_name in twig_file.name
                or rel == template_name
            ):
                if twig_file not in results:
                    results.append(twig_file)

    return results


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
