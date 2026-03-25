# symfony-php-mcp

A production-ready **Model Context Protocol (MCP) server** that bridges your Symfony/PHP project with LLMs such as Claude. It exposes tools that let the AI read your project's routes, services, Twig templates, and PHP source code — without ever needing direct filesystem access from the LLM itself.

```
Claude Desktop / Claude Code
        │
        │  MCP (stdio)
        ▼
symfony-php-mcp  ──► reads ──► composer.json, symfony.lock, services.yaml, *.twig
                 ──► runs  ──► php bin/console debug:router / debug:container
```

---

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Installation](#installation)
  - [Via uvx (recommended)](#via-uvx-recommended)
  - [Via local clone](#via-local-clone)
- [Configuration](#configuration)
  - [Environment Variables](#environment-variables)
  - [Project-local config file](#project-local-config-file-symfony-mcpjson)
  - [Configuration priority](#configuration-priority)
- [Claude Desktop Setup](#claude-desktop-setup)
  - [Local PHP](#local-php)
  - [Docker](#docker)
  - [DDEV](#ddev)
  - [Lando](#lando)
  - [Sail (Laravel-style Docker)](#sail)
- [Tools Reference](#tools-reference)
  - [get_project_overview](#get_project_overview)
  - [find_route](#find_route)
  - [analyze_twig](#analyze_twig)
  - [list_services](#list_services)
  - [read_code_context](#read_code_context)
- [Docker / Container Environments](#docker--container-environments-in-depth)
- [Development](#development)
- [Troubleshooting](#troubleshooting)

---

## Features

| Tool | What it does | PHP needed? |
|------|-------------|-------------|
| `get_project_overview` | Reads `composer.json` + `symfony.lock` – PHP version, Symfony version, all packages | No |
| `find_route` | Runs `debug:router --format=json`, filters by URL pattern and HTTP method | Yes |
| `analyze_twig` | Finds a `.twig` file and extracts `extends`, `include`, `block`, macros | No |
| `list_services` | Reads `config/services.yaml` or runs `debug:container --format=json` | Optional |
| `read_code_context` | Reads a PHP file, strips doc-block comments to save tokens | No |

---

## Quick Start

```bash
# Requires uv — https://docs.astral.sh/uv/
SYMFONY_PROJECT_ROOT=/path/to/your/symfony/app uvx symfony-php-mcp
```

The server speaks MCP over **stdio** and is designed to be launched by your MCP client (Claude Desktop, Claude Code, etc.), not run manually.

---

## Installation

### Via uvx (recommended)

`uvx` runs the package from the PyPI / GitHub registry with no permanent install:

```jsonc
// claude_desktop_config.json
{
  "mcpServers": {
    "symfony": {
      "command": "uvx",
      "args": ["symfony-php-mcp"],
      "env": {
        "SYMFONY_PROJECT_ROOT": "/path/to/your/symfony-app"
      }
    }
  }
}
```

Install from GitHub (before it's on PyPI):

```jsonc
{
  "mcpServers": {
    "symfony": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/maschmann/symfony-php-mcp", "symfony-mcp"],
      "env": {
        "SYMFONY_PROJECT_ROOT": "/path/to/your/symfony-app"
      }
    }
  }
}
```

### Via local clone

```bash
git clone https://github.com/maschmann/symfony-php-mcp
cd symfony-php-mcp
uv sync
```

```jsonc
{
  "mcpServers": {
    "symfony": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/symfony-php-mcp", "symfony-mcp"],
      "env": {
        "SYMFONY_PROJECT_ROOT": "/path/to/your/symfony-app"
      }
    }
  }
}
```

---

## Configuration

Configuration is resolved in priority order: **environment variables → `.symfony-mcp.json` → built-in defaults**.

### Environment Variables

Set these in your MCP client's `env` block.

| Variable | Default | Description |
|----------|---------|-------------|
| `SYMFONY_PROJECT_ROOT` | current working directory | **Required.** Absolute path to your Symfony project (the directory containing `composer.json`). |
| `PHP_EXECUTABLE` | `php` | PHP binary or wrapper command. Use `ddev php`, `lando php`, or a full path like `/usr/bin/php8.3`. |
| `DOCKER_CONTAINER` | *(none)* | Docker container name. When set, commands run as `docker exec <container> php …`. Takes precedence over `PHP_EXECUTABLE`. |
| `DOCKER_EXEC_USER` | *(none)* | Optional `-u <user>` for `docker exec`. Useful when the container runs PHP as `www-data`. |
| `CONSOLE_PATH` | `bin/console` | Path to `bin/console` relative to `SYMFONY_PROJECT_ROOT`. Rarely needs changing. |
| `COMMAND_TIMEOUT` | `30` | Seconds before a PHP subprocess is killed. Increase for large projects or slow containers. |

### Project-local config file (`.symfony-mcp.json`)

Place this file in your **Symfony project root** to commit PHP runtime preferences alongside the project code. Great for teams using DDEV or Docker Compose.

```json
{
  "php_executable": "php",
  "docker_container": null,
  "docker_exec_user": null,
  "console_path": "bin/console",
  "command_timeout": 30
}
```

**Example for a DDEV project:**

```json
{
  "php_executable": "ddev php",
  "command_timeout": 60
}
```

**Example for a Docker Compose project:**

```json
{
  "docker_container": "my-project-php-1",
  "docker_exec_user": "www-data",
  "command_timeout": 45
}
```

### Configuration priority

```
Environment variables (MCP client env block)
        ↓  (override)
.symfony-mcp.json  (in SYMFONY_PROJECT_ROOT)
        ↓  (override)
Built-in defaults
```

Environment variables always win. This means you can commit a `.symfony-mcp.json` with sensible defaults for your team while still being able to override them per-machine via env vars.

---

## Claude Desktop Setup

The `claude_desktop_config.json` file lives at:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux:** `~/.config/Claude/claude_desktop_config.json`

### Local PHP

```jsonc
{
  "mcpServers": {
    "symfony": {
      "command": "uvx",
      "args": ["symfony-php-mcp"],
      "env": {
        "SYMFONY_PROJECT_ROOT": "/home/alice/projects/my-symfony-app"
      }
    }
  }
}
```

### Docker

Works with any Docker Compose project. The container must be **running** when Claude Desktop starts (or before you use the tools).

```jsonc
{
  "mcpServers": {
    "symfony": {
      "command": "uvx",
      "args": ["symfony-php-mcp"],
      "env": {
        "SYMFONY_PROJECT_ROOT": "/home/alice/projects/my-symfony-app",
        "DOCKER_CONTAINER": "my-symfony-app-php-1",
        "DOCKER_EXEC_USER": "www-data"
      }
    }
  }
}
```

> **Finding your container name:** run `docker ps` and look at the `NAMES` column.

### DDEV

```jsonc
{
  "mcpServers": {
    "symfony": {
      "command": "uvx",
      "args": ["symfony-php-mcp"],
      "env": {
        "SYMFONY_PROJECT_ROOT": "/home/alice/projects/my-symfony-app",
        "PHP_EXECUTABLE": "ddev php"
      }
    }
  }
}
```

Alternatively, commit a `.symfony-mcp.json` to your project:

```json
{
  "php_executable": "ddev php"
}
```

Then the MCP config only needs `SYMFONY_PROJECT_ROOT`.

### Lando

```jsonc
{
  "mcpServers": {
    "symfony": {
      "command": "uvx",
      "args": ["symfony-php-mcp"],
      "env": {
        "SYMFONY_PROJECT_ROOT": "/home/alice/projects/my-symfony-app",
        "PHP_EXECUTABLE": "lando php"
      }
    }
  }
}
```

### Sail

Laravel Sail is a thin Docker wrapper but the pattern works for Symfony projects using a similar setup:

```jsonc
{
  "env": {
    "SYMFONY_PROJECT_ROOT": "/home/alice/projects/my-project",
    "DOCKER_CONTAINER": "my-project-laravel.test-1"
  }
}
```

---

## Tools Reference

### `get_project_overview`

Returns a Markdown summary of the project. **Call this first** to give the LLM context before using other tools.

**Parameters:** none

**Returns:**
```markdown
# Symfony Project: `acme/store`
> An e-commerce platform built with Symfony

## Runtime
| Key | Value |
|-----|-------|
| PHP requirement | `>=8.2` |
| Symfony version | `7.1.3` |
| APP_ENV | `dev` |

## Installed Packages
### Symfony Components
| Package | Version | Dev? |
...
```

---

### `find_route`

Finds routes matching a URL pattern or route name.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url_pattern` | string | Yes | Substring or regex to match against route paths and names. E.g. `/api/user`, `user_show`, `^/admin` |
| `method` | string | No | HTTP method filter: `GET`, `POST`, `PUT`, `PATCH`, `DELETE`. Empty = any. |

**Example:**

```
Tool: find_route
url_pattern: /api/user
method: GET
```

```markdown
Found **3** route(s) matching `/api/user`

| Route Name | Path | Methods | Controller |
|------------|------|---------|------------|
| `api_user_list` | `/api/users` | `GET` | `UserController::list` |
| `api_user_show` | `/api/users/{id}` | `GET` | `UserController::show` |
| `api_user_me`   | `/api/user/me`    | `GET` | `UserController::me`   |
```

**Requires:** PHP + `bin/console`

---

### `analyze_twig`

Analyses a Twig template without running PHP.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `template_name` | string | Yes | Template name as used in Twig, or a partial name. E.g. `user/show.html.twig`, `show`, `user/` |

**Example:**

```
Tool: analyze_twig
template_name: user/show.html.twig
```

```markdown
## Template: `templates/user/show.html.twig`

### Inheritance
Extends: `base.html.twig`

### Included / Embedded Templates
| Type | Template |
|------|----------|
| include | `_partials/user_card.html.twig` |
| include | `_partials/breadcrumb.html.twig` |

### Defined Blocks
`title`, `content`, `scripts`

### Template Variables
`user`, `posts`, `pagination`
```

---

### `list_services`

Lists service definitions from `config/services.yaml` or the compiled container.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `filter_pattern` | string | `""` | Regex/substring to filter by service ID or class. E.g. `App\\Service`, `mailer`, `doctrine` |
| `use_container_debug` | bool | `false` | Use `debug:container --format=json` instead of YAML. Required to see auto-wired / framework services. |

**Example – find all mailer services:**

```
Tool: list_services
filter_pattern: mailer
use_container_debug: true
```

---

### `read_code_context`

Reads a PHP file with optional comment stripping to reduce token usage.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file_path` | string | — | Path relative to project root. E.g. `src/Controller/UserController.php` |
| `strip_doc_comments` | bool | `true` | Strip `/** … */` and `/* … */` blocks. Saves ~20-40% tokens on typical controllers. |
| `strip_line_comments` | bool | `false` | Also strip `//` comments. Use when you need maximum token efficiency. |

**Example:**

```
Tool: read_code_context
file_path: src/Controller/UserController.php
```

```markdown
## `src/Controller/UserController.php`

| Property | Value |
|----------|-------|
| Original  | 187 lines / 6,842 chars |
| After processing | 134 lines / 4,201 chars |
| Token savings | ~39% (block comments stripped) |

```php
  1 | <?php
  2 |
  3 | namespace App\Controller;
...
```

---

## Docker / Container Environments (in depth)

### How command routing works

When `DOCKER_CONTAINER` is set, every PHP/console invocation becomes:

```bash
docker exec [-u <DOCKER_EXEC_USER>] <DOCKER_CONTAINER> php bin/console <args>
```

When `PHP_EXECUTABLE` is set to `ddev php`:

```bash
ddev php bin/console <args>
```

The server **never modifies** the project files inside the container.

### Docker Compose tips

1. **Container name** – use `docker ps` to find the exact name. For `docker compose`, it's usually `<project>-<service>-1`.

2. **File paths** – `SYMFONY_PROJECT_ROOT` should point to the **host** path since the server reads files directly via the Python filesystem layer. Only `debug:router` / `debug:container` commands run inside the container.

3. **Working directory** – commands are run in `SYMFONY_PROJECT_ROOT` on the host. If your container mounts the project at a different path, set `CONSOLE_PATH` accordingly — or ensure `bin/console` is accessible from the host path.

4. **Container not running** – the server will return a helpful error. Start your containers first: `docker compose up -d`.

### DDEV tips

```bash
# List DDEV projects
ddev list

# Ensure the project is running
ddev start

# Test PHP is accessible
ddev php --version
```

`.symfony-mcp.json` for DDEV:

```json
{
  "php_executable": "ddev php",
  "command_timeout": 60
}
```

### Lando tips

```bash
# Ensure the project is running
lando start

# Test PHP is accessible
lando php --version
```

`.symfony-mcp.json` for Lando:

```json
{
  "php_executable": "lando php"
}
```

---

## Development

```bash
# Clone
git clone https://github.com/maschmann/symfony-php-mcp
cd symfony-php-mcp

# Install dependencies (requires uv — https://docs.astral.sh/uv/)
uv sync

# Run the server (will block waiting for MCP stdio input)
uv run symfony-php-mcp

# Run tests
uv run pytest

# Lint
uv run ruff check src/
uv run ruff format --check src/
```

### Project structure

```
src/symfony_mcp/
├── server.py        # FastMCP server, lifespan, tool decorators, main()
├── config.py        # ServerConfig: env vars + .symfony-mcp.json merge logic
├── executor.py      # PhpExecutor: subprocess wrapper with Docker/wrapper support
└── tools/
    ├── project.py   # get_project_overview
    ├── router.py    # find_route
    ├── twig.py      # analyze_twig
    ├── services.py  # list_services
    └── code.py      # read_code_context
```

### Adding a new tool

1. Create `src/symfony_mcp/tools/my_tool.py` with a plain function.
2. Register it in `server.py` with `@mcp.tool()`.
3. Add a docstring – FastMCP uses it as the tool description.

---

## Troubleshooting

### "Binary not found: 'php'"

PHP is not in the PATH used by the MCP server process.

**Fix options:**
- Install PHP: `apt install php-cli` / `brew install php`
- Use a full path: `PHP_EXECUTABLE=/usr/bin/php8.3`
- Use Docker: `DOCKER_CONTAINER=my-php-container`
- Use DDEV: `PHP_EXECUTABLE=ddev php`

### "Symfony console not found"

`SYMFONY_PROJECT_ROOT` is pointing to the wrong directory, or `bin/console` is missing.

**Fix:** Make sure `SYMFONY_PROJECT_ROOT` is the directory that contains both `composer.json` and `bin/console`.

```bash
ls /your/project/bin/console   # should exist
```

### "Command timed out"

The PHP command took longer than `COMMAND_TIMEOUT` seconds.

**Fix:** Increase the timeout:
```json
// .symfony-mcp.json
{ "command_timeout": 120 }
```
Or set `COMMAND_TIMEOUT=120` in the MCP env block.

### "Cannot inspect container"

The Docker container is not running.

**Fix:**
```bash
docker compose up -d           # Docker Compose
ddev start                     # DDEV
lando start                    # Lando
```

### Claude Desktop doesn't see the server

1. Restart Claude Desktop after editing `claude_desktop_config.json`.
2. Check the MCP server logs in Claude Desktop → Settings → Developer → MCP Servers.
3. Run the server manually to check for errors:
   ```bash
   SYMFONY_PROJECT_ROOT=/path/to/project uvx symfony-php-mcp
   ```
   It should start silently (waiting for stdio input). Any error on startup will print to stderr.

### "Error parsing config/services.yaml"

Your `services.yaml` has a syntax error or uses YAML features not supported by PyYAML.

**Fix:** Use `use_container_debug=true` in `list_services` as a fallback:
```
Tool: list_services
filter_pattern: App\
use_container_debug: true
```

---

## License

MIT — see [LICENSE](LICENSE).
