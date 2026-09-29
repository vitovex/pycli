import sys
from pathlib import Path
from pycli import run_file, transpile_file


def test_cli_transpile_file(tmp_path: Path):
    spy_file = tmp_path / "sample.spy"
    out_file = tmp_path / "sample.py"

    spy_file.write_text("x = $(echo hello)\nprint(x.stdout.strip())\n", encoding="utf-8")
    code = transpile_file(spy_file, out_file)

    assert out_file.exists()
    assert "from pycli.runtime import run" in code
    assert 'x = run("echo hello")' in code


def test_cli_run_file(tmp_path: Path, capsys):
    spy_file = tmp_path / "script.spy"
    spy_file.write_text(
        "res = $(echo 123)\n"
        "print('RESULT:', res.stdout.strip())\n",
        encoding="utf-8",
    )

    exit_code = run_file(spy_file)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "RESULT: 123" in captured.out


def test_cli_run_file_with_json_and_interpolation(tmp_path: Path, capsys):
    spy_file = tmp_path / "script_json.spy"
    py_exe = sys.executable.replace("\\", "/")
    spy_file.write_text(
        "import json, sys\n"
        f"py = {py_exe!r}\n"
        "val = 'world'\n"
        "msg = $({py} -c \"import json; print(json.dumps({'greet': 'hello world'}))\").json\n"
        "print('GREETING:', msg.greet)\n",
        encoding="utf-8",
    )

    exit_code = run_file(spy_file)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "GREETING: hello world" in captured.out


def test_cli_run_complex_devops_example(capsys):
    script_path = Path("examples/complex_devops.spy")
    assert script_path.exists()

    exit_code = run_file(script_path)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Initializing deployment pipeline for environment: production" in captured.out
    assert "Service IP: 10.0.8080.1 | Health: UP (Replicas: 3)" in captured.out
    assert "Service IP: 10.0.9090.1 | Health: UP (Replicas: 2)" in captured.out
    assert "Successfully caught CommandError with code 42" in captured.out
    assert "DevOps Pipeline completed successfully!" in captured.out


def test_cli_diagnostics_unclosed_and_invalid_dsl_dx02(tmp_path: Path, capsys):
    # Unclosed command expr
    unclosed_file = tmp_path / "unclosed.spy"
    unclosed_file.write_text("x = $(git status\n", encoding="utf-8")
    exit_code = run_file(unclosed_file)
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Error:" in captured.err
    assert "Unclosed" in captured.err

    # Invalid DSL syntax (empty stage)
    invalid_file = tmp_path / "invalid.spy"
    invalid_file.write_text("$(echo 1 | | cat)\n", encoding="utf-8")
    exit_code = run_file(invalid_file)
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Error:" in captured.err
    assert "Empty pipeline stage" in captured.err
