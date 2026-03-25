"""Tests for read_code_context."""

from __future__ import annotations

from pathlib import Path

import pytest

from symfony_mcp.tools.code import read_code_context


def test_reads_file_with_line_numbers(symfony_project: Path) -> None:
    result = read_code_context(symfony_project, "src/Controller/UserController.php")
    assert "UserController" in result
    assert " | " in result  # line number format "  1 | <?php"


def test_strips_doc_block_comments(symfony_project: Path) -> None:
    result = read_code_context(
        symfony_project,
        "src/Controller/UserController.php",
        strip_doc_comments=True,
    )
    assert "Controller for user-related actions" not in result
    assert "This is a doc-block comment" not in result


def test_preserves_content_when_not_stripping(symfony_project: Path) -> None:
    result = read_code_context(
        symfony_project,
        "src/Controller/UserController.php",
        strip_doc_comments=False,
    )
    assert "Controller for user-related actions" in result


def test_inline_block_comment_stripped(symfony_project: Path) -> None:
    result = read_code_context(
        symfony_project,
        "src/Controller/UserController.php",
        strip_doc_comments=True,
    )
    assert "inline block comment" not in result


def test_token_savings_summary(symfony_project: Path) -> None:
    result = read_code_context(
        symfony_project,
        "src/Controller/UserController.php",
        strip_doc_comments=True,
    )
    assert "Token savings" in result
    assert "Original" in result


def test_path_traversal_blocked(symfony_project: Path) -> None:
    result = read_code_context(symfony_project, "../../etc/passwd")
    assert "Security error" in result or "outside the project" in result


def test_file_not_found(symfony_project: Path) -> None:
    result = read_code_context(symfony_project, "src/Controller/NonExistent.php")
    assert "not found" in result.lower()


def test_empty_path_returns_error(symfony_project: Path) -> None:
    result = read_code_context(symfony_project, "")
    assert "Error" in result


def test_absolute_path_inside_project(symfony_project: Path) -> None:
    abs_path = str(symfony_project / "src" / "Controller" / "UserController.php")
    result = read_code_context(symfony_project, abs_path)
    assert "UserController" in result
    assert "Error" not in result


def test_strip_line_comments(symfony_project: Path) -> None:
    # Add a file with line comments
    php = symfony_project / "src" / "HasComments.php"
    php.write_text("<?php\n// this is a line comment\n$x = 1; // inline\necho $x;\n")
    result = read_code_context(
        symfony_project,
        "src/HasComments.php",
        strip_doc_comments=False,
        strip_line_comments=True,
    )
    assert "this is a line comment" not in result
    assert "echo $x" in result
