"""Interactive REPL for pycli (.spy)."""

from __future__ import annotations

import code
import sys
from typing import Any

from pycli.highlighter import enable_windows_ansi
from pycli.importer import install_import_hook
from pycli.runtime import (
    CommandError,
    CommandResult,
    DynamicObj,
    cd,
    env,
    run,
    run_bg,
    run_expanded,
    wait_all,
)
from pycli.transformer import transpile


class SpyConsole(code.InteractiveConsole):
    """Interactive console that dynamically transpiles .spy syntax $(...) on input."""

    def __init__(self, locals: dict[str, Any] | None = None, filename: str = "<console>") -> None:
        install_import_hook()
        enable_windows_ansi()

        if locals is None:
            locals = {}

        locals.setdefault("run", run)
        locals.setdefault("run_expanded", run_expanded)
        locals.setdefault("run_bg", run_bg)
        locals.setdefault("wait_all", wait_all)
        locals.setdefault("cd", cd)
        locals.setdefault("env", env)
        locals.setdefault("CommandResult", CommandResult)
        locals.setdefault("CommandError", CommandError)
        locals.setdefault("DynamicObj", DynamicObj)

        super().__init__(locals=locals, filename=filename)

    def runsource(self, source: str, filename: str = "<input>", symbol: str = "single") -> bool:
        """Transpile source line and execute standard interactive Python."""
        try:
            transpiled = transpile(source)
        except Exception:
            self.showsyntaxerror(filename)
            return False

        return super().runsource(transpiled, filename=filename, symbol=symbol)


def start_repl() -> None:
    """Launch the pycli interactive REPL."""
    console = SpyConsole()
    banner = (
        "pycli Interactive Shell (.spy)\n"
        "Python DevOps DSL with first-class shell execution $(cmd).\n"
        "Type 'exit()' or Ctrl+Z/D to quit.\n"
    )
    console.interact(banner=banner)
