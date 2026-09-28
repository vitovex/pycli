"""Unit tests for advanced pycli DSL features."""

import os
from pathlib import Path
from pycli import transpile, run_file
from pycli.runtime import cd, env, CommandResult


def test_lines_and_iteration():
    """Verify CommandResult .lines, .text, and __iter__."""
    output = "alpha\nbeta\ngamma\n"
    res = CommandResult("test", output, "", 0, 0.01)

    assert res.lines == ["alpha", "beta", "gamma"]
    assert res.text == "alpha\nbeta\ngamma"
    assert list(res) == ["alpha", "beta", "gamma"]
    assert res[1] == "beta"


def test_cd_context_manager(tmp_path: Path):
    """Verify with cd(...) safely changes and restores directory."""
    original_cwd = Path.cwd()
    sub_dir = tmp_path / "sub"
    sub_dir.mkdir()

    with cd(sub_dir):
        assert Path.cwd() == sub_dir

    assert Path.cwd() == original_cwd


def test_env_context_manager():
    """Verify with env(...) safely sets and restores environment variables."""
    assert os.environ.get("PYCLI_TEST_VAR") is None

    with env(PYCLI_TEST_VAR="active_value"):
        assert os.environ.get("PYCLI_TEST_VAR") == "active_value"

    assert os.environ.get("PYCLI_TEST_VAR") is None


def test_safe_mode_transpilation():
    """Verify $(cmd)? transpiles with suppress_errors=True."""
    code = '$(exit 1)?'
    py_code = transpile(code)
    assert 'suppress_errors=True' in py_code


def test_background_transpilation():
    """Verify $(cmd) & transpiles with run_bg."""
    code = 'job = $(sleep 1) &'
    py_code = transpile(code)
    assert 'run_bg(' in py_code


def test_tee_transpilation():
    """Verify $(cmd).tee transpiles with tee=True."""
    code = 'res = $(docker build .).tee'
    py_code = transpile(code)
    assert 'tee=True' in py_code


def test_input_transpilation():
    """Verify $(cmd).input(...) transpiles with input=..."""
    code = '$(kubectl apply -f -).input(manifest)'
    py_code = transpile(code)
    assert 'input=manifest' in py_code


def test_double_vs_single_quote_interpolation(tmp_path: Path):
    """Verify that double quotes interpolate {var} and single quotes stay literal."""
    script = '''val = "dynamo"
res_double = $(echo "Value: {val}")
res_single = $(echo 'Value: {val}')

assert "Value: dynamo" in res_double.stdout
assert "Value: {val}" in res_single.stdout
'''
    script_file = tmp_path / "quotes.spy"
    script_file.write_text(script, encoding="utf-8")

    exit_code = run_file(script_file)
    assert exit_code == 0



def test_input_piping_execution(tmp_path: Path):
    """Verify .input(...) feeds stdin to the command during execution."""
    import sys
    py = sys.executable.replace("\\", "/")
    script = f'''payload = "Hello from Stdin"
res = $("{py}" -c "import sys; print('ECHO:', sys.stdin.read().strip())").input(payload)
assert "ECHO: Hello from Stdin" in res.stdout
'''
    script_file = tmp_path / "stdin_test.spy"
    script_file.write_text(script, encoding="utf-8")

    exit_code = run_file(script_file)
    assert exit_code == 0


def test_background_execution(tmp_path: Path):
    """Verify $(cmd) & returns a non-blocking background job with wait()."""
    import sys
    py = sys.executable.replace("\\", "/")
    script = f'''job = $("{py}" -c "import time; time.sleep(0.05); print('FINISHED')") &
assert job.is_running or job.poll() is not None
res = job.wait()
assert "FINISHED" in res.stdout
assert res.exit_code == 0
'''
    script_file = tmp_path / "bg_test.spy"
    script_file.write_text(script, encoding="utf-8")

    exit_code = run_file(script_file)
    assert exit_code == 0

