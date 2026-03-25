"""Tests for list_services (YAML mode — no PHP required)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from symfony_mcp.config import ServerConfig
from symfony_mcp.executor import PhpExecutor
from symfony_mcp.tools.services import list_services


@pytest.fixture()
def config(symfony_project: Path) -> ServerConfig:
    cfg = ServerConfig()
    cfg.project_root = symfony_project
    return cfg


@pytest.fixture()
def executor(config: ServerConfig) -> PhpExecutor:
    return PhpExecutor(config)


def test_reads_services_yaml(config: ServerConfig, executor: PhpExecutor) -> None:
    result = list_services(executor, config)
    assert "UserService" in result
    assert "UserRepository" in result


def test_filter_by_pattern(config: ServerConfig, executor: PhpExecutor) -> None:
    result = list_services(executor, config, filter_pattern="UserService")
    assert "UserService" in result
    assert "UserRepository" not in result


def test_filter_no_match(config: ServerConfig, executor: PhpExecutor) -> None:
    result = list_services(executor, config, filter_pattern="NonExistentXyz")
    assert "No services matched" in result


def test_global_defaults_shown(config: ServerConfig, executor: PhpExecutor) -> None:
    result = list_services(executor, config)
    assert "autowire" in result.lower()


def test_missing_services_yaml_falls_back_to_container_debug(
    tmp_path: Path,
) -> None:
    cfg = ServerConfig()
    cfg.project_root = tmp_path  # no services.yaml here
    # Mock executor to simulate PHP not available
    mock_executor = MagicMock(spec=PhpExecutor)
    mock_executor.run_console.return_value = (
        False,
        "Binary not found: 'php'",
    )
    result = list_services(mock_executor, cfg)
    assert "Error" in result or "not found" in result.lower()


def test_yaml_tags_shown(config: ServerConfig, executor: PhpExecutor) -> None:
    result = list_services(executor, config, filter_pattern="UserService")
    assert "app.service" in result
