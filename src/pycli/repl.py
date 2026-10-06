"""Interactive REPL for pycli (.spy)."""

from __future__ import annotations

import builtins
import code
import codeop
import keyword
import os
import re
import rlcompleter
import shutil
import sys
from typing import Any

from pycli.highlighter import enable_windows_ansi, highlight_python
from pycli.importer import install_import_hook
from pycli.lexer import LexerError
from pycli.parser import ParseError
from pycli.runtime import (
    CommandError,
    CommandResult,
    CommandTimeoutError,
    DynamicObj,
    ShellOp,
    cd,
    env,
    run,
    run_bg,
    run_expanded,
    shell_quote,
    wait_all,
)
from pycli.transformer import TranspilerError, transpile

try:
    import readline
except ImportError:
    try:
        import pyreadline3 as readline  # type: ignore
    except ImportError:
        readline = None

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.document import Document
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.lexers import PygmentsLexer
    from prompt_toolkit.styles import Style, merge_styles
    from prompt_toolkit.styles.pygments import style_from_pygments_cls
    from pygments.lexer import words
    from pygments.lexers.python import PythonLexer
    from pygments.styles import get_style_by_name
    from pygments.token import Keyword, Name, Operator, String
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False


SHELL_BUILTINS: set[str] = {
    "cd", "cls", "clear", "dir", "pwd", "ls", "echo", "type", "cat",
    "copy", "cp", "move", "mv", "del", "rm", "mkdir", "md", "rmdir", "rd",
    "touch", "head", "tail", "grep", "find", "curl", "wget", "which",
    "git", "docker", "kubectl", "az", "aws", "gcloud", "terraform",
    "npm", "node", "cargo", "pip", "python", "make", "uv", "tar", "ssh",
    "whoami", "hostname", "date", "uptime", "ps", "kill", "df", "du",
}


def complete_path(text: str) -> list[str]:
    """Return filesystem path completions for the given text prefix."""
    prefix = text
    quote = ""
    if prefix and prefix[0] in ('"', "'"):
        quote = prefix[0]
        prefix = prefix[1:]

    expanded = os.path.expanduser(prefix)
    dirname, partial = os.path.split(expanded)
    orig_dirname, _ = os.path.split(prefix)

    search_dir = dirname if dirname else "."
    try:
        entries = os.listdir(search_dir)
    except OSError:
        return []

    use_slash = "/" in text or os.name != "nt"
    sep = "/" if use_slash else os.sep

    matches: list[str] = []
    lower_partial = partial.lower()

    for entry in entries:
        if entry.lower().startswith(lower_partial):
            full_path = os.path.join(search_dir, entry)
            is_dir = os.path.isdir(full_path)

            if orig_dirname:
                clean_orig = orig_dirname.rstrip("/\\")
                res = f"{clean_orig}{sep}{entry}"
            else:
                res = entry

            if is_dir:
                res += sep

            if quote:
                res = f"{quote}{res}"
            matches.append(res)

    return sorted(matches)


class SpyCompleter:
    """Tab completion handler for pycli REPL combining Python symbols, attributes, and path completion."""

    def __init__(self, namespace: dict[str, Any] | None = None) -> None:
        self.namespace = namespace if namespace is not None else {}
        self.python_completer = rlcompleter.Completer(self.namespace)
        self._matches: list[str] = []

    def complete(self, text: str, state: int) -> str | None:
        """Readline callback method."""
        if state == 0:
            line_buf = ""
            if readline is not None:
                try:
                    line_buf = readline.get_line_buffer()
                except Exception:
                    line_buf = ""
            self._matches = self.get_completions(line_buf, text)

        if state < len(self._matches):
            return self._matches[state]
        return None

    def get_completions(self, line_buffer: str, text: str) -> list[str]:
        """Compute matching completions based on current input buffer and word token."""
        lbuf = line_buffer.strip()

        # Check if line_buffer indicates shell command context or path context
        is_path_context = (
            any(lbuf.startswith(prefix) for prefix in ("cd ", "cat ", "ls ", "dir ", "rm ", "type ", "open("))
            or "/" in text
            or "\\" in text
            or text.startswith((".", "~", '"', "'"))
        )

        if is_path_context:
            path_matches = complete_path(text)
            if path_matches:
                return path_matches

        # Try Python identifier and attribute completions
        py_matches: list[str] = []
        i = 0
        while True:
            m = self.python_completer.complete(text, i)
            if m is None:
                break
            py_matches.append(m)
            i += 1

        if py_matches:
            return py_matches

        # Fallback to path completion if no Python symbol matches
        return complete_path(text)


def _handle_cd(source: str) -> bool:
    """Handle in-process directory change (cd) command."""
    stripped = source.strip()
    if stripped == "cd":
        target = os.path.expanduser("~")
    elif stripped.startswith("cd ") or stripped.startswith("cd\t"):
        target = stripped[2:].strip()
        if target.startswith(('"', "'")) and target.endswith(('"', "'")) and len(target) >= 2:
            target = target[1:-1]
        target = os.path.expanduser(target)
    else:
        return False

    try:
        os.chdir(target)
    except Exception as e:
        sys.stderr.write(f"cd: {e}\n")
    return True


def _is_shell_command(source: str, locals_dict: dict[str, Any]) -> tuple[bool, str, str | None]:
    """Determine whether source line should be executed as a Shell Light command."""
    stripped = source.strip()
    if not stripped:
        return False, "", None

    # Explicit DSL command expression or env var
    if "$(" in stripped or stripped.startswith("$"):
        return False, "", None

    # Explicit shell escape: !cmd
    if stripped.startswith("!"):
        return True, stripped[1:].strip(), None

    # Check for assignment: target = cmd ...
    m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.+)$", stripped)
    if m:
        target_var = m.group(1)
        rhs = m.group(2).strip()

        # Check if entire assignment compiles as valid Python
        try:
            res = codeop.compile_command(stripped)
            if res is not None:
                return False, "", None
        except SyntaxError:
            pass

        # RHS could be a shell command
        rhs_tokens = rhs.split()
        if rhs_tokens:
            first_rhs = rhs_tokens[0]
            if (
                first_rhs in SHELL_BUILTINS
                or shutil.which(first_rhs) is not None
                or first_rhs.startswith(("./", "../", "/", "~/", ".\\", "..\\"))
                or len(rhs_tokens) >= 2
            ):
                return True, rhs, target_var

    # Standalone line
    tokens = stripped.split()
    if not tokens:
        return False, "", None

    first_token = tokens[0]

    # Python keywords are NEVER shell commands
    if keyword.iskeyword(first_token):
        return False, "", None

    # Check compilation with Python
    try:
        compiled = codeop.compile_command(stripped)
    except SyntaxError:
        # Invalid python syntax: check if it's a shell command
        if (
            first_token in SHELL_BUILTINS
            or shutil.which(first_token) is not None
            or first_token.startswith(("./", "../", "/", "~/", ".\\", "..\\"))
            or any(op in stripped for op in ("|", ">", "<"))
            or (first_token.isidentifier() and len(tokens) >= 2 and not stripped.endswith(":"))
        ):
            return True, stripped, None
        return False, "", None

    if compiled is None:
        # Incomplete Python statement (e.g. def foo(): or for i in range(5):)
        return False, "", None

    # If first_token is in locals
    if first_token in locals_dict:
        # If single identifier e.g. `x`, it is the Python variable
        if len(tokens) == 1:
            return False, "", None
        # If followed by a shell flag e.g. `ls -la`, it is a shell command
        if len(tokens) >= 2 and tokens[1].startswith("-"):
            return True, stripped, None
        return False, "", None

    # If first_token is in builtins (e.g. print, len, open, help):
    if first_token in dir(builtins):
        # Exception: `dir` on Windows or when used without parens as a command
        if first_token == "dir" and not stripped.startswith("dir("):
            return True, stripped, None
        return False, "", None

    # If first_token is not defined in Python, but IS an executable or shell builtin
    if (
        first_token in SHELL_BUILTINS
        or shutil.which(first_token) is not None
        or first_token.startswith(("./", "../", "/", "~/", ".\\", "..\\"))
    ):
        return True, stripped, None

    return False, "", None


class SpyConsole(code.InteractiveConsole):
    """Interactive console that dynamically transpiles .spy syntax and supports Shell Light mode."""

    def __init__(self, locals: dict[str, Any] | None = None, filename: str = "<console>") -> None:
        install_import_hook()
        enable_windows_ansi()

        if locals is None:
            locals = {}

        locals.setdefault("os", os)
        locals.setdefault("sys", sys)
        locals.setdefault("run", run)
        locals.setdefault("run_expanded", run_expanded)
        locals.setdefault("run_bg", run_bg)
        locals.setdefault("wait_all", wait_all)
        locals.setdefault("cd", cd)
        locals.setdefault("env", env)
        locals.setdefault("shell_quote", shell_quote)
        locals.setdefault("CommandResult", CommandResult)
        locals.setdefault("CommandError", CommandError)
        locals.setdefault("CommandTimeoutError", CommandTimeoutError)
        locals.setdefault("DynamicObj", DynamicObj)

        locals.setdefault("highlight_python", highlight_python)

        super().__init__(locals=locals, filename=filename)

        # Tab completion & history setup
        self.completer = SpyCompleter(self.locals)
        if readline is not None:
            try:
                readline.set_completer(self.completer.complete)
                readline.set_completer_delims(" \t\n\"'")
                if "libedit" in getattr(readline, "__doc__", ""):
                    readline.parse_and_bind("bind ^I rl_complete")
                else:
                    readline.parse_and_bind("tab: complete")

                histfile = os.path.expanduser("~/.pycli_history")
                if os.path.exists(histfile):
                    readline.read_history_file(histfile)
                import atexit
                atexit.register(readline.write_history_file, histfile)
            except Exception:
                pass

    def runsource(self, source: str, filename: str = "<input>", symbol: str = "single") -> bool:
        """Transpile source line and execute standard interactive Python with Shell Light support."""
        stripped = source.strip()
        if not stripped or stripped.startswith("#"):
            return super().runsource(source, filename=filename, symbol=symbol)

        # In-process shell commands
        if _handle_cd(source):
            return False

        if stripped == "pwd":
            print(os.getcwd())
            return False

        if stripped in ("cls", "clear"):
            os.system("cls" if os.name == "nt" else "clear")
            return False

        if stripped in ("exit", "quit", "exit()", "quit()", "^Z") or "\x1a" in stripped:
            raise SystemExit(0)

        is_shell, cmd, target_var = _is_shell_command(source, self.locals)
        is_standalone_shell = False

        if is_shell:
            if target_var:
                to_transpile = f"{target_var} = $({cmd})"
            else:
                to_transpile = f"$({cmd})"
                is_standalone_shell = True
        else:
            to_transpile = source

        try:
            transpiled = transpile(to_transpile)
        except (LexerError, ParseError, TranspilerError) as e:
            sys.stderr.write(f"  spy syntax error: {e}\n")
            return False
        except Exception as e:
            sys.stderr.write(f"  [pycli internal error] {type(e).__name__}: {e}\n")
            return False

        # Filter out redundant header imports (e.g. from pycli.runtime import ...)
        # because InteractiveConsole with symbol="single" rejects multiple statements.
        lines = [
            line for line in transpiled.splitlines()
            if not line.startswith("from pycli.runtime import ")
            and line != "import os"
        ]
        clean_code = "\n".join(lines)

        # For standalone command expressions or statements, assign to `_` so that
        # the result is accessible via `_` without printing duplicate CommandResult repr
        if is_standalone_shell or (to_transpile.startswith("$(") and to_transpile.endswith(")")):
            if clean_code.startswith("run(") or clean_code.startswith("run_expanded("):
                clean_code = f"_ = {clean_code}"

        return super().runsource(clean_code, filename=filename, symbol=symbol)


if PROMPT_TOOLKIT_AVAILABLE:
    PYCLI_RUNTIME_WORDS = (
        "run", "run_expanded", "run_bg", "wait_all", "cd", "env", "shell_quote",
        "CommandResult", "CommandError", "CommandTimeoutError", "DynamicObj",
    )

    SHELL_WORDS = (
        "git", "docker", "kubectl", "az", "aws", "gcloud", "terraform",
        "npm", "node", "cargo", "pip", "make", "uv", "tar", "ssh",
        "cd", "cls", "clear", "dir", "pwd", "ls", "echo", "type", "cat",
        "copy", "cp", "move", "mv", "del", "rm", "mkdir", "md", "rmdir", "rd",
        "touch", "head", "tail", "grep", "find", "curl", "wget", "which",
    )

    class SpyLexer(PythonLexer):
        """Pygments Lexer for .spy syntax supporting $(cmd), $VAR, and shell commands."""

        tokens = dict(PythonLexer.tokens)
        custom_root = [
            (r"\$\(", Operator, "spy-cmd"),
            (r"\$[A-Za-z_][A-Za-z0-9_]*", Name.Variable),
            (words(PYCLI_RUNTIME_WORDS, suffix=r"\b"), Name.Builtin.Pseudo),
            (words(SHELL_WORDS, prefix=r"^\s*", suffix=r"\b"), Keyword.Namespace),
        ]
        tokens["root"] = custom_root + list(PythonLexer.tokens["root"])
        tokens["spy-cmd"] = [
            (r"\)", Operator, "#pop"),
            (r"\{[^\}]+\}", Name.Variable),
            (r"[^)\{]+", String.Backtick),
        ]

    class PycliPtCompleter(Completer):
        """Prompt toolkit completer adapter wrapping SpyCompleter."""

        def __init__(self, spy_completer: SpyCompleter) -> None:
            self.spy_completer = spy_completer

        def get_completions(self, document: Document, complete_event: Any):
            line = document.current_line_before_cursor
            delims = " \t\"'"
            pos = len(line)
            while pos > 0 and line[pos - 1] not in delims:
                pos -= 1
            token = line[pos:]

            matches = self.spy_completer.get_completions(line, token)
            for m in matches:
                yield Completion(m, start_position=-len(token))

    def run_pt_repl(console: SpyConsole) -> None:
        """Run interactive REPL using prompt_toolkit for live syntax highlighting and completion."""
        history_path = os.path.expanduser("~/.pycli_history")
        history = FileHistory(history_path)

        pygments_style = style_from_pygments_cls(get_style_by_name("default"))
        custom_style = Style.from_dict({
            "prompt": "ansicyan bold",
            "continuation": "ansibrightblack",
        })
        combined_style = merge_styles([pygments_style, custom_style])

        kb = KeyBindings()

        @kb.add("c-z")
        def _handle_ctrl_z(event: Any) -> None:
            event.app.exit(exception=EOFError)

        session: PromptSession[str] = PromptSession(
            lexer=PygmentsLexer(SpyLexer),
            completer=PycliPtCompleter(console.completer),
            complete_while_typing=False,
            history=history,
            style=combined_style,
            key_bindings=kb,
        )

        buffer_lines: list[str] = []

        while True:
            try:
                if not buffer_lines:
                    prompt_text = [("class:prompt", "spy>>> ")]
                else:
                    prompt_text = [("class:continuation", "...    ")]

                line = session.prompt(prompt_text)
            except KeyboardInterrupt:
                buffer_lines = []
                print("\nKeyboardInterrupt")
                continue
            except (EOFError, SystemExit):
                print("\nnow exiting SpyConsole...")
                break

            # Handle Windows Ctrl+Z character or exit keywords
            stripped_line = line.strip()
            if "\x1a" in line or stripped_line in ("^Z", "exit()", "quit()", "exit", "quit"):
                print("\nnow exiting SpyConsole...")
                break

            buffer_lines.append(line)
            source = "\n".join(buffer_lines)

            try:
                more = console.runsource(source)
            except SystemExit:
                print("\nnow exiting SpyConsole...")
                break

            if not more:
                buffer_lines = []


def start_repl() -> None:
    """Launch the pycli interactive REPL."""
    enable_windows_ansi()
    console = SpyConsole()
    banner = (
        "\033[96;1mpycli Interactive Shell (.spy)\033[0m\n"
        "Python DevOps DSL with first-class shell execution \033[93m$(cmd)\033[0m and \033[92mShell Light\033[0m.\n"
        "Type 'exit()' or Ctrl+Z/D to quit.\n"
    )
    if PROMPT_TOOLKIT_AVAILABLE and sys.stdin.isatty():
        print(banner)
        run_pt_repl(console)
    else:
        sys.ps1 = "spy>>> "
        sys.ps2 = "...    "
        console.interact(banner=banner)
