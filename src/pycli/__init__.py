"""pycli: Python-compatible DevOps DSL.

Transpiles .spy files to standard Python with first-class shell execution.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

from pycli.highlighter import highlight_python, should_colorize
from pycli.importer import SpyFinder, SpyLoader, install_import_hook, uninstall_import_hook
from pycli.lexer import LexerError
from pycli.parser import ParseError
from pycli.repl import start_repl
from pycli.runtime import (
    BackgroundJob,
    CommandError,
    CommandResult,
    CommandTimeoutError,
    DynamicObj,
    ShellOp,
    async_run,
    async_run_expanded,
    cd,
    env,
    run,
    run_bg,
    run_bg_expanded,
    run_expanded,
    shell_quote,
    wait_all,
)
from pycli.transformer import Transformer, TranspilerError, transpile

__version__ = "0.1.0"
MAX_SOURCE_SIZE_BYTES = 10 * 1024 * 1024


def get_max_source_size() -> int:
    """Return maximum allowed source size in bytes from env or default."""
    return int(os.environ.get("PYCLI_MAX_SOURCE_SIZE", MAX_SOURCE_SIZE_BYTES))

__all__ = [
    "transpile",
    "TranspilerError",
    "ParseError",
    "LexerError",
    "highlight_python",
    "install_import_hook",
    "uninstall_import_hook",
    "SpyFinder",
    "SpyLoader",
    "run",
    "run_expanded",
    "run_bg",
    "run_bg_expanded",
    "wait_all",
    "async_run",
    "async_run_expanded",
    "BackgroundJob",
    "cd",
    "env",
    "shell_quote",
    "ShellOp",
    "start_repl",
    "CommandResult",
    "CommandError",
    "CommandTimeoutError",
    "DynamicObj",
    "main",
]


def transpile_file(
    input_path: Path,
    output_path: Path | None = None,
    validate: bool = False,
    unsafe_interpolation: bool = False,
) -> str:
    """Transpile a .spy file to Python code, optionally writing to output_path."""
    resolved = input_path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"File not found: {input_path}")

    size = resolved.stat().st_size
    max_size = get_max_source_size()
    if size > max_size:
        raise ValueError(
            f"File {input_path} exceeds maximum allowed size "
            f"({size} bytes > {max_size} bytes)"
        )

    source = resolved.read_text(encoding="utf-8")
    transformer = Transformer(auto_import=True, unsafe_interpolation=unsafe_interpolation)
    py_code = transformer.transform(source, validate=validate)
    if output_path:
        output_path.write_text(py_code, encoding="utf-8")
    return py_code


def _make_spy_excepthook(script_path: Path, source_map: dict[int, int], original_hook):
    resolved_path_str = str(script_path.resolve())
    orig_path_str = str(script_path)

    def _spy_excepthook(exc_type, exc_value, exc_tb):
        try:
            te = traceback.TracebackException(exc_type, exc_value, exc_tb)
            spy_text = script_path.read_text(encoding="utf-8").splitlines()
            for frame in te.stack:
                if frame.filename in (resolved_path_str, orig_path_str, "<string>"):
                    frame.filename = orig_path_str
                    if frame.lineno in source_map:
                        spy_line = source_map[frame.lineno]
                        frame.lineno = spy_line
                        if 1 <= spy_line <= len(spy_text):
                            frame._line = spy_text[spy_line - 1]
            for line in te.format():
                sys.stderr.write(line)
        except Exception:
            original_hook(exc_type, exc_value, exc_tb)

    return _spy_excepthook


def run_file(
    script_path: Path,
    script_args: list[str] | None = None,
    validate: bool = False,
    unsafe_interpolation: bool = False,
    warn_external: bool = False,
) -> int:
    """Transpile and execute a .spy file using CPython with .spy import hook enabled."""
    resolved_path = script_path.resolve()
    if not resolved_path.is_file():
        sys.stderr.write(f"Error: script not found: {script_path}\n")
        return 1

    size = resolved_path.stat().st_size
    max_size = get_max_source_size()
    if size > max_size:
        sys.stderr.write(
            f"Error: File {script_path} exceeds maximum allowed size "
            f"({size} bytes > {max_size} bytes)\n"
        )
        return 1

    if warn_external:
        sys.stderr.write(
            "Warning: Executing .spy script with full user privileges. pycli does not sandbox execution.\n"
        )

    install_import_hook(unsafe_interpolation=unsafe_interpolation, validate=validate)

    # Ensure the script's directory is at the beginning of sys.path
    script_dir = str(resolved_path.parent)
    original_sys_path = list(sys.path)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    source = resolved_path.read_text(encoding="utf-8")
    transformer = Transformer(auto_import=True, unsafe_interpolation=unsafe_interpolation)
    try:
        py_code = transformer.transform(source, validate=validate)
    except (TranspilerError, LexerError, ParseError, ValueError) as e:
        sys.stderr.write(f"Error: {e}\n")
        return 1

    original_argv = sys.argv
    sys.argv = [str(script_path)] + (script_args or [])

    global_namespace: dict = {
        "__name__": "__main__",
        "__file__": str(resolved_path),
        "__doc__": None,
        "cd": cd,
        "env": env,
        "wait_all": wait_all,
        "run": run,
        "run_expanded": run_expanded,
        "run_bg": run_bg,
        "async_run": async_run,
        "shell_quote": shell_quote,
        "ShellOp": ShellOp,
        "CommandResult": CommandResult,
        "CommandError": CommandError,
        "CommandTimeoutError": CommandTimeoutError,
        "DynamicObj": DynamicObj,
    }

    orig_excepthook = sys.excepthook
    sys.excepthook = _make_spy_excepthook(resolved_path, transformer.source_map, orig_excepthook)

    try:
        compiled = compile(py_code, str(script_path), "exec")
        exec(compiled, global_namespace)
        return 0
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except Exception as e:
        sys.excepthook(*sys.exc_info())
        return 1
    finally:
        sys.excepthook = orig_excepthook
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

    # pycli transpile <file.spy> [-o <file.py>] [--validate] [--unsafe-interpolation] [--color / --no-color]
    transpile_parser = subparsers.add_parser("transpile", help="Transpile .spy to standard .py")
    transpile_parser.add_argument("file", help="Path to .spy source file")
    transpile_parser.add_argument("-o", "--output", help="Output .py file path (defaults to stdout)")
    transpile_parser.add_argument(
        "--validate",
        action="store_true",
        default=False,
        help="Validate generated Python using ast.parse() before output",
    )
    transpile_parser.add_argument(
        "--unsafe-interpolation",
        action="store_true",
        default=False,
        help="Disable automatic shell_quote() sanitization on interpolations",
    )
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

    # pycli run <file.spy> [--validate] [--unsafe-interpolation] [--warn-external] [args...]
    run_parser = subparsers.add_parser("run", help="Transpile and execute a .spy file")
    run_parser.add_argument("file", help="Path to .spy file to run")
    run_parser.add_argument(
        "--validate",
        action="store_true",
        default=False,
        help="Validate generated Python before executing",
    )
    run_parser.add_argument(
        "--unsafe-interpolation",
        action="store_true",
        default=False,
        help="Disable automatic shell_quote() sanitization on interpolations",
    )
    run_parser.add_argument(
        "--warn-external",
        action="store_true",
        default=False,
        help="Warn about executing untrusted scripts with user privileges",
    )
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
        try:
            py_code = transpile_file(
                input_file,
                out_path,
                validate=args.validate,
                unsafe_interpolation=args.unsafe_interpolation,
            )
        except (ValueError, TranspilerError, LexerError, ParseError) as e:
            sys.stderr.write(f"Error: {e}\n")
            sys.exit(1)
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
        exit_code = run_file(
            input_file,
            args.args,
            validate=args.validate,
            unsafe_interpolation=args.unsafe_interpolation,
            warn_external=args.warn_external,
        )
        sys.exit(exit_code)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
