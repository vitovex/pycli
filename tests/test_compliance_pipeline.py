"""Compliance and pipeline end-to-end regression tests (QA-01).

Tests the table of cases defined in pycli-grammar.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
import pytest

from pycli import transpile, run_file
from pycli.runtime import CommandError


@pytest.mark.parametrize(
    "source,expected_snippet",
    [
        ("$(python -c 'print(1)')\n", "capture=False"),
        ("res = $(python -c 'print(1)')\n", "res = run("),
        ("$(python -c 'print(1)')!\n", 'check=True'),
        ("$(python -c 'print(1)')?\n", 'suppress_errors=True'),
        ("job = $(python -c 'print(1)')&\n", 'run_bg('),
        ("$(python -c 'print(1)').tee\n", 'tee=True'),
        ("res = $(cat).input('hello')\n", "input='hello'"),
        ("v = 'xyz'\n$(echo {v})\n", 'run_expanded("echo", (v)'),
        ("items = ['1', '2']\n$(echo {*items})\n", 'run_expanded('),
        ("items = ['1', '2']\n$(echo {*items} | cat)\n", 'ShellOp("|")'),
        ("items = ['1', '2']\n$(echo $(echo x) {*items})\n", '"$(echo x)"'),
    ],
)
def test_compliance_matrix_transpilation(source: str, expected_snippet: str):
    target_platform = "linux" if "$(" in source[2:] else None
    py_code = transpile(source, auto_import=True, target_platform=target_platform)
    assert expected_snippet in py_code
    # Must compile without error
    compiled = compile(py_code, "<test>", "exec")
    assert compiled is not None


def test_compliance_execution_end_to_end(tmp_path: Path):
    script = tmp_path / "compliance.spy"
    py_exe = sys.executable.replace("\\", "/")
    content = f"""
import sys
py = {py_exe!r}

# Test basic assignment and execution
x = $({{py}} -c "print('hello')")
assert x.stdout.strip() == "hello"

# Test interpolation
name = "pycli"
res = $({{py}} -c "import sys; print(sys.argv[1])" {{name}})
assert res.stdout.strip() == "pycli"

# Test splat expansion
files = ["arg1", "arg2"]
count_res = $({{py}} -c "import sys; print(len(sys.argv)-1)" {{*files}})
assert count_res.stdout.strip() == "2"

# Test safe mode
bad = $({{py}} -c "import sys; sys.exit(3)")?
assert bad.exit_code == 3

# Test input chaining
inp = $({{py}} -c "import sys; print(sys.stdin.read().upper())").input("hello")
assert inp.stdout.strip() == "HELLO"
"""
    script.write_text(content, encoding="utf-8")
    exit_code = run_file(script)
    assert exit_code == 0


def test_compliance_strict_mode_failure(tmp_path: Path):
    script = tmp_path / "strict_fail.spy"
    py_exe = sys.executable.replace("\\", "/")
    content = f"""
py = {py_exe!r}
$({{py}} -c "import sys; sys.exit(7)")!
"""
    script.write_text(content, encoding="utf-8")
    exit_code = run_file(script)
    assert exit_code == 1


def test_cor02_expanded_arguments_remain_opaque_data(tmp_path: Path):
    """COR-02: Test that splat items containing '|', '>', '$(...)', quotes, and spaces remain data."""
    script = tmp_path / "cor02_data.spy"
    out_file = (tmp_path / "out.txt").as_posix()
    py_exe = sys.executable.replace("\\", "/")

    content = f"""
import sys
import json
from pathlib import Path

py = {py_exe!r}
items = ["|", "> file", "$(echo literal)", "'single'", '"double"', "with space"]

# 1. Without shell operators: each item must arrive 1:1 in sys.argv
res = $({{py}} -c "import sys, json; print(json.dumps(sys.argv[1:]))" {{*items}})
received = json.loads(res.stdout.strip())
assert received == items

# 2. With real pipeline: the pipe operates, and items are still passed cleanly as data
res_pipe = $({{py}} -c "import sys, json; print(json.dumps(sys.argv[1:]))" {{*items}} | {{py}} -c "import sys; print(sys.stdin.read().strip())")
received_pipe = json.loads(res_pipe.stdout.strip())
assert received_pipe == items

# 3. With real redirection: target receives the data cleanly
target = {out_file!r}
$({{py}} -c "import sys, json; print(json.dumps(sys.argv[1:]))" {{*items}} > {{target}})
content = Path(target).read_text(encoding="utf-8").strip()
received_redir = json.loads(content)
assert received_redir == items
"""
    script.write_text(content, encoding="utf-8")
    exit_code = run_file(script)
    assert exit_code == 0


def test_cor03_quoting_and_context_interpolation(tmp_path: Path):
    """COR-03: Test interpolation in unquoted and double-quoted contexts with spaces and quotes."""
    script = tmp_path / "cor03_quoting.spy"
    py_exe = sys.executable.replace("\\", "/")

    content = f"""
import sys

py = {py_exe!r}

# 1. Unquoted with spaces: remains a single argument, without literal extra quotes
name1 = "alpha beta"
res1 = $({{py}} -c "import sys; print(sys.argv[1])" {{name1}})
assert res1.stdout.strip() == "alpha beta"

# 2. Inside double quotes: remains a single argument, without literal extra quotes
name2 = "alpha beta"
res2 = $({{py}} -c "import sys; print(sys.argv[1])" "{{name2}}")
assert res2.stdout.strip() == "alpha beta"

# 3. Special characters & mixed quotes
name3 = "hello'world \\"quoted\\" & more"
res3 = $({{py}} -c "import sys; print(sys.argv[1])" {{name3}})
assert res3.stdout.strip() == name3
"""
    script.write_text(content, encoding="utf-8")
    exit_code = run_file(script)
    assert exit_code == 0


def test_compliance_all_examples_transpile_and_compile():
    examples_dir = Path("examples")
    spy_files = list(examples_dir.glob("*.spy"))
    assert len(spy_files) > 0, "No .spy examples found"

    for spy_path in spy_files:
        source = spy_path.read_text(encoding="utf-8")
        py_code = transpile(source, auto_import=True)
        # Verify it compiles as valid Python
        compiled = compile(py_code, str(spy_path), "exec")
        assert compiled is not None


def test_cor03_mandatory_regression_matrix(tmp_path: Path, monkeypatch):
    """COR-03: Mandatory regression matrix for portable argument passing.

    Tests: alpha beta, a%b, %PYCLI_TEST_VALUE%, a^b, a&b, a|b, a>b, a"b, a\\b, ends\\,
    a!b, newline, single/double quotes, and harmless marker.
    Ensures zero alteration of values and no injected commands across splat, pipe, and redirection.
    """
    monkeypatch.setenv("PYCLI_TEST_VALUE", "SECRET_EXPANSION_MUST_NOT_HAPPEN")
    py_exe = sys.executable.replace("\\", "/")
    out_file = (tmp_path / "matrix_out.json").as_posix()
    marker_file = (tmp_path / "marker_injected.txt").as_posix()
    script = tmp_path / "cor03_matrix.spy"

    content = f"""
import sys
import json
from pathlib import Path

py = {py_exe!r}
target_file = {out_file!r}
marker_file = {marker_file!r}

matrix = [
    "alpha beta",
    "a%b",
    "%PYCLI_TEST_VALUE%",
    "a^b",
    "a&b",
    "a|b",
    "a>b",
    'a"b',
    "a\\\\b",
    "ends\\\\",
    "a!b",
    "line1\\nline2",
    "'single'",
    '"double"',
    f"& echo INJECTED > {{marker_file}}",
]

for val in matrix:
    items = [val]

    # 1. Direct argv in splat: must arrive byte-for-byte as data
    res = $({{py}} -c "import sys, json; print(json.dumps(sys.argv[1:]))" {{*items}})
    assert res.exit_code == 0
    received = json.loads(res.stdout.strip())
    assert received == [val], f"Direct argv mismatch: received {{received!r}} expected {{[val]!r}}"
    assert not Path(marker_file).exists(), "Injected command was executed!"

    # 2. Pipeline connection
    res_pipe = $({{py}} -c "import sys, json; print(json.dumps(sys.argv[1:]))" {{*items}} | {{py}} -c "import sys; print(sys.stdin.read().strip())")
    assert res_pipe.exit_code == 0
    received_pipe = json.loads(res_pipe.stdout.strip())
    assert received_pipe == [val], f"Pipeline mismatch: received {{received_pipe!r}} expected {{[val]!r}}"
    assert not Path(marker_file).exists(), "Injected command was executed in pipeline!"

    # 3. Redirection
    $({{py}} -c "import sys, json; print(json.dumps(sys.argv[1:]))" {{*items}} > {{target_file}})
    content_str = Path(target_file).read_text(encoding="utf-8").strip()
    received_redir = json.loads(content_str)
    assert received_redir == [val], f"Redirection mismatch: received {{received_redir!r}} expected {{[val]!r}}"
    assert not Path(marker_file).exists(), "Injected command was executed in redirection!"
"""
    script.write_text(content, encoding="utf-8")
    exit_code = run_file(script)
    assert exit_code == 0


def test_cor03_mandatory_regression_matrix_simple_interpolation(tmp_path: Path):
    """Verify that simple interpolation WITHOUT splat routes to direct argv and preserves data verbatim."""
    script = tmp_path / "test_simple_interp.spy"
    marker = tmp_path / "injected_marker.txt"
    target = tmp_path / "target_out.txt"

    content = f"""import os, sys, json
from pathlib import Path

py = sys.executable
os.environ["PYCLI_TEST_VALUE"] = "env_var_expanded_fail"
marker_file = {json.dumps(str(marker))}
target_file = {json.dumps(str(target))}

matrix = [
    "alpha beta",
    "a%b",
    "%PYCLI_TEST_VALUE%",
    "a^b",
    "a&b",
    "a|b",
    "a>b",
    'a"b',
    "a\\\\b",
    "ends\\\\",
    "a!b",
    "line1\\nline2",
    "'single'",
    '"double"',
    f"& echo INJECTED > {{marker_file}}",
]

for val in matrix:
    # 1. Direct unquoted simple interpolation: must arrive byte-for-byte without shell mangling
    res = $({{py}} -c "import sys, json; print(json.dumps(sys.argv[1:]))" {{val}})
    assert res.exit_code == 0, f"Exit code non-zero for {{val!r}}: {{res.stderr}}"
    received = json.loads(res.stdout.strip())
    assert received == [val], f"Simple interp mismatch: received {{received!r}} expected {{[val]!r}}"
    assert not Path(marker_file).exists(), f"Injected command was executed for {{val!r}}!"

    # 2. Quoted interpolation: must concatenate in argument without shell quoting
    res_quoted = $({{py}} -c "import sys, json; print(json.dumps(sys.argv[1:]))" "prefix {{val}} suffix")
    assert res_quoted.exit_code == 0
    received_quoted = json.loads(res_quoted.stdout.strip())
    expected_quoted = [f"prefix {{val}} suffix"]
    assert received_quoted == expected_quoted, f"Quoted interp mismatch: received {{received_quoted!r}} expected {{expected_quoted!r}}"
    assert not Path(marker_file).exists(), f"Injected command executed in quoted interp for {{val!r}}!"

    # 3. Simple interpolation with pipe
    res_pipe = $({{py}} -c "import sys, json; print(json.dumps(sys.argv[1:]))" {{val}} | {{py}} -c "import sys; print(sys.stdin.read().strip())")
    assert res_pipe.exit_code == 0
    received_pipe = json.loads(res_pipe.stdout.strip())
    assert received_pipe == [val], f"Pipe interp mismatch: received {{received_pipe!r}} expected {{[val]!r}}"
    assert not Path(marker_file).exists(), f"Injected command executed in pipe for {{val!r}}!"
"""
    script.write_text(content, encoding="utf-8")
    exit_code = run_file(script)
    assert exit_code == 0


def test_cor02_large_pipeline_stderr_and_echo_drainage(tmp_path: Path):
    """Verify that multi-stage pipelines do not deadlock on large (>64 KB) stderr or portable echo output."""
    script = tmp_path / "test_large_pipeline.spy"

    content = """import sys

py = sys.executable

# 1. Upstream stage writes ~200 KB on stderr while piping stdout to downstream stage
res_err = $({py} -c "import sys; sys.stderr.write('E' * 200000); sys.stderr.flush(); print('P1_STDOUT')" | {py} -c "import sys; print(sys.stdin.read().strip())")
assert res_err.exit_code == 0, f"Exit code non-zero: {res_err.stderr}"
assert res_err.stdout.strip() == "P1_STDOUT"
assert len(res_err.stderr) == 200000, f"Expected 200000 chars of stderr, got {len(res_err.stderr)}"

# 2. Portable echo stage writing ~200 KB into pipe to downstream stage
large_str = "X" * 200000
res_echo = $(echo {large_str} | {py} -c "import sys; print(len(sys.stdin.read().strip()))")
assert res_echo.exit_code == 0, f"Exit code non-zero: {res_echo.stderr}"
assert int(res_echo.stdout.strip()) == 200000, f"Expected 200000 chars received from echo, got {res_echo.stdout}"
"""
    script.write_text(content, encoding="utf-8")
    exit_code = run_file(script)
    assert exit_code == 0


def test_cor01_background_structured_routing_and_splat(tmp_path: Path):
    """Verify $(...) & preserves dangerous values and expands splats without cmd.exe mangling."""
    script = tmp_path / "test_bg.spy"
    marker_file = (tmp_path / "injected_bg.txt").as_posix()
    py_exe = sys.executable.replace("\\", "/")

    content = f"""
import sys
import json
from pathlib import Path

py = {py_exe!r}
marker_file = {marker_file!r}

# 1. Dangerous values in background jobs
matrix = [
    "a%b",
    "%PYCLI_TEST_VALUE%",
    "a^b",
    "a&b",
    "a|b",
    'a"b',
    "ends\\\\",
    "line1\\nline2",
    f"& echo INJECTED > {{marker_file}}",
]

for val in matrix:
    job = $(echo {{val}}) &
    res = job.wait(timeout=5)
    assert res.exit_code == 0
    assert res.stdout == f"{{val}}\\n"
    assert not Path(marker_file).exists(), f"Injected command executed in background for {{val!r}}!"

# 2. Splat expansion in background jobs
items = ["first", "second", "third"]
job_splat = $(echo {{*items}}) &
res_splat = job_splat.wait(timeout=5)
assert res_splat.exit_code == 0
assert res_splat.stdout.strip() == "first second third"
"""
    script.write_text(content, encoding="utf-8")
    exit_code = run_file(script)
    assert exit_code == 0


def test_cor01_async_structured_routing_splat_tee_input(tmp_path: Path):
    """Verify await $(...) preserves dangerous values, expands splats, and supports .tee and .input."""
    script = tmp_path / "test_async.spy"
    marker_file = (tmp_path / "injected_async.txt").as_posix()
    py_exe = sys.executable.replace("\\", "/")

    content = f"""
import sys
import json
import asyncio
from pathlib import Path

py = {py_exe!r}
marker_file = {marker_file!r}

async def main_async():
    # 1. Dangerous values in await
    matrix = [
        "a%b",
        "%PYCLI_TEST_VALUE%",
        "a^b",
        "a&b",
        "a|b",
        'a"b',
        "ends\\\\",
        "line1\\nline2",
        f"& echo INJECTED > {{marker_file}}",
    ]

    for val in matrix:
        res = await $(echo {{val}})
        assert res.exit_code == 0
        assert res.stdout == f"{{val}}\\n"
        assert not Path(marker_file).exists(), f"Injected command executed in async for {{val!r}}!"

    # 2. Splat expansion in await
    items = ["alpha", "beta", "gamma"]
    res_splat = await $(echo {{*items}})
    assert res_splat.exit_code == 0
    assert res_splat.stdout.strip() == "alpha beta gamma"

    # 3. Modifiers on await: .tee and .input
    res_tee = await $(echo hello_tee).tee
    assert res_tee.exit_code == 0
    assert res_tee.stdout.strip() == "hello_tee"

    res_input = await $({{py}} -c "import sys; print(sys.stdin.read().upper().strip())").input("payload_123")
    assert res_input.exit_code == 0
    assert res_input.stdout.strip() == "PAYLOAD_123"

asyncio.run(main_async())
"""
    script.write_text(content, encoding="utf-8")
    exit_code = run_file(script)
    assert exit_code == 0


def test_cor01_background_redirection_mode(tmp_path: Path):
    """Verify background redirection with > and >> works without AttributeError and creates/appends files."""
    from pycli.runtime import run_bg, ShellOp

    target = tmp_path / "bg_out.txt"

    # 1. Truncate / write (>)
    job1 = run_bg("echo", "hello", ShellOp(">"), str(target))
    res1 = job1.wait(timeout=5)
    assert res1.exit_code == 0
    assert target.read_text(encoding="utf-8").strip() == "hello"

    # 2. Append (>>)
    job2 = run_bg("echo", "world", ShellOp(">>"), str(target))
    res2 = job2.wait(timeout=5)
    assert res2.exit_code == 0
    assert target.read_text(encoding="utf-8").strip().splitlines() == ["hello", "world"]


def test_cor02_background_pipeline_large_stderr_drainage(tmp_path: Path):
    """Verify background pipelines drain upstream stderr (>64 KB) without deadlocking or timing out."""
    script = tmp_path / "test_bg_pipe_err.spy"
    py_exe = sys.executable.replace("\\", "/")

    content = f"""
import sys

py = {py_exe!r}

# First stage writes ~200 KB on stderr while piping to second stage in background
job = $({{py}} -c "import sys; sys.stderr.write('E' * 200000); sys.stderr.flush(); print('P1_OUT')" | {{py}} -c "import sys; print(sys.stdin.read().strip())") &

res = job.wait(timeout=5)
assert res.exit_code == 0, f"Background pipeline failed: {{res.stderr[:200]}}"
assert res.stdout.strip() == "P1_OUT"
assert len(res.stderr) == 200000, f"Expected 200000 chars of stderr, got {{len(res.stderr)}}"
"""
    script.write_text(content, encoding="utf-8")
    exit_code = run_file(script)
    assert exit_code == 0



