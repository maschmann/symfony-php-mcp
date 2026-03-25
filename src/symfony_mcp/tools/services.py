"""
Tool: list_services

Two modes:
  1. YAML mode (default): reads config/services.yaml directly – fast, no PHP needed.
  2. Container debug mode: runs `php bin/console debug:container --format=json`
     for the full resolved DI container including auto-wired services.

Results are filtered by an optional pattern and returned as Markdown.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

try:
    import yaml  # PyYAML

    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

from ..config import ServerConfig
from ..executor import PhpExecutor


def list_services(
    executor: PhpExecutor,
    config: ServerConfig,
    filter_pattern: str = "",
    use_container_debug: bool = False,
) -> str:
    """Return a Markdown list of Symfony service definitions.

    Args:
        executor:            Configured PhpExecutor instance.
        config:              Server configuration.
        filter_pattern:      Regex/substring to filter service IDs or class names.
                             Empty = show all (may be very long for large projects).
        use_container_debug: Force use of ``debug:container`` instead of YAML parsing.
                             Required to see auto-wired / compiler-pass services.
    """
    if use_container_debug:
        return _from_container_debug(executor, config, filter_pattern)

    # Try YAML first; fall back to debug:container on failure
    yaml_result = _from_yaml(config, filter_pattern)
    if yaml_result is not None:
        return yaml_result

    return _from_container_debug(executor, config, filter_pattern)


# ---------------------------------------------------------------------------
# YAML reader
# ---------------------------------------------------------------------------

def _from_yaml(config: ServerConfig, filter_pattern: str) -> Optional[str]:
    """Read config/services.yaml and return Markdown, or None on hard failure."""
    if not _YAML_AVAILABLE:
        return None  # trigger fallback

    services_path = config.project_root / "config" / "services.yaml"
    if not services_path.is_file():
        # Check for services.yml (older Symfony)
        services_path = config.project_root / "config" / "services.yml"
        if not services_path.is_file():
            return None  # no file – trigger fallback

    try:
        raw = yaml.safe_load(services_path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError) as exc:
        return f"**Error parsing `config/services.yaml`:** {exc}"

    services_block: dict = raw.get("services", {})
    if not services_block:
        return (
            "No `services:` key found in `config/services.yaml`.\n\n"
            "Try using `use_container_debug=true` to inspect the full DI container."
        )

    # ------------------------------------------------------------------ #
    # Extract entries
    # ------------------------------------------------------------------ #
    entries: list[dict] = []

    # Global defaults (affects all services)
    defaults = services_block.get("_defaults", {})

    for svc_id, svc_def in services_block.items():
        if svc_id.startswith("_"):
            continue  # _defaults, _instanceof, etc.

        # svc_def can be None (shorthand alias) or a dict
        svc_info: dict = svc_def or {}
        if not isinstance(svc_info, dict):
            svc_info = {}

        entry = {
            "id": svc_id,
            "class": svc_info.get("class", svc_id if "\\" in svc_id else ""),
            "alias": svc_info.get("alias", ""),
            "public": svc_info.get("public", defaults.get("public", False)),
            "autowire": svc_info.get("autowire", defaults.get("autowire", False)),
            "autoconfigure": svc_info.get("autoconfigure", defaults.get("autoconfigure", False)),
            "tags": svc_info.get("tags", []),
            "arguments": svc_info.get("arguments", {}),
            "calls": svc_info.get("calls", []),
            "factory": svc_info.get("factory", ""),
            "decorates": svc_info.get("decorates", ""),
        }
        entries.append(entry)

    # ------------------------------------------------------------------ #
    # Filter
    # ------------------------------------------------------------------ #
    entries = _filter_entries(entries, filter_pattern)

    if not entries:
        return (
            f"No services matched `{filter_pattern}` in `config/services.yaml`.\n\n"
            "Try `use_container_debug=true` to search the full compiled container."
        )

    # ------------------------------------------------------------------ #
    # Format
    # ------------------------------------------------------------------ #
    header_line = (
        f"Found **{len(entries)}** service(s) in `config/services.yaml`"
        + (f" matching `{filter_pattern}`" if filter_pattern else "")
    )

    lines: list[str] = [
        header_line,
        "",
        "### Global Defaults",
        f"- autowire: `{defaults.get('autowire', False)}`",
        f"- autoconfigure: `{defaults.get('autoconfigure', False)}`",
        f"- public: `{defaults.get('public', False)}`",
        "",
        "### Services",
        "| Service ID | Class | Public | Tags |",
        "|------------|-------|--------|------|",
    ]

    for entry in entries:
        svc_id = entry["id"]
        cls = entry["class"] or entry["alias"] or "—"
        public = "Yes" if entry["public"] else "No"
        tags = _format_tags(entry["tags"])
        lines.append(f"| `{svc_id}` | `{cls}` | {public} | {tags} |")

    # Show full detail for small result sets
    if 0 < len(entries) <= 5:
        lines.append("")
        lines.append("### Detailed Definitions")
        for entry in entries:
            lines.extend(_format_service_detail(entry))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# debug:container reader
# ---------------------------------------------------------------------------

def _from_container_debug(
    executor: PhpExecutor, config: ServerConfig, filter_pattern: str
) -> str:
    """Use ``debug:container`` to list services from the compiled container."""
    # Build command – optionally add tag/name filters
    args = ["debug:container", "--format=json"]

    ok, output = executor.run_console(args)

    if not ok:
        if "Binary not found" in output or "not found" in output.lower():
            return (
                "**Error:** Cannot run `debug:container` – PHP or Symfony console not found.\n\n"
                f"Details:\n```\n{output}\n```\n\n"
                "Make sure `SYMFONY_PROJECT_ROOT` and `PHP_EXECUTABLE` are configured correctly."
            )
        return (
            f"**Error running `debug:container`:**\n\n```\n{output[:2000]}\n```"
        )

    # Parse JSON – debug:container output is an array of service descriptors
    try:
        raw = json.loads(output)
    except json.JSONDecodeError:
        # Strip leading non-JSON text (Symfony info banner)
        match = re.search(r"\[.*\]", output, re.DOTALL)
        if not match:
            return (
                "**Error:** Could not parse `debug:container` output.\n\n"
                f"Raw:\n```\n{output[:1000]}\n```"
            )
        try:
            raw = json.loads(match.group())
        except json.JSONDecodeError:
            return "**Error:** Could not parse `debug:container` JSON output."

    # Normalise – format varies between Symfony versions
    if isinstance(raw, dict):
        # Newer Symfony: { "service_id": { ... }, ... }
        services_list = [{"id": k, **v} for k, v in raw.items()]
    elif isinstance(raw, list):
        services_list = raw
    else:
        return f"**Unexpected output format from `debug:container`:** {type(raw)}"

    # Filter
    entries = _filter_entries(services_list, filter_pattern)

    if not entries:
        total = len(services_list)
        return (
            f"No services matched `{filter_pattern}` "
            f"(searched {total} compiled services).\n\n"
            "**Tip:** Try a shorter pattern (e.g. `Mailer` instead of `App\\Service\\Mailer`)."
        )

    lines: list[str] = [
        f"Found **{len(entries)}** service(s) in the compiled container"
        + (f" matching `{filter_pattern}`" if filter_pattern else ""),
        "> Source: `php bin/console debug:container --format=json`",
        "",
        "| Service ID | Class | Public | Tags |",
        "|------------|-------|--------|------|",
    ]

    for svc in entries[:200]:  # cap at 200 rows to avoid token explosion
        svc_id = svc.get("id", svc.get("service", ""))
        cls = svc.get("class", "")
        public = "Yes" if svc.get("public") else "No"
        tags = _format_tags(svc.get("tags", []))
        lines.append(f"| `{svc_id}` | `{cls}` | {public} | {tags} |")

    if len(entries) > 200:
        lines.append(f"\n> *(showing first 200 of {len(entries)} – narrow the filter)*")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _filter_entries(entries: list[dict], pattern: str) -> list[dict]:
    """Filter a list of service dicts by *pattern* (substring or regex)."""
    if not pattern.strip():
        return entries

    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error:
        rx = re.compile(re.escape(pattern), re.IGNORECASE)

    def _matches(entry: dict) -> bool:
        svc_id = str(entry.get("id", entry.get("service", "")))
        cls = str(entry.get("class", ""))
        return bool(rx.search(svc_id) or rx.search(cls))

    return [e for e in entries if _matches(e)]


def _format_tags(tags: Any) -> str:
    if not tags:
        return "—"
    if isinstance(tags, list):
        names = []
        for t in tags:
            if isinstance(t, dict):
                names.append(t.get("name", str(t)))
            else:
                names.append(str(t))
        return ", ".join(f"`{n}`" for n in names[:3]) + (
            f" +{len(tags) - 3} more" if len(tags) > 3 else ""
        )
    if isinstance(tags, dict):
        return ", ".join(f"`{k}`" for k in list(tags.keys())[:3])
    return str(tags)


def _format_service_detail(entry: dict) -> list[str]:
    lines = [f"\n#### `{entry['id']}`"]
    if entry.get("class"):
        lines.append(f"- **Class:** `{entry['class']}`")
    if entry.get("alias"):
        lines.append(f"- **Alias for:** `{entry['alias']}`")
    if entry.get("factory"):
        lines.append(f"- **Factory:** `{entry['factory']}`")
    if entry.get("decorates"):
        lines.append(f"- **Decorates:** `{entry['decorates']}`")
    if entry.get("arguments"):
        args = entry["arguments"]
        if isinstance(args, dict):
            for k, v in args.items():
                lines.append(f"- **Arg `{k}`:** `{v}`")
        elif isinstance(args, list):
            for v in args:
                lines.append(f"- **Arg:** `{v}`")
    if entry.get("tags"):
        lines.append(f"- **Tags:** {_format_tags(entry['tags'])}")
    return lines
