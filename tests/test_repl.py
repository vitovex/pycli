"""Unit and integration tests for pycli REPL Tab Completion and Shell Light mode."""

from __future__ import annotations

import os
from pathlib import Path
import pytest

from pycli.repl import (
    SHELL_BUILTINS,
    SpyCompleter,
    SpyConsole,
    _handle_cd,
    _is_shell_command,
    complete_path,
)
from pycli.runtime import CommandResult, DynamicObj


# =========================================================================
# 1. Tab Completion & Path Completion Tests
# =========================================================================

def test_complete_path_directory():
    matches = complete_path("src/")
    norm_matches = [m.replace("\\", "/") for m in matches]
    assert "src/pycli/" in norm_matches


def test_complete_path_partial_file():
    matches = complete_path("READ")
    assert "README.md" in matches


def test_complete_path_subdirectory():
    matches = complete_path("tests/test_c")
    norm_matches = [m.replace("\\", "/") for m in matches]
    assert "tests/test_cli.py" in norm_matches
    assert "tests/test_compliance_pipeline.py" in norm_matches


def test_complete_path_quoted():
    matches = complete_path('"src/')
    norm_matches = [m.replace("\\", "/") for m in matches]
    assert '"src/pycli/' in norm_matches


def test_complete_path_nonexistent():
    matches = complete_path("non_existent_folder_xyz/")
    assert matches == []


def test_spy_completer_python_keywords():
    completer = SpyCompleter()
    matches = completer.get_completions("impo", "impo")
    assert any(m.startswith("import") for m in matches)

    matches_ret = completer.get_completions("retu", "retu")
    assert any(m.startswith("return") for m in matches_ret)


def test_spy_completer_locals():
    completer = SpyCompleter({"my_custom_var": 42, "another_one": "test"})
    matches = completer.get_completions("my_custom_", "my_custom_")
    assert "my_custom_var" in matches


def test_spy_completer_command_result_attributes():
    res = CommandResult("git status", "stdout text", "stderr text", 0, 0.05)
    completer = SpyCompleter({"res": res})
    matches = completer.get_completions("res.", "res.")

    assert "res.command" in matches
    assert "res.duration" in matches
    assert "res.exit_code" in matches
    assert "res.json" in matches
    assert "res.lines" in matches
    assert "res.stderr" in matches
    assert "res.stdout" in matches
    assert "res.text" in matches


def test_spy_completer_dynamic_obj_properties():
    data = {"vm_name": "worker-1", "ip_address": "10.0.0.4", "status": "Ready"}
    obj = DynamicObj(data)
    assert "vm_name" in dir(obj)
    assert "ip_address" in dir(obj)

    completer = SpyCompleter({"vm": obj})
    matches = completer.get_completions("vm.", "vm.")
    assert "vm.vm_name" in matches
    assert "vm.ip_address" in matches
    assert "vm.status" in matches


def test_spy_completer_shell_context_path():
    completer = SpyCompleter()
    # "cd " prefix should trigger path completion
    matches = completer.get_completions("cd ex", "ex")
    norm_matches = [m.replace("\\", "/") for m in matches]
    assert "examples/" in norm_matches

    # "cat " prefix should trigger path completion
    matches = completer.get_completions("cat READ", "READ")
    assert "README.md" in matches


def test_spy_completer_readline_protocol():
    completer = SpyCompleter({"test_val": 100})
    # Calling complete(text, 0), complete(text, 1) ...
    res0 = completer.complete("test_val", 0)
    assert res0 == "test_val"
    res1 = completer.complete("test_val", 1)
    assert res1 is None


# =========================================================================
# 2. Shell Light Mode Detection Tests
# =========================================================================

def test_is_shell_command_detection():
    locals_env = {"x": 10, "res": CommandResult("ls", "out", "", 0, 0.1)}

    # Standalone shell commands
    assert _is_shell_command("git status", locals_env) == (True, "git status", None)
    assert _is_shell_command("docker ps -a", locals_env) == (True, "docker ps -a", None)
    assert _is_shell_command("ls -la", locals_env) == (True, "ls -la", None)
    assert _is_shell_command("ls", locals_env) == (True, "ls", None)
    assert _is_shell_command("echo hello world", locals_env) == (True, "echo hello world", None)
    assert _is_shell_command("cat file.txt | grep foo", locals_env) == (True, "cat file.txt | grep foo", None)
    assert _is_shell_command("!my_custom_tool", locals_env) == (True, "my_custom_tool", None)

    # Shell command assignment
    assert _is_shell_command("vms = az vm list", locals_env) == (True, "az vm list", "vms")
    assert _is_shell_command("out = git status", locals_env) == (True, "git status", "out")

    # Python expressions & statements (must NOT be intercepted)
    assert _is_shell_command("x = 10", locals_env) == (False, "", None)
    assert _is_shell_command("x", locals_env) == (False, "", None)
    assert _is_shell_command("def my_func():", locals_env) == (False, "", None)
    assert _is_shell_command("for i in range(5):", locals_env) == (False, "", None)
    assert _is_shell_command("import sys", locals_env) == (False, "", None)
    assert _is_shell_command("print('hi')", locals_env) == (False, "", None)
    assert _is_shell_command("$(git status)", locals_env) == (False, "", None)
    assert _is_shell_command("$MYVAR", locals_env) == (False, "", None)


# =========================================================================
# 3. SpyConsole REPL Execution Tests
# =========================================================================

def test_spy_console_python_execution():
    console = SpyConsole()
    console.runsource("val1 = 15")
    console.runsource("val2 = 25")
    console.runsource("total = val1 + val2")
    assert console.locals["total"] == 40


def test_spy_console_shell_light_standalone(capfd):
    console = SpyConsole()
    success = console.runsource("echo repl_test_ok")
    assert success is False  # Single complete statement returns False
    captured = capfd.readouterr()
    assert "repl_test_ok" in captured.out
    # Result stored in `_`
    assert isinstance(console.locals["_"], CommandResult)
    assert console.locals["_"].exit_code == 0


def test_spy_console_shell_light_assignment():
    console = SpyConsole()
    success = console.runsource("my_out = echo captured_assignment")
    assert success is False
    assert "my_out" in console.locals
    assert isinstance(console.locals["my_out"], CommandResult)
    assert console.locals["my_out"].stdout.strip() == "captured_assignment"


def test_spy_console_in_process_cd(tmp_path: Path):
    orig_cwd = os.getcwd()
    try:
        console = SpyConsole()
        target = str(tmp_path).replace("\\", "/")
        console.runsource(f"cd {target}")
        assert Path(os.getcwd()).resolve() == tmp_path.resolve()
    finally:
        os.chdir(orig_cwd)


def test_spy_console_pwd(capsys):
    console = SpyConsole()
    console.runsource("pwd")
    captured = capsys.readouterr()
    assert os.getcwd() in captured.out


def test_spy_console_explicit_dsl_expression():
    console = SpyConsole()
    success = console.runsource("dsl_res = $(echo from_explicit_dsl)")
    assert success is False
    assert "dsl_res" in console.locals
    assert console.locals["dsl_res"].stdout.strip() == "from_explicit_dsl"


def test_spy_console_syntax_errors_preserved(capsys):
    console = SpyConsole()
    # Invalid python that is not a shell command
    console.runsource("def invalid_syntax(")
    # Spy console unclosed $(
    console.runsource("$(echo 'unterminated)")
    captured = capsys.readouterr()
    assert "spy syntax error" in captured.err


# =========================================================================
# 4. Live Syntax Highlighting & prompt_toolkit Integration Tests
# =========================================================================

def test_spy_lexer_tokens():
    from pycli.repl import PROMPT_TOOLKIT_AVAILABLE
    if not PROMPT_TOOLKIT_AVAILABLE:
        pytest.skip("prompt_toolkit not installed")

    from pycli.repl import SpyLexer
    from pygments import lex
    from pygments.token import Operator, Name, String, Keyword

    # Check $(cmd) tokenization
    tokens = list(lex("res = $(git status)", SpyLexer()))
    # Operator for $( and )
    op_tokens = [t[1] for t in tokens if t[0] in (Operator, Operator.Word)]
    assert "$(" in op_tokens
    assert ")" in op_tokens

    # Check $VAR tokenization
    tokens_var = list(lex("echo $MY_VAR", SpyLexer()))
    var_tokens = [t[1] for t in tokens_var if t[0] == Name.Variable]
    assert "$MY_VAR" in var_tokens

    # Check pycli runtime symbols
    tokens_rt = list(lex("vm = DynamicObj({})", SpyLexer()))
    rt_tokens = [t[1] for t in tokens_rt if t[0] == Name.Builtin.Pseudo]
    assert "DynamicObj" in rt_tokens


def test_pt_completer_adapter():
    from pycli.repl import PROMPT_TOOLKIT_AVAILABLE
    if not PROMPT_TOOLKIT_AVAILABLE:
        pytest.skip("prompt_toolkit not installed")

    from prompt_toolkit.document import Document
    from pycli.repl import PycliPtCompleter

    vm = DynamicObj({"cluster": "prod-k8s", "replicas": 5})
    sc = SpyCompleter({"vm": vm, "res": CommandResult("ls", "out", "", 0, 0.1)})
    pt_comp = PycliPtCompleter(sc)

    doc = Document("vm.", 3)
    completions = [c.text for c in pt_comp.get_completions(doc, None)]
    assert "vm.cluster" in completions
    assert "vm.replicas" in completions

    doc_cd = Document("cd ex", 5)
    completions_cd = [c.text.replace("\\", "/") for c in pt_comp.get_completions(doc_cd, None)]
    assert "examples/" in completions_cd


def test_spy_console_exit_and_ctrl_z():
    console = SpyConsole()
    with pytest.raises(SystemExit):
        console.runsource("exit()")
    with pytest.raises(SystemExit):
        console.runsource("quit()")
    with pytest.raises(SystemExit):
        console.runsource("exit")
    with pytest.raises(SystemExit):
        console.runsource("quit")
    with pytest.raises(SystemExit):
        console.runsource("^Z")
    with pytest.raises(SystemExit):
        console.runsource("\x1a")
