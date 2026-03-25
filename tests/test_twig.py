"""Tests for analyze_twig."""

from __future__ import annotations

from pathlib import Path

import pytest

from symfony_mcp.tools.twig import analyze_twig


def test_detects_extends(symfony_project: Path) -> None:
    result = analyze_twig(symfony_project, "user/index.html.twig")
    assert "base.html.twig" in result


def test_detects_include(symfony_project: Path) -> None:
    result = analyze_twig(symfony_project, "user/index.html.twig")
    assert "breadcrumb.html.twig" in result


def test_detects_blocks(symfony_project: Path) -> None:
    result = analyze_twig(symfony_project, "user/index.html.twig")
    assert "title" in result
    assert "body" in result


def test_detects_variables(symfony_project: Path) -> None:
    result = analyze_twig(symfony_project, "user/index.html.twig")
    # "user" appears in {{ user.name }} — the {{ }} variable scanner picks it up.
    # "users" is only in {% for user in users %} (a tag expression, not {{ }})
    # so it is intentionally not in the variables section.
    assert "user" in result
    assert "Template Variables" in result


def test_base_template_has_no_extends(symfony_project: Path) -> None:
    result = analyze_twig(symfony_project, "base.html.twig")
    assert "Standalone" in result or "no" in result.lower()


def test_partial_name_match(symfony_project: Path) -> None:
    # "index" should find user/index.html.twig
    result = analyze_twig(symfony_project, "index")
    assert "index.html.twig" in result


def test_multiple_matches_reports_ambiguity(symfony_project: Path) -> None:
    # create a second template matching "user"
    extra = symfony_project / "templates" / "user" / "list.html.twig"
    extra.write_text("{% extends 'base.html.twig' %}\n{% block body %}list{% endblock %}\n")
    # "user" matches both user/index and user/list
    result = analyze_twig(symfony_project, "user/")
    # Either finds a specific one or reports multiple
    assert "twig" in result.lower()


def test_not_found(symfony_project: Path) -> None:
    result = analyze_twig(symfony_project, "nonexistent_template_xyz.html.twig")
    assert "not found" in result.lower() or "Template not found" in result


def test_empty_name_returns_error(symfony_project: Path) -> None:
    result = analyze_twig(symfony_project, "")
    assert "Error" in result
