"""Tests for the PHP symbol indexer and related tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from symfony_mcp.config import ServerConfig
from symfony_mcp.indexer import INDEX_FILE_NAME, SymbolIndex, _parse_file
from symfony_mcp.tools.index import build_index, find_symbol, search_code


# ---------------------------------------------------------------------------
# SymbolIndex — build & query
# ---------------------------------------------------------------------------

@pytest.fixture()
def built_index(symfony_project: Path) -> SymbolIndex:
    idx = SymbolIndex(project_root=symfony_project)
    idx.build(directories=["src"])
    return idx


def test_index_finds_controller_class(built_index: SymbolIndex) -> None:
    results = built_index.find_symbol("UserController")
    assert any(r.name == "UserController" for r in results)


def test_index_finds_service_class(built_index: SymbolIndex) -> None:
    results = built_index.find_symbol("UserService")
    assert any(r.name == "UserService" for r in results)


def test_index_finds_interface(built_index: SymbolIndex) -> None:
    results = built_index.find_symbol("UserProviderInterface")
    assert results, "Interface should be indexed"
    assert results[0].kind == "interface"


def test_index_finds_methods(built_index: SymbolIndex) -> None:
    results = built_index.find_symbol("findAll")
    assert results, "findAll method should be indexed"
    names = [r.name for r in results]
    assert "findAll" in names


def test_index_kind_filter_class(built_index: SymbolIndex) -> None:
    results = built_index.find_symbol("User", kind="class")
    assert all(hasattr(r, "kind") and r.kind == "class" for r in results)


def test_index_kind_filter_method(built_index: SymbolIndex) -> None:
    results = built_index.find_symbol("findAll", kind="method")
    assert results
    assert all(hasattr(r, "is_static") for r in results)  # MethodSymbol


def test_index_fqn(built_index: SymbolIndex) -> None:
    results = built_index.find_symbol("UserController")
    cls = next(r for r in results if r.name == "UserController")
    assert cls.fqn == "App\\Controller\\UserController"


def test_index_extends(built_index: SymbolIndex) -> None:
    results = built_index.find_symbol("UserController")
    cls = next(r for r in results if r.name == "UserController")
    assert any("AbstractController" in e for e in cls.extends)


def test_index_file_path(built_index: SymbolIndex) -> None:
    results = built_index.find_symbol("UserController")
    cls = next(r for r in results if r.name == "UserController")
    assert "UserController.php" in cls.file


def test_index_route_prefix(built_index: SymbolIndex) -> None:
    results = built_index.find_symbol("UserController")
    cls = next(r for r in results if r.name == "UserController")
    assert cls.route_prefix == "/user"


def test_index_method_route(built_index: SymbolIndex) -> None:
    results = built_index.find_symbol("index", kind="method")
    method = next((r for r in results if r.name == "index"), None)
    assert method is not None
    assert method.route_path == "/"


def test_index_method_visibility(built_index: SymbolIndex) -> None:
    results = built_index.find_symbol("privateHelper")
    assert results
    assert results[0].visibility == "protected"


def test_index_stats(built_index: SymbolIndex) -> None:
    stats = built_index.stats()
    assert stats["files"] >= 4
    assert stats["classes"] >= 4
    assert stats["methods"] >= 5


def test_index_no_match(built_index: SymbolIndex) -> None:
    results = built_index.find_symbol("AbsolutelyNonExistentClass")
    assert results == []


# ---------------------------------------------------------------------------
# Persistence (save / load round-trip)
# ---------------------------------------------------------------------------

def test_save_and_load(built_index: SymbolIndex, symfony_project: Path) -> None:
    built_index.save()
    assert (symfony_project / INDEX_FILE_NAME).is_file()

    loaded = SymbolIndex.load(symfony_project)
    assert loaded.stats()["files"] == built_index.stats()["files"]
    assert loaded.stats()["classes"] == built_index.stats()["classes"]


def test_load_nonexistent_returns_empty(tmp_path: Path) -> None:
    idx = SymbolIndex.load(tmp_path)
    assert idx.files == {}


def test_load_corrupt_json_returns_empty(symfony_project: Path) -> None:
    (symfony_project / INDEX_FILE_NAME).write_text("{corrupt")
    idx = SymbolIndex.load(symfony_project)
    assert idx.files == {}


def test_incremental_update(built_index: SymbolIndex, symfony_project: Path) -> None:
    built_index.save()
    stats_first = built_index.stats()

    # Add a new PHP file
    new_file = symfony_project / "src" / "Service" / "OrderService.php"
    new_file.write_text(
        "<?php\nnamespace App\\Service;\nclass OrderService {\n"
        "    public function placeOrder(): void {}\n}\n"
    )

    # Re-build without force — only new file should be scanned
    stats = built_index.build(directories=["src"])
    assert stats["updated"] >= 1
    assert stats["skipped"] >= stats_first["files"] - 1

    results = built_index.find_symbol("OrderService")
    assert results


def test_force_rebuild(built_index: SymbolIndex) -> None:
    stats = built_index.build(directories=["src"], force=True)
    assert stats["skipped"] == 0
    assert stats["updated"] == stats["scanned"]


# ---------------------------------------------------------------------------
# build_index tool
# ---------------------------------------------------------------------------

@pytest.fixture()
def config(symfony_project: Path) -> ServerConfig:
    cfg = ServerConfig()
    cfg.project_root = symfony_project
    return cfg


def test_build_index_tool_output(symfony_project: Path, config: ServerConfig) -> None:
    idx = SymbolIndex(project_root=symfony_project)
    result = build_index(idx, config, directories=["src"])
    assert "updated" in result.lower() or "rebuilt" in result.lower()
    assert "PHP files indexed" in result


def test_build_index_creates_json_file(
    symfony_project: Path, config: ServerConfig
) -> None:
    idx = SymbolIndex(project_root=symfony_project)
    build_index(idx, config, directories=["src"])
    assert (symfony_project / INDEX_FILE_NAME).is_file()


def test_build_index_empty_project(tmp_path: Path) -> None:
    cfg = ServerConfig()
    cfg.project_root = tmp_path
    idx = SymbolIndex(project_root=tmp_path)
    result = build_index(idx, cfg)
    assert "No scannable directories" in result


# ---------------------------------------------------------------------------
# find_symbol tool
# ---------------------------------------------------------------------------

def test_find_symbol_tool_empty_index(symfony_project: Path) -> None:
    idx = SymbolIndex(project_root=symfony_project)  # empty, not built
    result = find_symbol(idx, "UserController")
    assert "build_index" in result


def test_find_symbol_tool_found(symfony_project: Path) -> None:
    idx = SymbolIndex(project_root=symfony_project)
    idx.build(directories=["src"])
    result = find_symbol(idx, "UserController")
    assert "UserController" in result
    assert "UserController.php" in result


def test_find_symbol_tool_not_found(symfony_project: Path) -> None:
    idx = SymbolIndex(project_root=symfony_project)
    idx.build(directories=["src"])
    result = find_symbol(idx, "AbsolutelyNonExistent")
    assert "No symbols matched" in result


def test_find_symbol_tool_kind_filter(symfony_project: Path) -> None:
    idx = SymbolIndex(project_root=symfony_project)
    idx.build(directories=["src"])
    result = find_symbol(idx, "User", kind="interface")
    assert "UserProviderInterface" in result
    # Should not show classes when filtering for interface
    assert "UserController" not in result


def test_find_symbol_tool_empty_name(symfony_project: Path) -> None:
    idx = SymbolIndex(project_root=symfony_project)
    idx.build(directories=["src"])
    result = find_symbol(idx, "")
    assert "Error" in result


# ---------------------------------------------------------------------------
# search_code tool
# ---------------------------------------------------------------------------

def test_search_code_finds_pattern(symfony_project: Path) -> None:
    result = search_code(symfony_project, "UserService")
    assert "UserService" in result
    assert ">>>" in result  # match indicator


def test_search_code_file_glob(symfony_project: Path) -> None:
    result = search_code(
        symfony_project,
        "extends",
        path_glob="templates/**/*.twig",
    )
    assert "extends" in result


def test_search_code_no_match(symfony_project: Path) -> None:
    result = search_code(symfony_project, "AbsolutelyNeverInAnyFile12345")
    assert "No matches" in result


def test_search_code_invalid_regex(symfony_project: Path) -> None:
    result = search_code(symfony_project, "[invalid(regex")
    assert "Invalid regex" in result


def test_search_code_context_lines(symfony_project: Path) -> None:
    result = search_code(
        symfony_project,
        "public function index",
        path_glob="src/**/*.php",
        context_lines=3,
    )
    # With 3 lines of context, we should see surrounding lines
    assert ">>>" in result
    lines = result.splitlines()
    snippet_lines = [l for l in lines if "|" in l and ">>>" in l or "    " in l]
    assert len(snippet_lines) >= 2


def test_search_code_max_results(symfony_project: Path) -> None:
    # Create many files with the same pattern
    for i in range(10):
        f = symfony_project / "src" / f"Dummy{i}.php"
        f.write_text(f"<?php\nclass Dummy{i} {{\n    // FINDME\n}}\n")

    result = search_code(
        symfony_project,
        "FINDME",
        path_glob="src/**/*.php",
        max_results=3,
    )
    assert "first 3" in result or "showing 3" in result.lower()


def test_search_code_invalid_glob(symfony_project: Path) -> None:
    # An empty glob result is a graceful error
    result = search_code(symfony_project, "foo", path_glob="nonexistent_dir/**/*.php")
    assert "No files matched" in result or "No matches" in result


def test_search_code_reports_file_count(symfony_project: Path) -> None:
    result = search_code(symfony_project, "namespace App")
    assert "file" in result.lower()
