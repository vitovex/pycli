# pycli

A lightweight Python-compatible DevOps DSL that extends Python with first-class shell command execution.

## Overview

`pycli` transpiles `.spy` files into pure standard Python (`.py`), providing:
- **Python Readability**: Natural Python syntax and standard ecosystem compatibility.
- **PowerShell-like Command Invocation**: Run shell commands directly with `$(...)`.
- **Bash-like Command Composition**: Pipelines (`|`), redirections (`>`, `>>`, `<`), and subcommands.
- **Native Object Handling**: Seamless integration with Python objects, string interpolation `{var}`, and list expansion `{**files}`.
- **Zero Custom VM**: Transpiled directly to standard Python and executed on standard CPython.

See the full specification in [docs/pycli-grammar.md](docs/pycli-grammar.md).

## Example

### Source DSL (`script.spy`)

```python
subscription = "prod"

$(az login)

vms = $(az vm list --subscription {subscription}).json
for vm in vms:
    print(vm.name)

branch = $(git branch --show-current)
$(echo Current branch: {branch.stdout})

files = ["temp1.txt", "temp2.txt"]
$(rm {*files})

if $(git diff --quiet):
    print("Repository clean")
```

### Transpiled Python (`script.py`)

```python
from pycli.runtime import run, run_expanded

subscription = "prod"

run("az login")

vms = run(f"az vm list --subscription {subscription}").json
for vm in vms:
    print(vm.name)

branch = run("git branch --show-current")
run(f"echo Current branch: {branch.stdout}")

files = ["temp1.txt", "temp2.txt"]
run_expanded("rm", *files)

if run("git diff --quiet"):
    print("Repository clean")
```

## CLI Usage

### Transpile `.spy` to `.py`

```powershell
# Output to stdout
uv run pycli transpile script.spy

# Output to a file
uv run pycli transpile script.spy -o script.py
```

### Run `.spy` directly

```powershell
uv run pycli run script.spy
# or shorthand:
uv run pycli script.spy
```

## Supported Language Features

| Feature | Syntax Example | Target Python Equivalent |
|---|---|---|
| **Command Expression** | `$(git status)` | `run("git status")` |
| **Strict Mode** | `$(git status)!` | `run("git status", check=True)` |
| **Interpolation** | `$(echo {name})` | `run(f"echo {name}")` |
| **List Expansion (Splat)** | `$(rm {*files})` | `run_expanded("rm", *files)` |
| **Pipelines** | `$(kubectl get pods \| grep api)` | `run("kubectl get pods \| grep api")` |
| **Redirection** | `$(git status > status.txt)` | `run("git status > status.txt")` |
| **Subcommands** | `$(echo $(git branch --show-current))` | `run("echo $(git branch --show-current)")` |
| **Truthiness** | `if $(git diff --quiet): ...` | `if run("git diff --quiet"): ...` (truthy if `exit_code == 0`) |
| **Structured Output (JSON)** | `vms = $(az vm list).json` | Navigable `DynamicObj` via `vm.name` or `vm["name"]` |
| **CommandResult Properties** | `res = $(git status)` | `res.stdout`, `res.stderr`, `res.exit_code`, `res.command`, `res.duration` |

## Examples

The repository includes runnable `.spy` examples in the `examples/` directory:

- [examples/demo.spy](examples/demo.spy): Basic overview demonstrating variable interpolation, list expansion, and status checking.
- [examples/complex_devops.spy](examples/complex_devops.spy): Advanced pipeline orchestrator demonstrating Python dataclasses, object-oriented design, dynamic JSON parsing, strict mode error handling (`try/except CommandError`), and splat expansion.

Run them directly:
```powershell
uv run pycli examples/demo.spy
uv run pycli examples/complex_devops.spy
```

## Getting Started

### Prerequisites

- Python `>= 3.12`
- `uv` package manager

### Development Setup

```powershell
# Install dependencies
uv sync

# Run tests
uv run pytest

# Run CLI help
uv run pycli --help
```
