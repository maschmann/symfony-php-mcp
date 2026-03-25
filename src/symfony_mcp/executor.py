"""
PHP / Symfony console command executor.

Abstracts away the difference between:
  - direct PHP invocations  (php bin/console …)
  - Docker-based executions (docker exec <container> php bin/console …)
  - Wrapper CLIs            (ddev php bin/console …  /  lando php …)

All public methods return a (success: bool, output: str) tuple so that
callers never have to deal with subprocess exceptions.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from .config import ServerConfig


class PhpExecutor:
    """Runs PHP and Symfony console commands according to *config*."""

    def __init__(self, config: ServerConfig) -> None:
        self.config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_console(
        self,
        args: list[str],
        cwd: Optional[Path] = None,
    ) -> tuple[bool, str]:
        """Run ``php bin/console <args>`` and return (success, output)."""
        cmd = self.config.get_console_command() + args
        return self._run(cmd, cwd or self.config.project_root)

    def run_php(
        self,
        args: list[str],
        cwd: Optional[Path] = None,
    ) -> tuple[bool, str]:
        """Run ``php <args>`` and return (success, output)."""
        cmd = self.config.get_php_command() + args
        return self._run(cmd, cwd or self.config.project_root)

    def check_prerequisites(self) -> str:
        """Return a human-readable status string describing available tooling."""
        lines: list[str] = []

        # 1. PHP itself
        ok, out = self.run_php(["--version"])
        if ok:
            first_line = out.splitlines()[0] if out else "PHP (version unknown)"
            lines.append(f"[OK]  PHP      : {first_line}")
        else:
            lines.append(f"[ERR] PHP      : {out.strip()}")

        # 2. bin/console (filesystem check – avoids running it)
        console = self.config.project_root / self.config.console_path
        if console.is_file():
            lines.append(f"[OK]  console  : {console}")
        else:
            lines.append(
                f"[ERR] console  : not found at {console} "
                "(is SYMFONY_PROJECT_ROOT set correctly?)"
            )

        # 3. Docker (only if configured)
        if self.config.docker_container:
            ok2, out2 = self._run(
                ["docker", "inspect", "--format", "{{.State.Status}}", self.config.docker_container],
                self.config.project_root,
            )
            if ok2:
                lines.append(
                    f"[OK]  docker   : container '{self.config.docker_container}' "
                    f"is {out2.strip()}"
                )
            else:
                lines.append(
                    f"[ERR] docker   : cannot inspect container "
                    f"'{self.config.docker_container}': {out2.strip()}"
                )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(self, cmd: list[str], cwd: Path) -> tuple[bool, str]:
        """Execute *cmd* in *cwd*, returning (success, output).

        Never raises – all errors are captured and returned as (False, message).
        """
        try:
            result = subprocess.run(
                cmd,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=self.config.command_timeout,
            )
            if result.returncode == 0:
                return True, result.stdout
            # Combine stdout + stderr for debugging (some tools write to stdout
            # even on failure; others write errors to stderr)
            combined = (result.stdout + result.stderr).strip()
            return False, (
                f"Command exited with code {result.returncode}.\n{combined}"
                if combined
                else f"Command exited with code {result.returncode} (no output)."
            )
        except FileNotFoundError:
            binary = cmd[0]
            hint = self._missing_binary_hint(binary)
            return False, f"Binary not found: '{binary}'. {hint}"
        except subprocess.TimeoutExpired:
            return False, (
                f"Command timed out after {self.config.command_timeout}s: {' '.join(cmd)}\n"
                "Increase COMMAND_TIMEOUT if the project is large."
            )
        except PermissionError:
            return False, f"Permission denied executing: {' '.join(cmd)}"
        except Exception as exc:  # noqa: BLE001
            return False, f"Unexpected error running {' '.join(cmd)!r}: {exc}"

    @staticmethod
    def _missing_binary_hint(binary: str) -> str:
        hints = {
            "docker": "Install Docker or unset DOCKER_CONTAINER to use a local PHP binary.",
            "php": (
                "Install PHP (e.g. `apt install php-cli`) or set DOCKER_CONTAINER / "
                "PHP_EXECUTABLE to point to your PHP runtime."
            ),
            "ddev": "Install DDEV (https://ddev.readthedocs.io) or change PHP_EXECUTABLE.",
            "lando": "Install Lando (https://lando.dev) or change PHP_EXECUTABLE.",
        }
        return hints.get(binary, "Check that the binary is installed and in PATH.")
