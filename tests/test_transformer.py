from pycli.transformer import transpile


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
    assert py == 'vms = run(f"az vm list --subscription {shell_quote(subscription)}")\n'
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
    assert py == 'pods = run("kubectl get pods | grep api")\n'


def test_transpile_redirection():
    source = "$(git status > status.txt)\n"
    py = transpile(source, auto_import=False)
    assert py == 'run("git status > status.txt", capture=False)\n'


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
    assert "from pycli.runtime import run, run_expanded, shell_quote" in py
    assert 'run("az login", capture=False)' in py
    assert 'run(f"az vm list --subscription {shell_quote(subscription)}")' in py
    assert 'run("git branch --show-current")' in py
    assert 'run(f"echo Current branch: {shell_quote(branch.stdout)}", capture=False)' in py
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
