"""
Configuration management for symfony-php-mcp.

Priority order (highest to lowest):
  1. Environment variables
  2. .symfony-mcp.json in the Symfony project root
  3. Built-in defaults

This allows project-local overrides (e.g. Docker container name) while
still permitting the MCP client to override everything via env vars.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

CONFIG_FILE_NAME = ".symfony-mcp.json"

# Well-known PHP-wrapping CLIs that need special handling
KNOWN_WRAPPERS = {"ddev", "lando", "sail"}


@dataclass
class ServerConfig:
    """Resolved runtime configuration for the MCP server."""

    # Absolute path to the Symfony project root
    project_root: Path = field(default_factory=Path.cwd)

    # PHP binary / wrapper.  Examples:
    #   "php"              – plain system PHP
    #   "ddev php"         – DDEV wrapper  (split into argv list on load)
    #   "lando php"        – Lando wrapper
    #   "/usr/bin/php8.3"  – explicit path
    # When docker_container is set this is overridden to "php" inside the container.
    php_executable: str = "php"

    # Docker container name.  When set, commands are executed as:
    #   docker exec [-u <user>] <container> php …
    docker_container: Optional[str] = None

    # Optional user for `docker exec -u <user>`.
    docker_exec_user: Optional[str] = None

    # Path to bin/console relative to project_root.
    console_path: str = "bin/console"

    # Subprocess timeout in seconds.
    command_timeout: int = 30

    # ---------------------------------------------------------------------------
    # Derived helpers
    # ---------------------------------------------------------------------------

    def get_php_command(self) -> list[str]:
        """Return the argv prefix used to invoke PHP.

        With Docker:  ["docker", "exec", "-u", "user", "container", "php"]
        With wrapper: ["ddev", "php"]
        Plain:        ["php"]
        """
        if self.docker_container:
            cmd = ["docker", "exec"]
            if self.docker_exec_user:
                cmd += ["-u", self.docker_exec_user]
            cmd += [self.docker_container, "php"]
            return cmd

        # Support space-separated wrappers like "ddev php" or "lando php"
        parts = self.php_executable.strip().split()
        return parts if parts else ["php"]

    def get_console_command(self) -> list[str]:
        """Return the full argv list for running bin/console."""
        return self.get_php_command() + [self.console_path]

    def describe(self) -> str:
        """Human-readable config summary (useful for diagnostics)."""
        lines = [
            f"  project_root    : {self.project_root}",
            f"  php_executable  : {self.php_executable}",
            f"  docker_container: {self.docker_container or '(none)'}",
            f"  docker_exec_user: {self.docker_exec_user or '(none)'}",
            f"  console_path    : {self.console_path}",
            f"  command_timeout : {self.command_timeout}s",
        ]
        return "\n".join(lines)

    # ---------------------------------------------------------------------------
    # Factory / loading helpers
    # ---------------------------------------------------------------------------

    @classmethod
    def _from_dict(cls, data: dict, base: Optional["ServerConfig"] = None) -> "ServerConfig":
        """Overlay dict values onto *base* (or a fresh default config)."""
        cfg = base or cls()
        if pr := data.get("project_root"):
            cfg.project_root = Path(pr)
        if pe := data.get("php_executable"):
            cfg.php_executable = pe
        if dc := data.get("docker_container"):
            cfg.docker_container = dc
        if du := data.get("docker_exec_user"):
            cfg.docker_exec_user = du
        if cp := data.get("console_path"):
            cfg.console_path = cp
        if to := data.get("command_timeout"):
            try:
                cfg.command_timeout = int(to)
            except (TypeError, ValueError):
                pass
        return cfg

    @classmethod
    def from_file(cls, config_file: Path, base: Optional["ServerConfig"] = None) -> "ServerConfig":
        """Load config from a .symfony-mcp.json file, merging into *base*."""
        if not config_file.is_file():
            return base or cls()
        try:
            with config_file.open(encoding="utf-8") as fh:
                data = json.load(fh)
            return cls._from_dict(data, base)
        except (json.JSONDecodeError, OSError):
            # Malformed / unreadable config file – fall through to defaults
            return base or cls()

    @classmethod
    def from_env(cls, base: Optional["ServerConfig"] = None) -> "ServerConfig":
        """Overlay environment variables onto *base*."""
        cfg = base or cls()
        env = os.environ

        if pr := env.get("SYMFONY_PROJECT_ROOT"):
            cfg.project_root = Path(pr)
        if pe := env.get("PHP_EXECUTABLE"):
            cfg.php_executable = pe
        if dc := env.get("DOCKER_CONTAINER"):
            cfg.docker_container = dc
        if du := env.get("DOCKER_EXEC_USER"):
            cfg.docker_exec_user = du
        if cp := env.get("CONSOLE_PATH"):
            cfg.console_path = cp
        if to := env.get("COMMAND_TIMEOUT"):
            try:
                cfg.command_timeout = int(to)
            except (TypeError, ValueError):
                pass
        return cfg

    @classmethod
    def load(cls, project_root: Optional[Path] = None) -> "ServerConfig":
        """Load config with full priority chain.

        1. Start from defaults.
        2. Overlay .symfony-mcp.json from the project root (if present).
        3. Overlay environment variables (always win).
        4. If project_root was passed explicitly, use it to override the
           env-resolved value (for programmatic / testing use).
        """
        # Step 1 – defaults
        cfg = cls()

        # Step 2 – determine project root early so we can find the config file
        root_from_env = os.environ.get("SYMFONY_PROJECT_ROOT")
        effective_root = project_root or (Path(root_from_env) if root_from_env else Path.cwd())
        cfg.project_root = effective_root

        # Step 3 – .symfony-mcp.json in that root
        cfg = cls.from_file(effective_root / CONFIG_FILE_NAME, base=cfg)

        # Step 4 – env vars take precedence over file
        cfg = cls.from_env(base=cfg)

        # Step 5 – explicit argument wins over everything
        if project_root is not None:
            cfg.project_root = project_root.resolve()

        return cfg


# ---------------------------------------------------------------------------
# Example config generator (used by README / init command)
# ---------------------------------------------------------------------------

EXAMPLE_CONFIG: dict = {
    "php_executable": "php",
    "docker_container": None,
    "docker_exec_user": None,
    "console_path": "bin/console",
    "command_timeout": 30,
}


def write_example_config(dest: Path) -> None:
    """Write an example .symfony-mcp.json to *dest*."""
    with dest.open("w", encoding="utf-8") as fh:
        json.dump(EXAMPLE_CONFIG, fh, indent=2)
        fh.write("\n")
