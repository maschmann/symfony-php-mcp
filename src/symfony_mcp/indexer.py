"""
PHP symbol indexer — pure-Python, regex-based, no external dependencies.

Scans PHP source files and extracts:
  - Namespaces, use-imports
  - Classes, abstract classes, final classes
  - Interfaces, traits, enums
  - Methods (with visibility, static flag, return type)
  - Class-level constants
  - PHP 8 attributes on classes and methods (#[Route(...)], #[Autowire], …)

The index is persisted as JSON to <project_root>/.symfony-mcp-index.json so it
survives server restarts and only re-scans files whose mtime has changed.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

INDEX_FILE_NAME = ".symfony-mcp-index.json"
INDEX_VERSION = 2

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_RE_NAMESPACE = re.compile(r"^namespace\s+([\w\\]+)\s*;", re.MULTILINE)
_RE_USE = re.compile(r"^use\s+([\w\\]+)(?:\s+as\s+(\w+))?\s*;", re.MULTILINE)

# Catches: [abstract|final] class Foo [extends Bar] [implements A, B]
# extends/implements use a greedy class-name list pattern (no lazy quantifier)
# to avoid the lazy +? matching just one character when the group is optional.
_RE_CLASS = re.compile(
    r"^(?P<modifiers>(?:(?:abstract|final|readonly)\s+)*)"
    r"(?P<kind>class|interface|trait|enum)\s+"
    r"(?P<name>\w+)"
    r"(?:\s*:\s*(?P<enum_type>\w+))?"
    r"(?:\s+extends\s+(?P<extends>[\w\\]+(?:\s*,\s*[\w\\]+)*))?"
    r"(?:\s+implements\s+(?P<implements>[\w\\]+(?:\s*,\s*[\w\\]+)*))?",
    re.MULTILINE,
)

# Method declarations — no attrs group; we do a targeted look-back separately.
_RE_METHOD = re.compile(
    r"(?P<visibility>public|protected|private)\s+"
    r"(?P<modifiers>(?:(?:static|abstract|final|readonly)\s+)*)"
    r"function\s+(?P<name>\w+)\s*\(",
    re.MULTILINE,
)

# Class constants: public const FOO = …
_RE_CONST = re.compile(
    r"^\s*(?:public|protected|private)?\s*const\s+(\w+)\s*=",
    re.MULTILINE,
)

# PHP 8 attributes — handles one level of nested brackets e.g. methods: ['GET']
_RE_ATTR_LINE = re.compile(r"#\[((?:[^\[\]]|\[[^\]]*\])*)\]")

# Route attribute specifically — capture the path
_RE_ROUTE_ATTR = re.compile(
    r"""#\[Route\(\s*['"]([^'"]+)['"](?:[^)]*methods\s*:\s*\[([^\]]*)\])?""",
    re.IGNORECASE,
)

# Single-line doc-comment to grab @param / @return for method context
_RE_RETURN_TYPE = re.compile(r"->\s*([\w\\|?]+)\s*(?:\{|$|;)", re.MULTILINE)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

SymbolKind = Literal["class", "interface", "trait", "enum", "method", "const", "function"]


@dataclass
class MethodSymbol:
    name: str
    fqn: str                         # App\Controller\FooController::bar
    visibility: str                  # public / protected / private / ""
    is_static: bool
    is_abstract: bool
    line: int
    attributes: list[str] = field(default_factory=list)
    route_path: Optional[str] = None
    route_methods: list[str] = field(default_factory=list)


@dataclass
class ClassSymbol:
    name: str
    fqn: str                         # App\Controller\FooController
    kind: str                        # class / interface / trait / enum
    modifiers: list[str]             # abstract, final, readonly
    extends: list[str]
    implements: list[str]
    line: int
    file: str                        # relative to project_root
    attributes: list[str] = field(default_factory=list)
    methods: list[MethodSymbol] = field(default_factory=list)
    constants: list[str] = field(default_factory=list)
    route_prefix: Optional[str] = None


@dataclass
class FileIndex:
    rel_path: str
    mtime: float
    namespace: str
    symbols: list[ClassSymbol] = field(default_factory=list)


@dataclass
class SymbolIndex:
    """In-memory index of all PHP symbols in the project."""

    project_root: Path
    version: int = INDEX_VERSION
    built_at: float = field(default_factory=time.time)
    files: dict[str, FileIndex] = field(default_factory=dict)  # rel_path → FileIndex

    # ---------------------------------------------------------------------------
    # Build / update
    # ---------------------------------------------------------------------------

    def build(
        self,
        directories: list[str] | None = None,
        force: bool = False,
        progress_cb: Optional[callable] = None,
    ) -> dict:
        """Scan PHP files and update the index.

        Only re-scans files whose mtime has changed (incremental by default).
        Set *force=True* to re-scan everything.

        Returns a stats dict: {scanned, updated, skipped, errors}.
        """
        dirs = directories or ["src"]
        stats = {"scanned": 0, "updated": 0, "skipped": 0, "errors": 0}

        php_files: list[Path] = []
        for d in dirs:
            base = self.project_root / d
            if base.is_dir():
                php_files.extend(base.rglob("*.php"))

        for php_file in sorted(php_files):
            stats["scanned"] += 1
            try:
                rel = str(php_file.relative_to(self.project_root))
                mtime = php_file.stat().st_mtime

                if not force and rel in self.files and self.files[rel].mtime >= mtime:
                    stats["skipped"] += 1
                    continue

                file_idx = _parse_file(php_file, rel)
                self.files[rel] = file_idx
                stats["updated"] += 1

                if progress_cb:
                    progress_cb(rel)

            except Exception:  # noqa: BLE001
                stats["errors"] += 1

        # Remove entries for deleted files
        existing = {
            str(f.relative_to(self.project_root))
            for d in dirs
            for f in (self.project_root / d).rglob("*.php")
            if (self.project_root / d).is_dir()
        }
        stale = [k for k in self.files if k not in existing]
        for k in stale:
            del self.files[k]

        self.built_at = time.time()
        return stats

    # ---------------------------------------------------------------------------
    # Search
    # ---------------------------------------------------------------------------

    def find_symbol(
        self,
        name: str,
        kind: Optional[str] = None,
    ) -> list[ClassSymbol | MethodSymbol]:
        """Search for symbols matching *name* (case-insensitive substring).

        *kind* can be: class, interface, trait, enum, method, const
        Returns a flat list of matching ClassSymbol or MethodSymbol objects.
        """
        name_lower = name.lower()
        results: list = []

        for file_idx in self.files.values():
            for cls in file_idx.symbols:
                # Class-level match
                if kind in (None, cls.kind):
                    if name_lower in cls.name.lower() or name_lower in cls.fqn.lower():
                        results.append(cls)

                # Method-level match
                if kind in (None, "method"):
                    for method in cls.methods:
                        if name_lower in method.name.lower():
                            results.append(method)

        return results

    def all_classes(self) -> list[ClassSymbol]:
        """Return every indexed ClassSymbol."""
        return [cls for fi in self.files.values() for cls in fi.symbols]

    def get_file(self, rel_path: str) -> Optional[FileIndex]:
        return self.files.get(rel_path)

    # ---------------------------------------------------------------------------
    # Persistence
    # ---------------------------------------------------------------------------

    @property
    def index_path(self) -> Path:
        return self.project_root / INDEX_FILE_NAME

    def save(self) -> None:
        data = {
            "version": self.version,
            "built_at": self.built_at,
            "project_root": str(self.project_root),
            "files": {
                rel: _file_idx_to_dict(fi)
                for rel, fi in self.files.items()
            },
        }
        self.index_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, project_root: Path) -> "SymbolIndex":
        """Load a persisted index, or return an empty one if none exists."""
        idx = cls(project_root=project_root)
        path = project_root / INDEX_FILE_NAME
        if not path.is_file():
            return idx
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") != INDEX_VERSION:
                return idx  # stale format – start fresh
            idx.built_at = data.get("built_at", 0)
            for rel, fd in data.get("files", {}).items():
                idx.files[rel] = _dict_to_file_idx(rel, fd)
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        return idx

    def stats(self) -> dict:
        classes = methods = 0
        for fi in self.files.values():
            classes += len(fi.symbols)
            for cls in fi.symbols:
                methods += len(cls.methods)
        return {
            "files": len(self.files),
            "classes": classes,
            "methods": methods,
            "built_at": self.built_at,
        }


# ---------------------------------------------------------------------------
# PHP file parser
# ---------------------------------------------------------------------------

def _parse_file(php_file: Path, rel_path: str) -> FileIndex:
    content = php_file.read_text(encoding="utf-8", errors="replace")

    # Namespace
    ns_match = _RE_NAMESPACE.search(content)
    namespace = ns_match.group(1) if ns_match else ""

    # Use-imports for FQN resolution
    use_map: dict[str, str] = {}  # short name → FQCN
    for m in _RE_USE.finditer(content):
        fqcn = m.group(1)
        alias = m.group(2) or fqcn.rsplit("\\", 1)[-1]
        use_map[alias] = fqcn

    file_idx = FileIndex(rel_path=rel_path, mtime=php_file.stat().st_mtime, namespace=namespace)

    # Split content into rough "class blocks" by finding class/interface/trait/enum
    # declarations and slicing between them
    lines = content.splitlines()
    class_positions = list(_RE_CLASS.finditer(content))

    for i, class_match in enumerate(class_positions):
        # Determine the block of text belonging to this class
        start = class_match.start()
        end = class_positions[i + 1].start() if i + 1 < len(class_positions) else len(content)
        class_block = content[start:end]

        class_line = content[:start].count("\n") + 1

        kind = class_match.group("kind")
        class_name = class_match.group("name")
        modifiers_raw = (class_match.group("modifiers") or "").split()
        extends_raw = class_match.group("extends") or ""
        implements_raw = class_match.group("implements") or ""

        fqn = f"{namespace}\\{class_name}" if namespace else class_name

        extends = [e.strip() for e in extends_raw.split(",") if e.strip()]
        implements = [x.strip() for x in implements_raw.split(",") if x.strip()]

        # Collect PHP 8 attributes in the lines before the class declaration
        pre_class = content[max(0, start - 300):start]
        class_attrs = _RE_ATTR_LINE.findall(pre_class)

        # Route prefix on controller class
        route_prefix: Optional[str] = None
        for attr in class_attrs:
            rm = _RE_ROUTE_ATTR.search(f"#[{attr}]")
            if rm:
                route_prefix = rm.group(1)

        cls_sym = ClassSymbol(
            name=class_name,
            fqn=fqn,
            kind=kind,
            modifiers=modifiers_raw,
            extends=extends,
            implements=implements,
            line=class_line,
            file=rel_path,
            attributes=class_attrs,
            route_prefix=route_prefix,
        )

        # Constants
        cls_sym.constants = _RE_CONST.findall(class_block)

        # Methods
        for method_match in _RE_METHOD.finditer(class_block):
            method_name = method_match.group("name")
            if method_name in ("__halt_compiler",):
                continue

            method_offset = start + method_match.start()
            method_line = content[:method_offset].count("\n") + 1

            visibility = method_match.group("visibility")
            method_mods = (method_match.group("modifiers") or "").split()

            # Collect PHP 8 attributes from the lines immediately before the
            # visibility keyword.  We look back line-by-line (up to 10 lines)
            # and stop as soon as we hit a line that isn't an attribute or blank.
            pre_method = class_block[:method_match.start()]
            pre_lines = pre_method.splitlines()
            method_attrs: list[str] = []
            for raw_line in reversed(pre_lines[-10:]):
                stripped = raw_line.strip()
                if not stripped:
                    continue
                if stripped.startswith("#["):
                    method_attrs[:0] = _RE_ATTR_LINE.findall(stripped)
                else:
                    break

            route_path: Optional[str] = None
            route_methods: list[str] = []
            for attr in method_attrs:
                rm = _RE_ROUTE_ATTR.search(f"#[{attr}]")
                if rm:
                    route_path = rm.group(1)
                    if rm.group(2):
                        route_methods = [
                            m.strip().strip("'\"")
                            for m in rm.group(2).split(",")
                            if m.strip()
                        ]

            method_sym = MethodSymbol(
                name=method_name,
                fqn=f"{fqn}::{method_name}",
                visibility=visibility,
                is_static="static" in method_mods,
                is_abstract="abstract" in method_mods,
                line=method_line,
                attributes=method_attrs,
                route_path=route_path,
                route_methods=route_methods,
            )
            cls_sym.methods.append(method_sym)

        file_idx.symbols.append(cls_sym)

    return file_idx


# ---------------------------------------------------------------------------
# JSON serialisation helpers
# ---------------------------------------------------------------------------

def _file_idx_to_dict(fi: FileIndex) -> dict:
    return {
        "mtime": fi.mtime,
        "namespace": fi.namespace,
        "symbols": [_cls_to_dict(c) for c in fi.symbols],
    }


def _cls_to_dict(c: ClassSymbol) -> dict:
    return {
        "name": c.name,
        "fqn": c.fqn,
        "kind": c.kind,
        "modifiers": c.modifiers,
        "extends": c.extends,
        "implements": c.implements,
        "line": c.line,
        "file": c.file,
        "attributes": c.attributes,
        "route_prefix": c.route_prefix,
        "methods": [_method_to_dict(m) for m in c.methods],
        "constants": c.constants,
    }


def _method_to_dict(m: MethodSymbol) -> dict:
    return {
        "name": m.name,
        "fqn": m.fqn,
        "visibility": m.visibility,
        "is_static": m.is_static,
        "is_abstract": m.is_abstract,
        "line": m.line,
        "attributes": m.attributes,
        "route_path": m.route_path,
        "route_methods": m.route_methods,
    }


def _dict_to_file_idx(rel_path: str, d: dict) -> FileIndex:
    fi = FileIndex(
        rel_path=rel_path,
        mtime=d.get("mtime", 0),
        namespace=d.get("namespace", ""),
    )
    for cd in d.get("symbols", []):
        fi.symbols.append(_dict_to_cls(cd))
    return fi


def _dict_to_cls(d: dict) -> ClassSymbol:
    cls = ClassSymbol(
        name=d["name"],
        fqn=d["fqn"],
        kind=d["kind"],
        modifiers=d.get("modifiers", []),
        extends=d.get("extends", []),
        implements=d.get("implements", []),
        line=d["line"],
        file=d["file"],
        attributes=d.get("attributes", []),
        route_prefix=d.get("route_prefix"),
        constants=d.get("constants", []),
    )
    for md in d.get("methods", []):
        cls.methods.append(MethodSymbol(
            name=md["name"],
            fqn=md["fqn"],
            visibility=md.get("visibility", "public"),
            is_static=md.get("is_static", False),
            is_abstract=md.get("is_abstract", False),
            line=md["line"],
            attributes=md.get("attributes", []),
            route_path=md.get("route_path"),
            route_methods=md.get("route_methods", []),
        ))
    return cls
