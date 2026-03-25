"""
symfony-php-mcp — MCP server entry point.

Registers tools that help an LLM understand and navigate a Symfony project:

  get_project_overview   – reads composer.json / symfony.lock
  find_route             – queries debug:router for a URL pattern
  analyze_twig           – extracts structure from a Twig template
  list_services          – reads config/services.yaml or debug:container
  read_code_context      – reads a PHP file with optional comment stripping
  build_index            – scans PHP files and builds a symbol index
  find_symbol            – searches the index for classes / methods by name
  search_code            – live regex search across project files

Communication: stdio (default for Claude Desktop / MCP clients).

Configuration (priority: env vars > .symfony-mcp.json > defaults):
  SYMFONY_PROJECT_ROOT   – path to the Symfony project
  PHP_EXECUTABLE         – php binary (default: "php")
  DOCKER_CONTAINER       – run PHP via `docker exec <container> php`
  DOCKER_EXEC_USER       – optional `-u <user>` for docker exec
  CONSOLE_PATH           – path to bin/console (default: "bin/console")
  COMMAND_TIMEOUT        – subprocess timeout in seconds (default: 30)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from mcp.server.fastmcp import Context, FastMCP

from .config import ServerConfig
from .executor import PhpExecutor
from .indexer import SymbolIndex
from .tools import code, project, router, services, twig
from .tools import index as index_tools


# ---------------------------------------------------------------------------
# Lifespan – initialise shared state once at startup
# ---------------------------------------------------------------------------

@dataclass
class AppContext:
    """Shared server state injected into every tool call via lifespan."""
    config: ServerConfig
    executor: PhpExecutor
    index: SymbolIndex


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[AppContext]:  # noqa: ARG001
    config = ServerConfig.load()
    executor = PhpExecutor(config)
    # Load persisted index from disk (empty if none exists yet)
    symbol_index = SymbolIndex.load(config.project_root)
    yield AppContext(config=config, executor=executor, index=symbol_index)


# ---------------------------------------------------------------------------
# FastMCP server instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="symfony-php-mcp",
    instructions=(
        "MCP server for Symfony/PHP projects. "
        "Use the available tools to inspect routes, services, Twig templates, "
        "and PHP source code. Always call get_project_overview first to understand "
        "the project structure."
    ),
    lifespan=_lifespan,
)


# ---------------------------------------------------------------------------
# Tool: get_project_overview
# ---------------------------------------------------------------------------

@mcp.tool()
def get_project_overview(ctx: Context) -> str:
    """Return a Markdown overview of the Symfony project.

    Reads composer.json, symfony.lock (or composer.lock), and .env to report:
    - PHP version requirement
    - Exact Symfony version installed
    - All installed packages, grouped by category
    - PSR-4 autoload namespaces
    - Composer scripts
    - APP_ENV setting

    Call this tool first before using other tools to understand the project.
    """
    app: AppContext = ctx.request_context.lifespan_context
    try:
        return project.get_project_overview(app.config.project_root)
    except Exception as exc:  # noqa: BLE001
        return f"**Unexpected error in get_project_overview:** {exc}"


# ---------------------------------------------------------------------------
# Tool: find_route
# ---------------------------------------------------------------------------

@mcp.tool()
def find_route(
    url_pattern: str,
    ctx: Context,
    method: str = "",
) -> str:
    """Find Symfony routes matching a URL pattern or route name.

    Runs `php bin/console debug:router --format=json` and filters the results.

    Args:
        url_pattern: Substring or regex to match against route paths and route names.
                     Examples: "/api/users", "user_show", "^/admin"
        method:      Optional HTTP method filter (GET, POST, PUT, PATCH, DELETE).
                     Empty string = no filter (matches all methods).

    Returns a Markdown table with: route name, path, allowed methods, controller.
    For single matches, the full route definition is included.

    Requires PHP and bin/console to be accessible (configure via DOCKER_CONTAINER
    or PHP_EXECUTABLE if using Docker/DDEV/Lando).
    """
    app: AppContext = ctx.request_context.lifespan_context
    try:
        return router.find_route(app.executor, app.config, url_pattern, method)
    except Exception as exc:  # noqa: BLE001
        return f"**Unexpected error in find_route:** {exc}"


# ---------------------------------------------------------------------------
# Tool: analyze_twig
# ---------------------------------------------------------------------------

@mcp.tool()
def analyze_twig(
    template_name: str,
    ctx: Context,
) -> str:
    """Analyse a Twig template and return its structural metadata.

    No PHP execution required – pure filesystem analysis.

    Extracts:
    - extends parent (inheritance chain)
    - {% include %} and {% embed %} directives
    - {% import %} and {% from ... import %} macro imports
    - {% block %} definitions
    - Template variables referenced in {{ }} expressions

    Args:
        template_name: Template path as used in Twig (e.g. "user/show.html.twig")
                       or a partial name (e.g. "show" or "user/show").
                       The templates/ directory is searched recursively.

    Returns a structured Markdown report.
    """
    app: AppContext = ctx.request_context.lifespan_context
    try:
        return twig.analyze_twig(app.config.project_root, template_name)
    except Exception as exc:  # noqa: BLE001
        return f"**Unexpected error in analyze_twig:** {exc}"


# ---------------------------------------------------------------------------
# Tool: list_services
# ---------------------------------------------------------------------------

@mcp.tool()
def list_services(
    ctx: Context,
    filter_pattern: str = "",
    use_container_debug: bool = False,
) -> str:
    """List Symfony service definitions.

    Two modes:
    1. YAML mode (default, fast, no PHP required):
       Reads config/services.yaml directly. Shows explicitly defined services.

    2. Container debug mode (use_container_debug=true):
       Runs `php bin/console debug:container --format=json`.
       Shows the full compiled container including auto-wired services
       – useful for finding framework/bundle services.

    Args:
        filter_pattern:      Regex or substring to filter service IDs or class names.
                             Examples: "App\\\\Service", "mailer", "doctrine"
                             Empty = show all defined services.
        use_container_debug: Set to true to query the full compiled DI container.
                             Requires PHP and bin/console to be accessible.

    Returns a Markdown table with: service ID, class, public flag, tags.
    """
    app: AppContext = ctx.request_context.lifespan_context
    try:
        return services.list_services(
            app.executor,
            app.config,
            filter_pattern=filter_pattern,
            use_container_debug=use_container_debug,
        )
    except Exception as exc:  # noqa: BLE001
        return f"**Unexpected error in list_services:** {exc}"


# ---------------------------------------------------------------------------
# Tool: read_code_context
# ---------------------------------------------------------------------------

@mcp.tool()
def read_code_context(
    file_path: str,
    ctx: Context,
    strip_doc_comments: bool = True,
    strip_line_comments: bool = False,
) -> str:
    """Read a file from the Symfony project, with optional comment stripping.

    Stripping PHPDoc / block comments (/* ... */) significantly reduces token
    usage without losing functional information – use strip_doc_comments=true
    (the default) for faster, cheaper analysis.

    Args:
        file_path:           Path relative to the project root.
                             Examples: "src/Controller/UserController.php"
                                       "src/Entity/User.php"
                             Absolute paths inside the project are also accepted.
        strip_doc_comments:  Remove /** ... */ and /* ... */ blocks.
                             Default: true.
        strip_line_comments: Also remove // single-line comments.
                             Default: false (inline comments are useful context).

    Returns the file content with line numbers and a token-savings summary.

    Security: only files inside the project root can be read (path traversal
    is blocked).
    """
    app: AppContext = ctx.request_context.lifespan_context
    try:
        return code.read_code_context(
            app.config.project_root,
            file_path,
            strip_doc_comments=strip_doc_comments,
            strip_line_comments=strip_line_comments,
        )
    except Exception as exc:  # noqa: BLE001
        return f"**Unexpected error in read_code_context:** {exc}"


# ---------------------------------------------------------------------------
# Tool: build_index
# ---------------------------------------------------------------------------

@mcp.tool()
def build_index(
    ctx: Context,
    directories: list[str] | None = None,
    force: bool = False,
) -> str:
    """Scan PHP files and build (or update) the symbol index.

    The index stores all classes, interfaces, traits, enums, and their methods
    so that find_symbol can locate any symbol instantly without re-scanning.

    The index is persisted to <project_root>/.symfony-mcp-index.json and loaded
    automatically on next server start. Only changed files are re-scanned
    (incremental), so subsequent calls are fast.

    Args:
        directories: Directories to scan, relative to the project root.
                     Default: auto-detected (src/, app/, lib/).
                     Example: ["src", "lib"]
        force:       Re-scan every file even if it hasn't changed.
                     Use this after a major refactor or rename.

    Run this once after pointing the server at a new project, then again
    after significant code changes.
    """
    app: AppContext = ctx.request_context.lifespan_context
    try:
        return index_tools.build_index(
            app.index,
            app.config,
            directories=directories,
            force=force,
        )
    except Exception as exc:  # noqa: BLE001
        return f"**Unexpected error in build_index:** {exc}"


# ---------------------------------------------------------------------------
# Tool: find_symbol
# ---------------------------------------------------------------------------

@mcp.tool()
def find_symbol(
    name: str,
    ctx: Context,
    kind: str = "",
) -> str:
    """Search the symbol index for a PHP class, interface, trait, enum, or method.

    Returns file paths and line numbers — use the result directly with
    read_code_context to inspect the implementation.

    Requires build_index to have been run at least once.

    Args:
        name: Name to search for (case-insensitive substring or full name).
              Examples: "UserController", "UserRepo", "findByEmail", "App\\Entity"
        kind: Optional filter. One of: class, interface, trait, enum, method.
              Empty = search all symbol types.

    Typical workflow:
      1. build_index          — index the project (once, or after big changes)
      2. find_symbol "Foo"    — locate the file and line
      3. read_code_context    — read the file for full implementation
    """
    app: AppContext = ctx.request_context.lifespan_context
    try:
        return index_tools.find_symbol(
            app.index,
            name,
            kind=kind or None,
        )
    except Exception as exc:  # noqa: BLE001
        return f"**Unexpected error in find_symbol:** {exc}"


# ---------------------------------------------------------------------------
# Tool: search_code
# ---------------------------------------------------------------------------

@mcp.tool()
def search_code(
    pattern: str,
    ctx: Context,
    path_glob: str = "**/*.php",
    context_lines: int = 2,
    max_results: int = 30,
) -> str:
    """Live regex/substring search across project files.

    No index required — scans files directly each time.
    Useful for finding usages, checking for patterns, or searching non-PHP files.

    Args:
        pattern:       Python regex or plain substring to search for.
                       Examples: "UserRepository", "findByEmail\\(", "#\\[Route"
        path_glob:     Glob relative to project root.
                       Default: **/*.php
                       Other examples: src/**/*.php, templates/**/*.twig,
                       config/**/*.yaml, **/*.php
        context_lines: Lines of surrounding context to show (0–5). Default: 2.
        max_results:   Max matching snippets to return (1–200). Default: 30.

    Returns highlighted snippets with file path and line number.
    """
    app: AppContext = ctx.request_context.lifespan_context
    try:
        return index_tools.search_code(
            app.config.project_root,
            pattern,
            path_glob=path_glob,
            context_lines=context_lines,
            max_results=max_results,
        )
    except Exception as exc:  # noqa: BLE001
        return f"**Unexpected error in search_code:** {exc}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Start the MCP server using stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
