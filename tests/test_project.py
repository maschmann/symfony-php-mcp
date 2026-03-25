"""Tests for get_project_overview."""

from __future__ import annotations

from pathlib import Path

import pytest

from symfony_mcp.tools.project import get_project_overview


def test_overview_returns_project_name(symfony_project: Path) -> None:
    result = get_project_overview(symfony_project)
    assert "acme/shop" in result


def test_overview_php_version(symfony_project: Path) -> None:
    result = get_project_overview(symfony_project)
    assert ">=8.2" in result


def test_overview_symfony_version(symfony_project: Path) -> None:
    result = get_project_overview(symfony_project)
    assert "7.1.3" in result


def test_overview_lists_packages(symfony_project: Path) -> None:
    result = get_project_overview(symfony_project)
    assert "symfony/framework-bundle" in result
    assert "doctrine/orm" in result


def test_overview_app_env(symfony_project: Path) -> None:
    result = get_project_overview(symfony_project)
    assert "dev" in result


def test_overview_psr4_namespace(symfony_project: Path) -> None:
    result = get_project_overview(symfony_project)
    assert "App\\" in result


def test_overview_missing_composer_json(tmp_path: Path) -> None:
    result = get_project_overview(tmp_path)
    assert "Error" in result
    assert "composer.json" in result


def test_overview_malformed_composer_json(tmp_path: Path) -> None:
    (tmp_path / "composer.json").write_text("{not valid json")
    result = get_project_overview(tmp_path)
    assert "Error" in result
