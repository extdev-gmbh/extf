"""The public surface: the package root is the API, and nothing does what a library must not."""

from __future__ import annotations

import ast
from pathlib import Path

import extf

SOURCE = Path(extf.__file__).parent
#: Everything the library may import.
ALLOWED_IMPORTS = {"__future__", "calendar", "collections", "csv", "dataclasses", "datetime", "decimal", "io", "re", "unicodedata", "extf"}


def test_all_names_exist() -> None:
    assert sorted(extf.__all__) == sorted(set(extf.__all__))
    assert all(hasattr(extf, name) for name in extf.__all__)


def test_the_package_is_typed() -> None:
    assert (SOURCE / "py.typed").is_file()


def test_the_library_imports_the_standard_library_only() -> None:
    for path in SOURCE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            assert {name.split(".")[0] for name in names} <= ALLOWED_IMPORTS, path.name
