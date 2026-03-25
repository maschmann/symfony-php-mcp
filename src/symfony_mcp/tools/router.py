"""
Tool: find_route

Runs `php bin/console debug:router --format=json` and filters the results
to help the LLM identify which Controller/action handles a given URL.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from ..config import ServerConfig
from ..executor import PhpExecutor


def find_route(
    executor: PhpExecutor,
    config: ServerConfig,
    url_pattern: str,
    method: str = "",
) -> str:
    """Return matching routes for *url_pattern* as a Markdown table.

    Args:
        executor:    Configured PhpExecutor instance.
        config:      Server configuration (used for project_root).
        url_pattern: Substring or regex to match against route paths.
                     Also accepts route *names* (e.g. "app_user_show").
        method:      Optional HTTP method filter (GET, POST, PUT, …).
                     Case-insensitive. Empty string = no filter.

    The tool tries JSON output first; if the Symfony version does not
    support it, falls back to plain text parsing.
    """
    if not url_pattern.strip():
        return "**Error:** `url_pattern` must not be empty."

    # ------------------------------------------------------------------ #
    # 1. Run debug:router
    # ------------------------------------------------------------------ #
    ok, output = executor.run_console(["debug:router", "--format=json"])

    if not ok:
        # Common failure modes with helpful advice
        if "not found" in output.lower() or "Binary not found" in output:
            return _php_missing_error(config)
        if "console" in output.lower() and "not found" in output.lower():
            return _console_missing_error(config)
        # Symfony < 5.2 may not support --format=json
        if "option" in output.lower() and "format" in output.lower():
            return _fallback_text_parse(executor, config, url_pattern, method)
        return (
            f"**Error running `debug:router`:**\n\n```\n{output}\n```\n\n"
            "Run `php bin/console debug:router` manually to diagnose the issue."
        )

    # ------------------------------------------------------------------ #
    # 2. Parse JSON
    # ------------------------------------------------------------------ #
    try:
        routes: dict[str, dict] = json.loads(output)
    except json.JSONDecodeError:
        # Output may contain leading symfony banner text; strip it
        match = re.search(r"\{.*\}", output, re.DOTALL)
        if match:
            try:
                routes = json.loads(match.group())
            except json.JSONDecodeError:
                return (
                    "**Error:** Could not parse `debug:router` JSON output.\n\n"
                    f"Raw output:\n```\n{output[:2000]}\n```"
                )
        else:
            return _fallback_text_parse(executor, config, url_pattern, method)

    # ------------------------------------------------------------------ #
    # 3. Filter
    # ------------------------------------------------------------------ #
    method_filter = method.strip().upper()
    matches: list[tuple[str, dict]] = []

    try:
        pattern = re.compile(url_pattern, re.IGNORECASE)
    except re.error:
        # Not a valid regex – fall back to substring match
        pattern = re.compile(re.escape(url_pattern), re.IGNORECASE)

    for route_name, info in routes.items():
        path = info.get("path", "")
        # Match against path OR route name
        if not (pattern.search(path) or pattern.search(route_name)):
            continue
        if method_filter:
            route_methods = [m.upper() for m in info.get("methods", [])]
            if route_methods and method_filter not in route_methods:
                continue
        matches.append((route_name, info))

    # ------------------------------------------------------------------ #
    # 4. Format output
    # ------------------------------------------------------------------ #
    if not matches:
        suggestion = (
            f" with HTTP method `{method_filter}`" if method_filter else ""
        )
        return (
            f"No routes matched `{url_pattern}`{suggestion}.\n\n"
            "**Tips:**\n"
            "- Try a shorter pattern (e.g. `user` instead of `/api/users/123`)\n"
            "- Route names are also searched (e.g. `app_user`)\n"
            f"- There are **{len(routes)}** routes registered in total."
        )

    lines = [
        f"Found **{len(matches)}** route(s) matching `{url_pattern}`"
        + (f" (method: `{method_filter}`)" if method_filter else ""),
        "",
        "| Route Name | Path | Methods | Controller |",
        "|------------|------|---------|------------|",
    ]

    for route_name, info in matches:
        path = info.get("path", "—")
        methods = ", ".join(info.get("methods", [])) or "ANY"
        controller = info.get("defaults", {}).get("_controller", "—")
        # Shorten fully-qualified controller names for readability
        short_ctrl = _shorten_controller(controller)
        lines.append(f"| `{route_name}` | `{path}` | `{methods}` | `{short_ctrl}` |")

    # If exactly one match, show full details
    if len(matches) == 1:
        name, info = matches[0]
        lines.append("")
        lines.append(f"### Full details for `{name}`")
        lines.append("```json")
        lines.append(json.dumps(info, indent=2))
        lines.append("```")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _shorten_controller(controller: str) -> str:
    """Shorten `App\\Controller\\UserController::showAction` to a readable form."""
    if "::" in controller:
        cls, method = controller.rsplit("::", 1)
        short_cls = cls.rsplit("\\", 1)[-1]
        return f"{short_cls}::{method}"
    return controller.rsplit("\\", 1)[-1] if "\\" in controller else controller


def _fallback_text_parse(
    executor: PhpExecutor,
    config: ServerConfig,
    url_pattern: str,
    method: str,
) -> str:
    """Parse plain-text output of debug:router (Symfony < 5.2 fallback)."""
    ok, output = executor.run_console(["debug:router"])
    if not ok:
        return f"**Error running `debug:router`:**\n\n```\n{output}\n```"

    try:
        pattern = re.compile(url_pattern, re.IGNORECASE)
    except re.error:
        pattern = re.compile(re.escape(url_pattern), re.IGNORECASE)

    matching_lines = [
        line for line in output.splitlines()
        if pattern.search(line)
    ]

    if not matching_lines:
        return f"No routes matched `{url_pattern}` in plain-text router output."

    return (
        f"Matches for `{url_pattern}` (plain-text fallback; Symfony may not support JSON output):\n\n"
        "```\n"
        + "\n".join(matching_lines)
        + "\n```"
    )


def _php_missing_error(config: ServerConfig) -> str:
    cmd = " ".join(config.get_php_command())
    return (
        f"**Error:** PHP binary not found (`{cmd}`).\n\n"
        "**Possible fixes:**\n"
        "- Install PHP: `apt install php-cli` / `brew install php`\n"
        "- If using Docker: set `DOCKER_CONTAINER=<name>` in your MCP config\n"
        "- If using DDEV: set `PHP_EXECUTABLE=ddev php`\n"
        "- If using Lando: set `PHP_EXECUTABLE=lando php`"
    )


def _console_missing_error(config: ServerConfig) -> str:
    return (
        f"**Error:** Symfony console not found at "
        f"`{config.project_root / config.console_path}`.\n\n"
        "Make sure `SYMFONY_PROJECT_ROOT` points to your Symfony project root "
        "(the directory containing `composer.json` and `bin/`)."
    )
