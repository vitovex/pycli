from pycli.transformer import Transformer, transpile


def test_transpile_simple_statement():
    source = "$(git status)\n"
    py = transpile(source, auto_import=False)
    assert py == 'run("git status", capture=False)\n'


def test_transpile_simple_assignment():
    source = "vms = $(az vm list)\n"
    py = transpile(source, auto_import=False)
    assert py == 'vms = run("az vm list")\n'


def test_transpile_strict_mode():
    source = "$(git status)!\n"
    py = transpile(source, auto_import=False)
    assert py == 'run("git status", capture=False, check=True)\n'


def test_transpile_interpolation():
    source = "vms = $(az vm list --subscription {subscription})\n"
    py = transpile(source, auto_import=False)
    assert py == 'vms = run_expanded("az", "vm", "list", "--subscription", (subscription))\n'
    py_unsafe = transpile(source, auto_import=False, unsafe_interpolation=True)
    assert py_unsafe == 'vms = run(f"az vm list --subscription {subscription}")\n'


def test_transpile_splat():
    source = "files = ['a.txt', 'b.txt']\n$(rm {*files})\n"
    py = transpile(source, auto_import=False)
    assert 'run_expanded("rm", *files, capture=False)' in py


def test_transpile_splat_strict():
    source = "$(rm -rf {*files})!\n"
    py = transpile(source, auto_import=False)
    assert 'run_expanded("rm", "-rf", *files, capture=False, check=True)' in py


def test_transpile_chained_json():
    source = "vms = $(az vm list).json\n"
    py = transpile(source, auto_import=False)
    assert py == 'vms = run("az vm list").json\n'


def test_transpile_truthiness_condition():
    source = "if $(git diff --quiet):\n    print('clean')\n"
    py = transpile(source, auto_import=False)
    assert py == 'if run("git diff --quiet"):\n    print(\'clean\')\n'


def test_transpile_pipeline():
    source = "pods = $(kubectl get pods | grep api)\n"
    py = transpile(source, auto_import=False)
    assert py == 'pods = run_expanded("kubectl", "get", "pods", ShellOp("|"), "grep", "api")\n'


def test_transpile_redirection():
    source = "$(git status > status.txt)\n"
    py = transpile(source, auto_import=False)
    assert py == 'run_expanded("git", "status", ShellOp(">"), "status.txt", capture=False)\n'


def test_transpile_auto_import():
    source = "x = $(echo hello)\nfiles = [1, 2]\n$(rm {*files})\n"
    py = transpile(source, auto_import=True)
    assert py.startswith("from pycli.runtime import run, run_expanded\n")


def test_full_grammar_example():
    source = """subscription = "prod"

$(az login)

vms = $(az vm list --subscription {subscription})

for vm in vms.json:
    print(vm.name)

branch = $(git branch --show-current)

$(echo Current branch: {branch.stdout})

files = ["temp1.txt", "temp2.txt"]

$(rm {*files})

if $(git diff --quiet):
    print("Repository clean")
"""
    py = transpile(source)
    assert "from pycli.runtime import run, run_expanded" in py
    assert 'run("az login", capture=False)' in py
    assert 'run_expanded("az", "vm", "list", "--subscription", (subscription))' in py
    assert 'run("git branch --show-current")' in py
    assert 'run_expanded("echo", "Current", "branch:", (branch.stdout), capture=False)' in py
    assert 'run_expanded("rm", *files, capture=False)' in py
    assert 'if run("git diff --quiet"):' in py


def test_transpile_statement_vs_expression_context():
    # Standalone statement with comment
    source1 = "$(echo 1) # comment\n"
    assert transpile(source1, auto_import=False) == 'run("echo 1", capture=False) # comment\n'

    # Inside if block statement
    source2 = "if True:\n    $(echo 2)\n"
    assert transpile(source2, auto_import=False) == 'if True:\n    run("echo 2", capture=False)\n'

    # Single-line if statement
    source3 = "if True: $(echo 3)\n"
    assert transpile(source3, auto_import=False) == 'if True: run("echo 3", capture=False)\n'

    # In expression (inside parentheses)
    source4 = "print($(echo 4))\n"
    assert transpile(source4, auto_import=False) == 'print(run("echo 4"))\n'

    # In expression (inside list)
    source5 = "items = [$(echo 5)]\n"
    assert transpile(source5, auto_import=False) == 'items = [run("echo 5")]\n'


def test_transpile_future_import_and_docstring():
    source = (
        '"""Module docstring."""\n'
        'from __future__ import annotations\n'
        '$(echo hello)\n'
    )
    result = transpile(source, auto_import=True)
    # Must compile cleanly without SyntaxError about future import
    compiled = compile(result, "<test>", "exec")
    assert compiled is not None
    # Docstring must remain module docstring
    ns = {}
    exec(compiled, ns)
    assert ns.get("__doc__") == "Module docstring."
    # from __future__ must come before from pycli.runtime
    lines = result.splitlines()
    doc_idx = next(i for i, l in enumerate(lines) if "Module docstring." in l)
    future_idx = next(i for i, l in enumerate(lines) if "from __future__" in l)
    runtime_idx = next(i for i, l in enumerate(lines) if "from pycli.runtime" in l)
    assert doc_idx < future_idx < runtime_idx


def test_transpile_existing_multiline_import():
    source = (
        'from pycli.runtime import (\n'
        '    run_bg,\n'
        ')\n'
        '$(echo 1)\n'
    )
    result = transpile(source, auto_import=True)
    compiled = compile(result, "<test>", "exec")
    assert compiled is not None
    assert "run" in result


def test_transpile_expanded_pipeline_preservation_par04():
    # Pipeline separator must be preserved as ShellOp("|") when splat is used
    source = "items = ['a', 'b']\n$(echo {*items} | cat)\n"
    py = transpile(source, auto_import=False)
    assert 'ShellOp("|")' in py
    assert 'run_expanded("echo", *items, ShellOp("|"), "cat", capture=False)' in py


def test_transpile_expanded_subcommand_preservation_par05():
    # Subcommand must remain literal/f-string $(...), not $(PipelineNode(...)) on POSIX
    source = "items = ['a', 'b']\n$(echo $(printf x) {*items})\n"
    py = transpile(source, auto_import=False, target_platform="linux")
    assert "PipelineNode" not in py
    assert '"$(printf x)"' in py


def test_transpile_nested_subcommand_windows_error_cor04():
    """COR-04: On Windows, nested subcommands $(...) raise TranspilerError with line/col."""
    import pytest
    from pycli.transformer import TranspilerError

    # 1. Statement
    with pytest.raises(TranspilerError) as exc_stmt:
        transpile("$(echo $(git branch))\n", target_platform="win32")
    assert exc_stmt.value.line == 1
    assert exc_stmt.value.column == 8
    assert "not supported on Windows" in str(exc_stmt.value)

    # 2. Expression
    with pytest.raises(TranspilerError) as exc_expr:
        transpile("res = $(echo $(git branch))\n", target_platform="win32")
    assert exc_expr.value.line == 1
    assert exc_expr.value.column == 14

    # 3. Splat combination
    with pytest.raises(TranspilerError) as exc_splat:
        transpile("$(echo $(git branch) {*files})\n", target_platform="win32")
    assert exc_splat.value.line == 1
    assert exc_splat.value.column == 8

    # 4. Single-quoted substring remains literal and succeeds on Windows
    py_literal = transpile("$(echo '$(git branch)')\n", auto_import=False, target_platform="win32")
    assert "'$(git branch)'" in py_literal


def test_transpile_no_duplicate_interpolation_trf01():
    # $(echo "a {name}" {*items}) should not emit an extra {name}
    source = "name = 'test'\nitems = ['a']\n$(echo \"a {name}\" {*items})\n"
    py = transpile(source, auto_import=False)
    # Count occurrences of 'name' inside the run_expanded call
    # It should only appear once inside f"a {name}"
    run_line = [l for l in py.splitlines() if "run_expanded" in l][0]
    assert run_line.count("name") == 1


def test_transpile_await_statement_and_expression_trf03():
    # Standalone await should have capture=False
    stmt_source = "async def f():\n    await $(deploy)\n"
    stmt_py = transpile(stmt_source, auto_import=False)
    assert 'async_run("deploy", capture=False)' in stmt_py

    # Assigned await should capture output
    expr_source = "async def f():\n    res = await $(deploy)\n"
    expr_py = transpile(expr_source, auto_import=False)
    assert 'res = await async_run("deploy")' in expr_py


def test_transpile_multiline_parentheses_expression_trf03():
    # $(cmd) inside unclosed multiline parentheses must NOT have capture=False
    source = "x = (\n    $(git status)\n)\n"
    py = transpile(source, auto_import=False)
    assert "capture=False" not in py
    assert 'run("git status")' in py


def test_transpile_chaining_input_quotes_and_tee_trf03():
    # .input(f(")")) with quotes should not close input early
    source = 'res = $(cat).input("hello ) world")\n'
    py = transpile(source, auto_import=False)
    assert 'input="hello ) world"' in py

    # .tee.input chaining
    source_combo = 'res = $(cat).tee.input("data")\n'
    py_combo = transpile(source_combo, auto_import=False)
    assert 'tee=True' in py_combo
    assert 'input="data"' in py_combo


def test_transpile_source_map_exact_lines_dx01():
    # Verify no phantom lines in source map
    source = "$(echo 1)\n$(echo 2)\n$(echo 3)\n"
    transformer = Transformer(auto_import=False)
    transformer.transform(source)
    # Three lines in source, three lines in output
    assert transformer.source_map == {1: 1, 2: 2, 3: 3}
