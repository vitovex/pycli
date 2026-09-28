"""pycli: Python-compatible DevOps DSL.

Transpiles .spy files to standard Python with first-class shell execution.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pycli.highlighter import highlight_python, should_colorize
from pycli.importer import SpyFinder, SpyLoader, install_import_hook, uninstall_import_hook
from pycli.repl import start_repl
from pycli.runtime import (
    BackgroundJob,
    CommandError,
    CommandResult,
    DynamicObj,
    async_run,
    cd,
    env,
    run,
    run_bg,
    run_expanded,
    wait_all,
)
from pycli.transformer import transpile

__version__ = "0.1.0"
__all__ = [
    "transpile",
    "highlight_python",
    "install_import_hook",
    "uninstall_import_hook",
    "SpyFinder",
    "SpyLoader",
    "run",
    "run_expanded",
    "run_bg",
    "wait_all",
    "async_run",
    "BackgroundJob",
    "cd",
    "env",
    "start_repl",
    "CommandResult",
    "CommandError",
    "DynamicObj",
    "main",
]



def transpile_file(input_path: Path, output_path: Path | None = None) -> str:
    """Transpile a .spy file to Python code, optionally writing to output_path."""
    source = input_path.read_text(encoding="utf-8")
    py_code = transpile(source)
    if output_path:
        output_path.write_text(py_code, encoding="utf-8")
    return py_code


def run_file(script_path: Path, script_args: list[str] | None = None) -> int:
    """Transpile and execute a .spy file using CPython with .spy import hook enabled."""
    install_import_hook()

    # Ensure the script's directory is at the beginning of sys.path (standard Python behavior)
    script_dir = str(script_path.resolve().parent)
    original_sys_path = list(sys.path)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    py_code = transpile_file(script_path)

    # Set up execution environment
    original_argv = sys.argv
    sys.argv = [str(script_path)] + (script_args or [])

    global_namespace: dict = {
        "__name__": "__main__",
        "__file__": str(script_path.resolve()),
        "__doc__": None,
        "cd": cd,
        "env": env,
        "wait_all": wait_all,
    }

    try:
        compiled = compile(py_code, str(script_path), "exec")
        exec(compiled, global_namespace)
        return 0
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except Exception as e:
        sys.stderr.write(f"Error executing {script_path}: {e}\n")
        return 1
    finally:
        sys.argv = original_argv
        sys.path = original_sys_path



def main(argv: list[str] | None = None) -> None:
    """CLI entrypoint for pycli."""
    if argv is None:
        argv = sys.argv[1:]

    # If no arguments provided in an interactive terminal, launch REPL
    if not argv:
        if hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
            start_repl()
            return

    parser = argparse.ArgumentParser(
        prog="pycli",
        description="pycli: Python-compatible DevOps DSL transpiler and runner.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"pycli {__version__}",
    )

    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # pycli repl
    subparsers.add_parser("repl", help="Start the interactive pycli shell")

    # pycli transpile <file.spy> [-o <file.py>] [--color / --no-color]
    transpile_parser = subparsers.add_parser("transpile", help="Transpile .spy to standard .py")
    transpile_parser.add_argument("file", help="Path to .spy source file")
    transpile_parser.add_argument("-o", "--output", help="Output .py file path (defaults to stdout)")
    transpile_parser.add_argument(
        "--color",
        action="store_true",
        default=False,
        help="Force colorized output even when stdout is redirected",
    )
    transpile_parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        help="Disable colorized output",
    )

    # pycli run <file.spy> [args...]
    run_parser = subparsers.add_parser("run", help="Transpile and execute a .spy file")
    run_parser.add_argument("file", help="Path to .spy file to run")
    run_parser.add_argument("args", nargs=argparse.REMAINDER, help="Arguments passed to script")

    # If the first argument is a file ending in .spy or an existing file (not a known command/flag), default to run
    if argv and not argv[0].startswith("-") and argv[0] not in ("transpile", "run", "repl", "help"):
        argv = ["run"] + argv

    args = parser.parse_args(argv)

    if args.subcommand == "repl":
        start_repl()

    elif args.subcommand == "transpile":
        input_file = Path(args.file)
        if not input_file.exists():
            sys.stderr.write(f"Error: file not found: {input_file}\n")
            sys.exit(1)
        out_path = Path(args.output) if args.output else None
        py_code = transpile_file(input_file, out_path)
        if not out_path:
            if should_colorize(force_color=args.color, no_color=args.no_color):
                sys.stdout.write(highlight_python(py_code))
            else:
                sys.stdout.write(py_code)

    elif args.subcommand == "run":
        input_file = Path(args.file)
        if not input_file.exists():
            sys.stderr.write(f"Error: file not found: {input_file}\n")
            sys.exit(1)
        exit_code = run_file(input_file, args.args)
        sys.exit(exit_code)

    else:
        parser.print_help()



if __name__ == "__main__":
    main()
