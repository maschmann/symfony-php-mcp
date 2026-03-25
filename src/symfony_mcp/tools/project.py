"""
Tool: get_project_overview

Reads composer.json, symfony.lock (or composer.lock), and .env to build
a concise Markdown summary of the project for the LLM.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Packages that belong to known categories – used to group the bundle list
_CATEGORY_PREFIXES: dict[str, str] = {
    "symfony/": "Symfony Components",
    "doctrine/": "Doctrine / Database",
    "api-platform/": "API Platform",
    "league/": "League",
    "nelmio/": "Nelmio",
    "knplabs/": "KNP Labs",
    "stof/": "StofDoctrineExtensions",
    "jms/": "JMS",
    "friendsofsymfony/": "FriendsOfSymfony",
    "easycorp/": "EasyAdmin / EasyCorp",
    "liip/": "Liip",
    "lexik/": "Lexik",
    "scheb/": "Scheb",
    "sensio/": "Sensio Labs",
    "twig/": "Twig",
    "monolog/": "Logging",
    "guzzlehttp/": "HTTP Client",
    "symfony/messenger": "Messaging",
}

# Symfony component names we want to highlight in the summary
_HIGHLIGHT_COMPONENTS = {
    "symfony/framework-bundle",
    "symfony/security-bundle",
    "symfony/twig-bundle",
    "symfony/console",
    "symfony/http-kernel",
    "symfony/messenger",
    "symfony/mailer",
    "symfony/notifier",
    "symfony/webpack-encore-bundle",
    "symfony/api-platform-core",
}


def get_project_overview(project_root: Path) -> str:
    """Return a Markdown summary of the Symfony project.

    Reads:
      - composer.json       – PHP version requirement, all packages
      - symfony.lock        – exact installed Symfony version
      - composer.lock       – fallback for exact versions
      - .env / .env.local   – APP_ENV, APP_NAME
    """
    lines: list[str] = []

    # ------------------------------------------------------------------ #
    # 1. composer.json
    # ------------------------------------------------------------------ #
    composer_path = project_root / "composer.json"
    if not composer_path.is_file():
        return (
            "**Error:** `composer.json` not found in "
            f"`{project_root}`.\n\n"
            "Make sure `SYMFONY_PROJECT_ROOT` points to the root of your "
            "Symfony project (the directory that contains `composer.json`)."
        )

    try:
        composer: dict[str, Any] = json.loads(composer_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return f"**Error reading composer.json:** {exc}"

    project_name = composer.get("name", "(unnamed)")
    description = composer.get("description", "")
    php_constraint = composer.get("require", {}).get("php", "not specified")

    lines.append(f"# Symfony Project: `{project_name}`")
    if description:
        lines.append(f"\n> {description}\n")

    # ------------------------------------------------------------------ #
    # 2. Symfony version – from symfony.lock or composer.lock
    # ------------------------------------------------------------------ #
    symfony_version = _detect_symfony_version(project_root)
    lines.append("## Runtime")
    lines.append(f"| Key | Value |")
    lines.append(f"|-----|-------|")
    lines.append(f"| PHP requirement | `{php_constraint}` |")
    lines.append(f"| Symfony version | `{symfony_version}` |")

    # APP_ENV from .env
    app_env = _read_env_var(project_root, "APP_ENV") or "not set"
    lines.append(f"| APP_ENV | `{app_env}` |")
    lines.append("")

    # ------------------------------------------------------------------ #
    # 3. Packages
    # ------------------------------------------------------------------ #
    require: dict[str, str] = composer.get("require", {})
    require_dev: dict[str, str] = composer.get("require-dev", {})
    all_packages = {**require, **require_dev}
    # Remove php itself
    all_packages.pop("php", None)
    all_packages.pop("ext-json", None)

    categorised = _categorise_packages(all_packages)

    lines.append("## Installed Packages")
    for category, pkgs in sorted(categorised.items()):
        lines.append(f"\n### {category}")
        lines.append("| Package | Version Constraint | Dev? |")
        lines.append("|---------|-------------------|------|")
        for pkg, ver in sorted(pkgs.items()):
            is_dev = "Yes" if pkg in require_dev else "No"
            lines.append(f"| `{pkg}` | `{ver}` | {is_dev} |")

    # ------------------------------------------------------------------ #
    # 4. Autoload info
    # ------------------------------------------------------------------ #
    autoload = composer.get("autoload", {})
    psr4 = autoload.get("psr-4", {})
    if psr4:
        lines.append("\n## PSR-4 Autoload Namespaces")
        lines.append("| Namespace | Directory |")
        lines.append("|-----------|-----------|")
        for ns, path in psr4.items():
            lines.append(f"| `{ns}` | `{path}` |")

    # ------------------------------------------------------------------ #
    # 5. Scripts (helpful to know what's available)
    # ------------------------------------------------------------------ #
    scripts: dict = composer.get("scripts", {})
    if scripts:
        lines.append("\n## Composer Scripts")
        lines.append("| Script | Command |")
        lines.append("|--------|---------|")
        for name, cmd in scripts.items():
            cmd_str = cmd if isinstance(cmd, str) else "; ".join(cmd) if isinstance(cmd, list) else str(cmd)
            lines.append(f"| `{name}` | `{cmd_str[:80]}` |")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _detect_symfony_version(project_root: Path) -> str:
    """Try to find the exact installed Symfony version."""
    # symfony.lock lists all locked Symfony packages
    symfony_lock_path = project_root / "symfony.lock"
    if symfony_lock_path.is_file():
        try:
            lock_data: dict = json.loads(symfony_lock_path.read_text(encoding="utf-8"))
            # Find symfony/framework-bundle or symfony/http-kernel as version reference
            for key in ("symfony/framework-bundle", "symfony/http-kernel", "symfony/console"):
                entry = lock_data.get(key)
                if entry and isinstance(entry, dict):
                    versions = entry.get("versions", [])
                    if versions:
                        return str(versions[-1])
        except (json.JSONDecodeError, OSError):
            pass

    # Fallback: composer.lock
    composer_lock_path = project_root / "composer.lock"
    if composer_lock_path.is_file():
        try:
            lock_data2: dict = json.loads(composer_lock_path.read_text(encoding="utf-8"))
            for pkg in lock_data2.get("packages", []):
                if pkg.get("name") in (
                    "symfony/framework-bundle",
                    "symfony/http-kernel",
                    "symfony/console",
                ):
                    return pkg.get("version", "unknown")
        except (json.JSONDecodeError, OSError):
            pass

    # Fallback: composer.json require constraint
    composer_path = project_root / "composer.json"
    if composer_path.is_file():
        try:
            composer: dict = json.loads(composer_path.read_text(encoding="utf-8"))
            for pkg in ("symfony/framework-bundle", "symfony/http-kernel"):
                ver = composer.get("require", {}).get(pkg)
                if ver:
                    return f"{ver} (constraint, not exact)"
        except (json.JSONDecodeError, OSError):
            pass

    return "unknown"


def _categorise_packages(packages: dict[str, str]) -> dict[str, dict[str, str]]:
    """Group packages by category prefix."""
    result: dict[str, dict[str, str]] = {}

    for pkg, ver in packages.items():
        category = "Other"
        for prefix, cat_name in _CATEGORY_PREFIXES.items():
            if pkg.startswith(prefix):
                category = cat_name
                break
        result.setdefault(category, {})[pkg] = ver

    return result


def _read_env_var(project_root: Path, var_name: str) -> str | None:
    """Read a variable from .env or .env.local (simple key=value parsing)."""
    for env_file in (".env.local", ".env"):
        path = project_root / env_file
        if not path.is_file():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() == var_name:
                    return value.strip().strip('"').strip("'")
        except OSError:
            continue
    return None
