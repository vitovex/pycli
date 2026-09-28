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

run("az login", capture=False)

vms = run(f"az vm list --subscription {subscription}").json
for vm in vms:
    print(vm.name)

branch = run("git branch --show-current")
run(f"echo Current branch: {branch.stdout}", capture=False)

files = ["temp1.txt", "temp2.txt"]
run_expanded("rm", *files, capture=False)

if run("git diff --quiet"):
    print("Repository clean")
```

## CLI Usage

### Global Installation (Centralized Command)

You can install `pycli` globally into your system `PATH` using `uv tool`:

```powershell
uv tool install --editable . --force
```

This registers two commands globally on your machine:
- **`spy`**: Ultra-concise runner for `.spy` scripts.
- **`pycli`**: The full CLI tool with subcommands.

Once installed, you can run `.spy` scripts from **any folder or terminal**:
```powershell
spy script.spy
# or
pycli script.spy
```

### Direct Script Execution without Global Install

If working inside this repository with `uv`:
```powershell
uv run pycli script.spy
```

### Transpile `.spy` to `.py`

```powershell
# Output to stdout with syntax coloring
spy transpile script.spy

# Output to a file (clean Python without ANSI codes)
spy transpile script.spy -o script.py

# Force / disable color
spy transpile script.spy --color
spy transpile script.spy --no-color
```



## Supported Language Features

| Feature | Syntax Example | Target Python Equivalent |
|---|---|---|
| **Statement Form** | `$(git status)` | `run("git status", capture=False)` (streams output to console) |
| **Expression Form** | `res = $(git status)` | `res = run("git status")` (captures stdout/stderr) |
| **Strict Mode** | `$(git status)!` | `run("git status", capture=False, check=True)` |
| **Safe Mode** | `$(curl http://...) ?` | `run(..., suppress_errors=True)` (no exception on non-zero exit code) |
| **Background / Async** | `job = $(docker build .) &` | `run_bg(...)` returning `BackgroundJob` (`.wait()`, `.poll()`, `.kill()`) |
| **Async / Await** | `res = await $(git pull)` | `await async_run(...)` for `asyncio` workflows |
| **Live Stream & Capture** | `res = $(npm test).tee` | `run(..., tee=True)` (live streaming to console + captured in `res`) |
| **Stdin Piping** | `$(kubectl apply -f -).input(yaml)` | `run(..., input=yaml)` |
| **Output Line Iteration** | `for line in $(git log): ...` | Direct iteration over `res`, or `res.lines` and `res.text` |
| **Context Managers** | `with cd(dir):`, `with env(K="V"):` | Temporary directory and environment variable scoping |
| **Quote Interpolation** | `$(echo "{name}" '{raw}')` | Double quotes interpolate `{expr}`; single quotes stay strictly literal |
| **List Expansion (Splat)** | `$(rm {*files})` | `run_expanded("rm", *files, capture=False)` |
| **Pipelines** | `$(kubectl get pods \| grep api)` | `run("kubectl get pods \| grep api", capture=False)` |
| **Redirection** | `$(git status > status.txt)` | `run("git status > status.txt", capture=False)` |
| **Subcommands** | `$(echo $(git branch --show-current))` | `run("echo $(git branch --show-current)", capture=False)` |
| **Truthiness** | `if $(git diff --quiet): ...` | `if run("git diff --quiet"): ...` (truthy if `exit_code == 0`) |
| **Structured Output (JSON)** | `vms = $(az vm list).json` | Navigable `DynamicObj` via `vm.name` or `vm["name"]` |
| **Interactive REPL** | `spy repl` or `spy` | Interactive shell with on-the-fly transpilation |
| **CommandResult Properties** | `res = $(git status)` | `res.stdout`, `res.stderr`, `res.exit_code`, `res.lines`, `res.text` |
| **Modular .spy Imports** | `import devops_utils` | Seamlessly import `.spy` files and packages via Python `importlib` hook |

---

## Complete Language Syntax Reference

`spy` is a superset of standard Python. Everything that is valid in Python 3.12+ is fully valid in `.spy`. `spy` introduces the **Command Expression** `$(...)` for seamless command-line execution and shell orchestration.

### 1. Command Invocation Forms

#### Statement Form (Unassigned)
When a command expression appears as a standalone line or single-line statement:
```python
$(terraform init)
if should_apply: $(terraform apply -auto-approve)
```
- **Execution**: The command is executed and its output (`stdout` and `stderr`) is streamed live to the console in real-time.
- **Return value**: Discarded (`capture=False`).

#### Expression Form (Assigned / Inline)
When a command expression is assigned to a variable, passed as a function argument, or used in an expression:
```python
current_branch = $(git branch --show-current)
log_output = $(git log -n 10).text
```
- **Execution**: The output is captured silently and returned as a `CommandResult` instance.

---

### 2. Execution Modifiers

Modifiers are placed immediately after the closing parenthesis `)` of a command:

| Modifier | Syntax | Behavior | Python Equivalent |
|---|---|---|---|
| **Strict (`!`)** | `$(cmd)!` | Raises `CommandError` if exit code != 0 | `run(..., check=True)` |
| **Safe (`?`)** | `$(cmd)?` | Suppresses errors; never raises exceptions on failure | `run(..., suppress_errors=True)` |
| **Background (`&`)** | `job = $(cmd) &` | Spawns in background non-blockingly; returns `BackgroundJob` | `run_bg(...)` |

#### Examples:
```python
# 1. Strict mode: abort pipeline if build fails
try:
    $(docker build -t app:latest .)!
except Exception as err:
    print(f"Build failed with exit code: {err.result.exit_code}")

# 2. Safe mode: probe an endpoint or optional service without try/except
probe = $(curl -sSf http://localhost:8080/health)?
if probe:
    print("Service is healthy!")
else:
    print(f"Service offline (exit code: {probe.exit_code})")

# 3. Background job: run long-running task concurrently
job = $(mvn clean package) &
print("Maven build started in background. Running other checks...")
while job.is_running:
    # do background work...
    break
result = job.wait()
print(f"Build finished with code: {result.exit_code}")

# 4. Async / Await inside coroutines
async def pull_repo():
    res = await $(git pull origin main)
    return res.stdout
```

---

### 3. Chaining Methods & Modifiers

You can chain properties and helper methods directly onto command expressions:

#### `.json` — Structured JSON Output
Automatically parses JSON standard output into a navigable `DynamicObj`:
```python
pods = $(kubectl get pods -o json).json
for item in pods.items:
    print(f"Pod: {item.metadata.name} | Status: {item.status.phase}")
    # Supports both dot access and dict access:
    print(f"Namespace: {item['metadata']['namespace']}")
```

#### `.tee` — Live Console Streaming + Output Capture
Streams stdout/stderr in real-time to the console while simultaneously capturing the complete result in the variable:
```python
# Output is displayed immediately on screen AND stored in 'test_run'
test_run = $(pytest tests/ -v).tee
if test_run.exit_code != 0:
    print("Failed test log:", test_run.stderr)
```

#### `.input(...)` — Feeding Stdin Data
Pipes string or binary data directly into the standard input of the subprocess:
```python
manifest = """
apiVersion: v1
kind: ConfigMap
metadata:
  name: app-config
"""
$(kubectl apply -f -).input(manifest)
```

#### `.lines` & `.text`
- `.lines`: Returns a `list[str]` of non-empty lines from `stdout` (stripped of trailing newlines).
- `.text`: Returns the trimmed `stdout` string (`stdout.strip()`).
```python
branches = $(git branch --list).lines
first_line = $(head -n 1 file.txt).text
```

---

### 4. Interpolation & Quoting Semantics

`spy` provides precise rules for parameter interpolation to keep shell scripts intuitive:

#### Unquoted Variable / Expression Interpolation
Any Python expression enclosed in `{...}` is evaluated and inserted into the command string:
```python
target_cluster = "prod-us-east-1"
$(kubectl config use-context {target_cluster})
$(az vm list --resource-group {config.resource_group})
```

#### Double Quotes (`"..."`) — Interpolation Enabled
Double-quoted command strings expand `{expression}`:
```python
name = "World"
$(echo "Hello, {name}!")
# Evaluates to: echo "Hello, World!"
```

#### Single Quotes (`'...'`) — Strictly Literal
Single-quoted command strings preserve braces literally. No interpolation occurs inside single quotes. This is critical for shell tools like `awk`, regex patterns, or inline sub-scripts:
```python
# Braces remain literal {print $1}:
$(awk '{print $1}' access.log)

# Regex stays literal:
$(grep -E '^[0-9]{4}-[0-9]{2}' server.log)
```

#### List Expansion / Splat (`{*iterable}`)
Expands a Python list, tuple, or iterable into space-separated command-line arguments:
```python
files = ["service.py", "models.py", "utils.py"]
$(ruff check {*files})
# Transpiles to: run_expanded("ruff", "check", *files, capture=False)
```

---

### 5. Preserved Shell Semantics

`spy` passes commands directly to the platform's native shell environment, preserving core shell capabilities:

- **Pipelines (`|`)**:
  ```python
  $(kubectl get pods | grep api | sort)
  ```
- **Redirections (`>`, `>>`, `<`)**:
  ```python
  $(git status > current_status.txt)
  $(uptime >> uptime_history.log)
  ```
- **Subcommands (`$(...)`)**:
  Inner command substitutions are handled directly by the shell:
  ```python
  $(echo $(git rev-parse --short HEAD))
  ```

---

### 6. The `CommandResult` Object

Captured command expressions return a `CommandResult` instance with rich inspection capabilities:

| Attribute / Method | Type | Description |
|---|---|---|
| `res.stdout` | `str` | Full standard output |
| `res.stderr` | `str` | Full standard error |
| `res.exit_code` | `int` | Process exit status code (`0` = success) |
| `res.duration` | `float` | Command execution time in seconds |
| `res.command` | `str` | Exact command string executed |
| `res.lines` | `list[str]` | List of non-empty stdout lines |
| `res.text` | `str` | Trimmed standard output (`stdout.strip()`) |
| `res.json` | `DynamicObj` | Parsed JSON object / list |
| `for line in res:` | `Iterator[str]` | Iterate directly over lines in stdout |
| `res[index]` | `str` | Access a specific line by index |
| `bool(res)` | `bool` | **Truthiness**: `True` if `exit_code == 0`, else `False` |

#### Truthiness Example:
```python
if $(git diff --quiet):
    print("Working tree clean")
else:
    print("Uncommitted changes detected")
```

---

### 7. Built-in Context Managers

Every `.spy` script automatically has access to `cd()` and `env()` without manual imports:

#### `cd(path)` — Temporary Directory Navigation
Changes directory for the duration of the `with` block and guarantees restoration to the original directory upon exit:
```python
with cd("subproject"):
    $(npm install)
    $(npm run build)
# Automatically back in original working directory
```

#### `env(**kwargs)` — Temporary Environment Variables
Sets environment variables for the duration of the `with` block and safely restores the original environment afterwards:
```python
with env(DATABASE_URL="postgres://localhost:5432/test", LOG_LEVEL="DEBUG"):
    $(pytest tests/)
# Original environment restored
```

---

### 8. Modular Architecture (`.spy` Imports)

You can structure large DevOps and infrastructure projects into modular files. `.spy` scripts can import other `.spy` scripts or packages natively:

```python
# main.spy
import devops_utils
from infrastructure.cloud import deploy_cluster

status = devops_utils.get_git_status()
deploy_cluster("production")
```

The underlying import hook compiles `.spy` files into standard Python bytecode on the fly with zero disk pollution.

---

### 9. Interactive REPL

`spy` includes a dedicated interactive read-eval-print loop with instant transpilation:

```powershell
# Launch interactive shell
spy repl
# or simply
spy
```

```text
>>> branch = $(git branch --show-current).text
>>> branch
'main'
>>> for file in $(git ls-files):
...     if file.endswith(".spy"):
...         print("Found spy script:", file)
... 
```

---

## Examples

The repository includes runnable `.spy` examples in the `examples/` directory:

- [examples/demo.spy](examples/demo.spy): Basic overview demonstrating variable interpolation, list expansion, and status checking.
- [examples/syntax_reference.spy](examples/syntax_reference.spy): Comprehensive, executable reference covering every syntax construct and execution mode.
- [examples/advanced_features.spy](examples/advanced_features.spy): Practical demonstration of the 8 advanced productivity features (streaming, `.tee`, `.input(...)`, `cd()`/`env()`, background jobs `&`, safe mode `?`, quote semantics).
- [examples/complex_devops.spy](examples/complex_devops.spy): Advanced pipeline orchestrator demonstrating Python dataclasses, object-oriented design, dynamic JSON parsing, strict mode error handling (`try/except CommandError`), and splat expansion.
- [examples/modular_demo.spy](examples/modular_demo.spy) & [examples/devops_utils.spy](examples/devops_utils.spy): Modular multi-file architecture demonstrating how a `.spy` file can seamlessly import reusable functions, classes, and shell workflows from another `.spy` file or package.

Run them directly with `spy`:
```powershell
spy examples/demo.spy
spy examples/syntax_reference.spy
spy examples/advanced_features.spy
spy examples/complex_devops.spy
spy examples/modular_demo.spy
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

## Editor Support

Language extensions and syntax highlighting definitions are provided in the `editors/` directory:

- **Visual Studio Code & Antigravity IDE** ([editors/vscode](editors/vscode)): Full Python + embedded shell grammar, command delimiter highlighting, interpolation scoping, and snippets (`cmd`, `cmdvar`, `cmdjson`, `cmdstrict`, `cmdsplat`, `cmdif`).
  - To install in VSCode:
    ```powershell
    Copy-Item -Recurse -Force "editors/vscode" "$env:USERPROFILE\.vscode\extensions\pycli-vscode"
    ```
  - To install in Antigravity IDE:
    ```powershell
    Copy-Item -Recurse -Force "editors/vscode" "$env:USERPROFILE\.antigravity-ide\extensions\pycli-vscode"
    ```
- **Notepad++** ([editors/notepadplusplus](editors/notepadplusplus)): User Defined Language (UDL) definition for `.spy` files.
  - To install locally:
    ```powershell
    Copy-Item -Force "editors/notepadplusplus/pycli.xml" "$env:APPDATA\Notepad++\userDefineLangs\"
    ```

