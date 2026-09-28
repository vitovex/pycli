"""Import hook system for pycli (.spy) files.

Allows standard Python `import` statements to transparently discover,
transpile, and execute .spy files as modules and packages.
"""

from __future__ import annotations

import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import marshal
import sys
from pathlib import Path
from typing import Any, Sequence

from pycli.transformer import transpile

STDLIB_NAMES = getattr(sys, "stdlib_module_names", frozenset())


def _get_cached_code(spy_path: Path, source: str) -> Any:
    """Retrieve compiled bytecode from __pycache__ or compile and cache it."""
    cache_dir = spy_path.parent / "__pycache__"
    src_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    cache_file = cache_dir / f"{spy_path.stem}.spy-{src_hash}.pyc"

    if cache_file.is_file():
        try:
            data = cache_file.read_bytes()
            return marshal.loads(data[8:])  # skip 8-byte magic header
        except Exception:
            pass

    py_code = transpile(source)
    compiled = compile(py_code, str(spy_path), "exec")

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(b"\x00" * 8 + marshal.dumps(compiled))
    except Exception:
        # Silently ignore if filesystem is read-only or not writable
        pass

    return compiled


class SpyLoader(importlib.abc.Loader):
    """Loader that transpiles .spy code to standard Python and executes it."""

    def __init__(self, fullname: str, path: str | Path) -> None:
        self.fullname = fullname
        self.path = str(Path(path).resolve())

    def create_module(self, spec: importlib.machinery.ModuleSpec):
        """Use default Python module creation."""
        return None

    def exec_module(self, module) -> None:
        """Transpile .spy file and execute inside module.__dict__."""
        filepath = Path(self.path).resolve()
        if not filepath.is_file():
            raise FileNotFoundError(f"Cannot import .spy module from non-existent path: {self.path}")

        source = filepath.read_text(encoding="utf-8")
        compiled = _get_cached_code(filepath, source)

        module.__file__ = str(filepath)
        module.__loader__ = self

        if filepath.name == "__init__.spy":
            module.__path__ = [str(filepath.parent)]
            module.__package__ = self.fullname
        else:
            module.__package__ = self.fullname.rpartition(".")[0]

        exec(compiled, module.__dict__)

    def get_source(self, fullname: str) -> str | None:
        """Return the original .spy source text."""
        filepath = Path(self.path).resolve()
        if filepath.is_file():
            return filepath.read_text(encoding="utf-8")
        return None


class SpyFinder(importlib.abc.MetaPathFinder):
    """MetaPathFinder that locates .spy files and packages along search paths."""

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target=None,
    ) -> importlib.machinery.ModuleSpec | None:
        top_level = fullname.split(".")[0]
        # Never shadow standard library modules
        if top_level in STDLIB_NAMES:
            return None

        # If already imported and in sys.modules, do not interfere
        if fullname in sys.modules:
            return None

        subname = fullname.rpartition(".")[-1]

        # Use package search path if provided, otherwise standard sys.path
        search_dirs = path if path is not None else sys.path

        for search_dir in search_dirs:
            if not search_dir or not isinstance(search_dir, (str, Path)):
                continue
            dir_path = Path(search_dir).resolve()
            if not dir_path.is_dir():
                continue

            # 1. Look for single file module: {dir_path}/{subname}.spy
            module_file = (dir_path / f"{subname}.spy").resolve()
            # Ensure no path traversal outside search directory
            if module_file.is_file() and str(module_file).startswith(str(dir_path)):
                loader = SpyLoader(fullname, module_file)
                return importlib.util.spec_from_loader(
                    fullname,
                    loader,
                    origin=str(module_file),
                )

            # 2. Look for package: {dir_path}/{subname}/__init__.spy
            pkg_dir = (dir_path / subname).resolve()
            init_file = (pkg_dir / "__init__.spy").resolve()
            if init_file.is_file() and str(init_file).startswith(str(dir_path)):
                loader = SpyLoader(fullname, init_file)
                spec = importlib.util.spec_from_loader(
                    fullname,
                    loader,
                    origin=str(init_file),
                    is_package=True,
                )
                if spec:
                    spec.submodule_search_locations = [str(pkg_dir)]
                return spec

        return None


# Singleton finder instance
_spy_finder: SpyFinder | None = None


def install_import_hook() -> SpyFinder:
    """Install the .spy import hook into sys.meta_path if not already present."""
    global _spy_finder
    if _spy_finder is None:
        _spy_finder = SpyFinder()

    if _spy_finder not in sys.meta_path:
        # Insert before standard PathFinder so .spy files can be loaded
        sys.meta_path.insert(0, _spy_finder)

    return _spy_finder


def uninstall_import_hook() -> None:
    """Remove the .spy import hook from sys.meta_path."""
    global _spy_finder
    if _spy_finder is not None and _spy_finder in sys.meta_path:
        sys.meta_path.remove(_spy_finder)
