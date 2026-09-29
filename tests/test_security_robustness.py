"""Comprehensive tests for Security & Robustness improvements in pycli."""

from __future__ import annotations

import asyncio
import os
import sys
import warnings
from pathlib import Path
import pytest

from pycli import (
    CommandError,
    CommandResult,
    CommandTimeoutError,
    SpyFinder,
    TranspilerError,
    async_run,
    main,
    run,
    run_bg,
    run_expanded,
    run_file,
    shell_quote,
    transpile,
    transpile_file,
)
from pycli.lexer import Lexer, TokenType
from pycli.parser import CommandParser, is_valid_interpolation_expr
from pycli.repl import SpyConsole
from pycli.runtime import set_current_expression
from pycli.transformer import Transformer


# =========================================================================
# Sezione 1 — Shell Injection (Sicurezza)
# =========================================================================

def test_sec_01_shell_quote_helper():
    assert shell_quote("simple") == "simple"
    expected_spaces = '"file with spaces.txt"' if sys.platform == "win32" else "'file with spaces.txt'"
    assert shell_quote("file with spaces.txt") == expected_spaces
    expected_cmd = '"a; rm -rf /"' if sys.platform == "win32" else "'a; rm -rf /'"
    assert shell_quote("a; rm -rf /") == expected_cmd
    assert shell_quote(123) == "123"


def test_sec_01_safe_interpolation_transpiles():
    source = "user_input = 'innocuo; echo pwned'\n$(cat {user_input})\n"
    py = transpile(source, auto_import=False)
    assert 'run_expanded("cat", (user_input), capture=False)' in py


def test_sec_01_unsafe_interpolation_flag():
    source = "user_input = 'innocuo; echo pwned'\n$(cat {user_input})\n"
    py_unsafe = transpile(source, auto_import=False, unsafe_interpolation=True)
    assert 'run(f"cat {user_input}", capture=False)' in py_unsafe


def test_sec_01_injection_prevention_at_runtime():
    malicious = "innocuo; echo pwned"
    quoted = shell_quote(malicious)
    expected_malicious = '"innocuo; echo pwned"' if sys.platform == "win32" else "'innocuo; echo pwned'"
    assert quoted == expected_malicious
    expected_and = '"test && rm -rf /"' if sys.platform == "win32" else "'test && rm -rf /'"
    assert shell_quote("test && rm -rf /") == expected_and


def test_sec_02_redirection_sanitization():
    source = "outfile = 'file con spazi.txt'\n$(git status > {outfile})\n"
    py = transpile(source, auto_import=False)
    assert 'run_expanded("git", "status", ShellOp(">"), (outfile), capture=False)' in py


def test_sec_02_redirection_with_quotes_preserved():
    parser = CommandParser('cmd > "file with spaces.txt"')
    ast = parser.parse()
    redir = ast.pipeline.commands[0].parts[-1]
    assert redir.target == '"file with spaces.txt"'


def test_sec_02_expanded_redirection_sanitization():
    source = "items = ['a', 'b']\noutfile = 'path with spaces.txt'\n$(echo {*items} > {outfile})\n"
    py = transpile(source, auto_import=False)
    assert 'ShellOp(">"), (outfile)' in py


def test_sec_02_expanded_redirection_unsafe():
    source = "items = ['a', 'b']\noutfile = 'path with spaces.txt'\n$(echo {*items} > {outfile})\n"
    py = transpile(source, auto_import=False, unsafe_interpolation=True)
    assert 'ShellOp(">"), (outfile)' in py


def test_sec_03_is_valid_interpolation_expr():
    # Valid expressions
    assert is_valid_interpolation_expr("foo") is True
    assert is_valid_interpolation_expr("obj.attr") is True
    assert is_valid_interpolation_expr("func('test')") is True

    # Invalid syntax returns False silently
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        assert is_valid_interpolation_expr("def foo(:") is False
        assert len(recorded) == 0

    # Dict literal is not treated as interpolation
    assert is_valid_interpolation_expr("'k': 'v'") is False


# =========================================================================
# Sezione 2 — Lexer (Robustezza)
# =========================================================================

def test_lex_01_raw_string_not_command():
    source = 'pattern = r"$(\\d+)"\n'
    lexer = Lexer(source)
    tokens = lexer.tokenize()
    assert len(tokens) == 1
    assert tokens[0].type == TokenType.PYTHON_CODE
    assert 'r"$(\\d+)"' in tokens[0].value


def test_lex_01_raw_string_variants():
    sources = [
        "s1 = rb'$(hello)'\n",
        's2 = fr"$({val})"\n',
        's3 = r\'$(test)\'\n',
        's4 = R"$(upper)"\n',
    ]
    for src in sources:
        tokens = Lexer(src).tokenize()
        assert len(tokens) == 1
        assert tokens[0].type == TokenType.PYTHON_CODE


def test_lex_02_fstring_with_dollar_parenthesis():
    source = 'msg = f"The pattern is $({variable})"\n'
    tokens = Lexer(source).tokenize()
    assert len(tokens) == 1
    assert tokens[0].type == TokenType.PYTHON_CODE
    assert 'The pattern is $({variable})' in tokens[0].value


def test_lex_03_max_source_size_exceeded(monkeypatch):
    monkeypatch.setenv("PYCLI_MAX_SOURCE_SIZE", "100")
    large_source = "x = 1\n" * 50  # > 100 bytes
    with pytest.raises(ValueError, match="exceeds maximum allowed size"):
        Lexer(large_source)


# =========================================================================
# Sezione 3 — Transformer (Robustezza / Correttezza)
# =========================================================================

def test_trf_01_lambda_expression_context():
    source = "fn = lambda: $(git status)\n"
    py = transpile(source, auto_import=False)
    # Lambda body must be an expression (capture=True), not capture=False statement
    assert py == 'fn = lambda: run("git status")\n'


def test_trf_01_ternary_expression_context():
    source1 = "x = $(cmd) if cond else fallback\n"
    py1 = transpile(source1, auto_import=False)
    assert 'run("cmd") if cond else fallback' in py1

    source2 = "x = fallback if cond else $(cmd)\n"
    py2 = transpile(source2, auto_import=False)
    assert 'fallback if cond else run("cmd")' in py2


def test_trf_01_comprehension_expression_context():
    source = "results = [$(process {item}) for item in items]\n"
    py = transpile(source, auto_import=False)
    assert 'run_expanded("process", (item)) for item in items' in py


def test_trf_02_token_immutability():
    source = "$(echo 1).tee\n$(echo 2).input('data')\n"
    lexer = Lexer(source)
    tokens = lexer.tokenize()

    original_values = [t.value for t in tokens]
    transformer = Transformer(auto_import=False)
    py = transformer.transform(source)

    # Ensure token values in original token list are not mutated in-place
    after_values = [t.value for t in tokens]
    assert original_values == after_values
    assert "tee=True" in py
    assert "input='data'" in py


def test_trf_03_validate_valid_and_invalid():
    # Valid Python transpile with validate=True
    py = transpile("x = $(echo hello)\n", auto_import=False, validate=True)
    assert "run" in py

    # Invalid Python syntax raised by validate
    transformer = Transformer(auto_import=False)
    with pytest.raises(TranspilerError):
        # Force a situation where generated code would be invalid Python syntax
        transformer.transform("def : invalid syntax $(echo 1)", validate=True)


# =========================================================================
# Sezione 4 — Runtime (Robustezza / Sicurezza)
# =========================================================================

def test_run_01_streaming_native(capfd):
    res = run([sys.executable, "-c", "import sys; sys.stdout.write('realtime-test\\n')"], capture=False)
    assert res.exit_code == 0
    assert res.stdout == ""
    captured = capfd.readouterr()
    assert "realtime-test" in captured.out


def test_run_02_timeout():
    with pytest.raises(CommandTimeoutError) as exc_info:
        run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.3)
    assert exc_info.value.result.exit_code == -1
    assert "failed with exit code -1" in str(exc_info.value)


def test_run_02_async_run_timeout():
    async def _test():
        with pytest.raises(CommandTimeoutError):
            await async_run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.3)
    asyncio.run(_test())


def test_cor09_async_run_no_unraisable_warnings_on_lifecycle():
    """COR-09: Verify async_run timeout, cancellation, and success do not leak unraisable loop warnings."""
    async def _runner():
        # 1. Success
        res = await async_run([sys.executable, "-c", "print('ok')"])
        assert res.exit_code == 0
        assert "ok" in res.stdout

        # 2. Timeout
        with pytest.raises(CommandTimeoutError):
            await async_run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.1)

        # 3. Cancellation
        task = asyncio.create_task(
            async_run([sys.executable, "-c", "import time; time.sleep(5)"])
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    for _ in range(5):
        asyncio.run(_runner())


def test_run_03_custom_encoding():
    cmd = [sys.executable, "-c", "import sys; sys.stdout.buffer.write('h\\xe9llo w\\xf6rld'.encode('latin-1'))"]
    res = run(cmd, encoding="latin-1")
    assert res.exit_code == 0
    assert "héllo wörld" in res.stdout


def test_run_04_background_job_wait_idempotent():
    job = run_bg([sys.executable, "-c", "print('bg-finished')"])
    res1 = job.wait(timeout=5)
    assert res1.exit_code == 0
    assert "bg-finished" in res1.stdout

    # Second wait on finished process returns same result without error
    res2 = job.wait()
    assert res2 is res1


def test_run_04_background_job_wait_timeout():
    job = run_bg([sys.executable, "-c", "import time; time.sleep(5)"])
    with pytest.raises(CommandTimeoutError):
        job.wait(timeout=0.3)


def test_run_05_max_output_bytes():
    # Produce 200 bytes of 'A'
    res = run([sys.executable, "-c", "print('A' * 200)"], max_output_bytes=50)
    assert res.truncated is True
    assert len(res.stdout.encode("utf-8")) <= 50


# =========================================================================
# Sezione 5 — Importer / exec() (Sicurezza)
# =========================================================================

def test_imp_01_path_traversal_validation(tmp_path: Path):
    non_existent = tmp_path / "does_not_exist.spy"
    exit_code = run_file(non_existent)
    assert exit_code == 1


def test_imp_01_warn_external_flag(tmp_path: Path, capsys):
    script = tmp_path / "hello.spy"
    script.write_text("print('hello')\n", encoding="utf-8")

    exit_code = run_file(script, warn_external=True)
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Warning: Executing .spy script with full user privileges" in captured.err


def test_imp_02_stdlib_not_hijacked():
    finder = SpyFinder()
    # Looking for 'json' or 'os' should immediately return None and not hijack stdlib
    assert finder.find_spec("json") is None
    assert finder.find_spec("os") is None
    assert finder.find_spec("sys") is None


def test_imp_03_bytecode_caching(tmp_path: Path):
    script = tmp_path / "module_cached.spy"
    script.write_text("val = 42\n", encoding="utf-8")

    # Transpile via importer
    from pycli.importer import SpyLoader
    loader = SpyLoader("module_cached", script)
    import types
    mod = types.ModuleType("module_cached")
    loader.exec_module(mod)
    assert mod.val == 42

    cache_dir = tmp_path / "__pycache__"
    assert cache_dir.is_dir()
    pyc_files = list(cache_dir.glob("module_cached.spy-*.pyc"))
    assert len(pyc_files) == 1

    # Second load uses cached bytecode
    mod2 = types.ModuleType("module_cached")
    loader.exec_module(mod2)
    assert mod2.val == 42


# =========================================================================
# Sezione 6 — REPL (Sicurezza / Robustezza)
# =========================================================================

def test_repl_01_syntax_error_not_obscured(capsys):
    console = SpyConsole()
    # Unclosed command expression
    success = console.runsource("$(git status")
    assert success is False
    captured = capsys.readouterr()
    assert "spy syntax error: Line 1" in captured.err


def test_repl_01_parse_error_handling(capsys):
    console = SpyConsole()
    # Unterminated quote inside command
    success = console.runsource("$(echo 'unterminated)")
    assert success is False
    captured = capsys.readouterr()
    assert "spy syntax error" in captured.err


class _FakePopenForInterrupt:
    def __init__(self, fail_wait: bool = False):
        self.fail_wait = fail_wait
        self.sigint_sent = False
        self.waited = False
        self.pid = 12345
        self.returncode = None

    def communicate(self, *args, **kwargs):
        raise KeyboardInterrupt()

    def send_signal(self, sig: int):
        import signal
        if sig == signal.SIGINT:
            self.sigint_sent = True

    def wait(self, timeout: float | None = None):
        import subprocess
        self.waited = True
        if self.fail_wait:
            raise subprocess.TimeoutExpired("cmd", timeout or 2)
        self.returncode = 0
        return 0

    def poll(self):
        return self.returncode


@pytest.mark.parametrize(
    "target_platform,fail_wait,expect_sigint,expect_kill_tree",
    [
        ("win32", False, False, True),
        ("linux", False, True, False),
        ("linux", True, True, True),
    ],
)
def test_repl_02_sigint_capture_true_handling(
    monkeypatch, target_platform, fail_wait, expect_sigint, expect_kill_tree
):
    import subprocess

    tree_killed = False
    fake_proc = _FakePopenForInterrupt(fail_wait=fail_wait)

    def mock_kill(proc):
        nonlocal tree_killed
        tree_killed = True

    monkeypatch.setattr(sys, "platform", target_platform)
    monkeypatch.setattr("pycli.runtime._kill_process_tree", mock_kill)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake_proc)

    with pytest.raises(KeyboardInterrupt):
        run("echo test", capture=True)

    assert fake_proc.sigint_sent is expect_sigint
    assert tree_killed is expect_kill_tree


# =========================================================================
# Sezione 7 — Errori e Observability
# =========================================================================

def test_obs_01_source_map_and_excepthook(tmp_path: Path, capsys):
    script = tmp_path / "error_script.spy"
    # Line 1: assignment
    # Line 2: error line
    script.write_text("x = 1\nraise RuntimeError('custom error')\n", encoding="utf-8")

    exit_code = run_file(script)
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "RuntimeError: custom error" in captured.err
    assert "error_script.spy" in captured.err
    assert "line 2" in captured.err.lower()


def test_obs_02_original_expression_in_command_error():
    result = CommandResult(
        command="rm -rf a.txt b.txt",
        stdout="",
        stderr="rm: cannot remove: Permission denied",
        exit_code=1,
        duration=0.05,
        original_expression="$(rm {*files})!",
    )
    err = CommandError(result)
    err_str = str(err)
    assert "rm -rf a.txt b.txt" in err_str
    assert "Original expression: $(rm {*files})!" in err_str
    assert "Permission denied" in err_str
    assert result.original_expression == "$(rm {*files})!"


def test_obs_02_set_current_expression_context():
    with set_current_expression("$(test command)"):
        res = run([sys.executable, "-c", "print('test')"])
        assert res.original_expression == "$(test command)"
