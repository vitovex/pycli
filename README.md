# pycli

[![CI Pipeline](https://github.com/vitovex/pycli/actions/workflows/ci.yml/badge.svg)](https://github.com/vitovex/pycli/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pycli-dsl.svg)](https://pypi.org/project/pycli-dsl/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-linux%20%7C%20macos%20%7C%20windows-lightgrey.svg)](https://github.com/vitovex/pycli/actions)

A lightweight Python-compatible DevOps DSL that extends Python with first-class shell command execution.

## Overview

`pycli` transpiles `.spy` files into pure standard Python (`.py`), providing:
- **Python Readability**: Natural Python syntax and standard ecosystem compatibility.
- **PowerShell-like Command Invocation**: Run shell commands directly with `$(...)`.
- **Bash-like Command Composition**: Pipelines (`|`), redirections (`>`, `>>`, `<`), and subcommands.
- **Native Object Handling**: Seamless integration with Python objects, string interpolation `{var}`, and list expansion (`{*files}`).
- **Cross-Platform Compatibility**: Tested and verified across Linux (`ubuntu-latest`), macOS (`macos-latest`), and Windows (`windows-latest`).
- **Zero Custom VM**: Transpiled directly to standard Python and executed on standard CPython.

See the full specification in [docs/pycli-grammar.md](docs/pycli-grammar.md) and release history in [CHANGELOG.md](CHANGELOG.md).

## Example

### Source DSL (`deploy.spy`)

```python
target_env = "production"
branch = $(git branch --show-current).text

# 1. Pipelines (|) and line streaming
live_pods = $(kubectl get pods -n {target_env} | grep -E 'Running|Pending').lines
for pod in live_pods:
    print(f"Active pod: {pod}")

# 2. Context managers: temporary directory (cd) and environment variables (env)
with cd("frontend"), env(NODE_ENV=target_env):
    $(npm ci)!
    build_log = $(npm run build).tee

# 3. List expansion (splat), structured JSON output, and safe probing (?)
artifacts = ["dist/app.js", "dist/app.css"]
$(gzip -k {*artifacts})

cluster = $(az aks show --name prod-cluster --resource-group {target_env}).json
print(f"Cluster FQDN: {cluster.fqdn}")

status = $(curl -sSf http://localhost:8080/health)?
if status:
    print("Health check passed successfully!")
```

### Transpiled Python (`deploy.py`)

```python
from pycli.runtime import cd, env, run, run_expanded

target_env = "production"
branch = run("git branch --show-current").text

# 1. Pipelines (|) and line streaming
live_pods = run(f"""kubectl get pods -n {target_env} | grep -E 'Running|Pending'""").lines
for pod in live_pods:
    print(f"Active pod: {pod}")

# 2. Context managers: temporary directory (cd) and environment variables (env)
with cd("frontend"), env(NODE_ENV=target_env):
    run("npm ci", capture=False, check=True)
    build_log = run("npm run build", tee=True)

# 3. List expansion (splat), structured JSON output, and safe probing (?)
artifacts = ["dist/app.js", "dist/app.css"]
run_expanded("gzip", "-k", *artifacts, capture=False)

cluster = run(f"az aks show --name prod-cluster --resource-group {target_env}").json
print(f"Cluster FQDN: {cluster.fqdn}")

status = run("curl -sSf http://localhost:8080/health", suppress_errors=True)
if status:
    print("Health check passed successfully!")
```

## Installation

`pycli` is published on PyPI as [**`pycli-dsl`**](https://pypi.org/project/pycli-dsl/).

You can install and use it directly without cloning or downloading this repository:

### Standalone CLI Tool (Recommended)

To install `spy` and `pycli` globally into your system `PATH`:

```powershell
# Using uv (fastest)
uv tool install pycli-dsl

# Or using pipx
pipx install pycli-dsl
```

This registers two commands globally in your terminal:
- **`spy`**: Ultra-concise runner for `.spy` scripts (`spy script.spy`).
- **`pycli`**: The full CLI tool with subcommands (`transpile`, `run`, `repl`).

### In a Python Environment

To install `pycli-dsl` into an existing Python environment or project:

```powershell
pip install pycli-dsl

# Or with uv
uv add pycli-dsl
```

### From Source (Local Development)

If you clone the repository for development:

```powershell
uv tool install --editable . --force
```

---

## CLI Usage

Once installed, you can run `.spy` scripts from **any folder or terminal**:
```powershell
spy script.spy
# or
pycli script.spy
```

### Direct Script Execution from Source Repo

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

# Cross-platform target transpilation (linux, win32, darwin)
spy transpile script.spy --platform linux

# Transpile directly from standard input (stdin / pipe)
echo "$(git status)" | spy transpile -

# Validate generated Python code with ast.parse
spy transpile script.spy --validate

# Disable automatic shell_quote() sanitization on interpolations
spy transpile script.spy --unsafe-interpolation
```

### Run `.spy` Scripts

```powershell
# Run a script directly
spy run script.spy
# or simply
spy script.spy

# Run directly from standard input (stdin / pipe)
echo "branch = $(git branch --show-current).text; print(branch)" | spy run -

# Run with generated Python syntax validation
spy run --validate script.spy

# Run with unsafe interpolation (disables shell_quote)
spy run --unsafe-interpolation script.spy

# Run with warning on untrusted external scripts
spy run --warn-external script.spy
```

---

## Security & Robustness

### Automatic Shell Interpolation Sanitization
By default, all variable interpolations `{var}` and dynamic redirection targets are wrapped with `shell_quote(var)` (`shlex.quote`) during transpilation. This protects against shell injection attacks if variables contain metacharacters (`;`, `&&`, `|`, etc.).
If raw, unquoted shell syntax expansion is explicitly needed, pass `--unsafe-interpolation` or use `transpile(..., unsafe_interpolation=True)`.

### Execution Privilege Model (Not a Sandbox)
`pycli` executes `.spy` files on standard CPython runtimes with the full privileges and environment of the user running the process. It is **not** a sandbox. When executing `.spy` scripts from external or untrusted sources, use `--warn-external` and verify the script contents.

### Command Execution Controls
The runtime functions `run()`, `run_expanded()`, and `async_run()` support robust controls:
- **`timeout`**: Terminate hanging processes and raise `CommandTimeoutError`.
- **`encoding`**: Custom text decoding (default `"utf-8"`, configurable to CP1252, Latin-1, etc.).
- **`max_output_bytes`**: Cap memory consumption by truncating stdout/stderr beyond a threshold (`res.truncated = True`).




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
| **Structured Output (YAML)** | `svc = $(kubectl get svc web -o yaml).yaml` | Navigable `DynamicObj` via `svc.metadata.name` or `svc["spec"]` |
| **Stdin / Pipe Execution** | `echo "$(git status)" \| spy run -` | Direct transpilation and execution from standard input or pipes |
| **Cross-Platform Target** | `spy transpile --platform linux file.spy` | Explicit platform transpilation (`linux`, `win32`, `darwin`) |
| **Environment Variables** | `$VAR`, `$VAR = "..."` | First-class environment variable access and mutation (`os.environ["VAR"]`) |
| **Interactive REPL** | `spy repl` or `spy` | Live syntax coloring, tab completion, and Shell Light mode |
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

# 3. Background jobs: run long-running tasks concurrently (&)
job = $(mvn clean package) &
print("Maven build started in background...")
result = job.wait()
print(f"Build finished with code: {result.exit_code}")

# 3.1 Waiting for multiple parallel background jobs with wait_all(...)
j_api = $(deploy-service api) &
j_web = $(deploy-service web) &
j_db  = $(deploy-service db) &

# Accepts variable arguments wait_all(j1, j2, ...) or a list wait_all([j1, j2, ...]):
all_results = wait_all(j_api, j_web, j_db)
for res in all_results:
    print(f"Completed: {res.command} (exit code: {res.exit_code})")

# 4. Async / Await inside coroutines
async def pull_repo(name: str):
    res = await $(git -C {name} pull origin main)
    return res.stdout

# 4.1 Running multiple async commands in parallel with asyncio.gather
async def update_all_microservices():
    repos = ["frontend", "backend", "worker"]
    # All pull commands execute concurrently:
    results = await asyncio.gather(*(pull_repo(r) for r in repos))
    print(f"Updated {len(results)} repositories simultaneously.")
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

#### `.yaml` — Structured YAML Output
Automatically parses YAML standard output into a navigable `DynamicObj` (requires `pyyaml`):
```python
svc = $(kubectl get svc web -o yaml).yaml
print(f"Service name: {svc.metadata.name}")
print(f"Cluster IP: {svc.spec.clusterIP}")
# Supports both dot access and dict access:
print(f"Port: {svc['spec']['ports'][0]['port']}")
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

### 5. Environment Variables (`$VARNAME`)

`spy` provides first-class support for reading, mutating, and passing environment variables across Python code and shell commands using `$VARNAME` syntax.

#### Reading Environment Variables
Any identifier starting with `$` followed by an uppercase letter (`A-Z`) or underscore (`_`) automatically transpiles to `os.environ["VARNAME"]` with automatic `import os` injection:
```python
home = $HOME
region = $AWS_REGION
print(f"Target cluster: {$CLUSTER_NAME}")
```

#### Setting Environment Variables
Mutate or declare environment variables directly using assignment:
```python
$NODE_ENV = "production"
$API_BASE_URL = "https://api.example.com"
```
Because this transpiles directly to `os.environ["VAR"] = "..."`, the environment variable is updated in the current process and automatically inherited by all subsequent commands and subprocesses.

#### Inside Shell Command Expressions
Use `$VARNAME` directly inside `$(...)` commands as standalone arguments or concatenated within composite URLs/paths:
```python
# Standalone command argument
$(kubectl -n $NAMESPACE get pods)

# Concatenated within paths / URLs
$(aws s3 cp {local_file} s3://$BUCKET/releases/)
```

#### Syntactic Disambiguation
- **Environment variables (`$VARNAME`)**: Starts with `$` followed by `[A-Z_]` and zero or more `[A-Za-z0-9_]`.
- **Command expressions (`$(...)`)**: Starts with `$(` and remains a DSL command expression or inner subcommand.
- **Literal shell tokens**: Lowercase `$foo`, `$?`, `$1`, or bare `$` are preserved as literal word tokens for the underlying shell.

---

### 6. Preserved Shell Semantics

`spy` passes command strings to the underlying shell without interfering with native shell operators.

#### Pipelines (`|`)
Connect the standard output of one command directly to the standard input of the next:
```python
# 1. Pipeline in statement form (streaming output directly to terminal)
$(kubectl get pods -n prod | grep -v Completed | sort)

# 2. Pipeline in expression form (captured and iterated)
failed_jobs = $(docker ps -a | grep "Exited (" | awk '{print $1}').lines
for container_id in failed_jobs:
    print(f"Removing dead container: {container_id}")
    $(docker rm {container_id})

# 3. Chaining with Python processing
build_errors = $(cargo check 2>&1 | grep "error\[E").lines
if build_errors:
    print(f"Found {len(build_errors)} compile errors:")
    for err in build_errors:
        print("  -", err)
```

#### Redirections (`>`, `>>`, `<`)
Direct process outputs or inputs to and from filesystem files:
```python
# Overwrite file with stdout (>)
$(terraform output -json > tf_outputs.json)

# Append to log file (>>)
$(date >> deployment.log)
$(echo "Deployed by {user} on {branch}" >> deployment.log)

# Read input from file (<)
$(mysql -u root -p{db_pass} my_database < migration.sql)!
```

#### Subcommands (`$(...)`)
Inner shell command substitutions are handled directly by the shell runtime:
```python
# Create timestamped tarball using subshell date command:
$(tar -czf backup-$(date +%Y%m%d).tar.gz /var/data)

# Create git release tag from file content:
$(git tag release-$(cat VERSION))
```

---

### 7. The `CommandResult` Object

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

### 8. Built-in Context Managers

Every `.spy` script and module automatically has access to `cd()` and `env()` as first-class primitives without requiring any manual `import` statement.

#### `with cd(path)` — Directory Navigation
Changes current working directory for the duration of the `with` block and **guarantees** restoration to the previous directory upon exiting, even if an exception occurs:
```python
# 1. Work in a specific subproject directory
with cd("services/billing"):
    $(cargo build --release)!
    $(cargo test)

# 2. Nested directory navigation
with cd("packages"):
    with cd("frontend"):
        $(npm test)
    # Automatically back in "packages"
# Automatically back in the root directory
```

#### `with env(**kwargs)` — Temporary Environment Variables
Sets or overrides environment variables for the duration of the `with` block and safely restores the original environment afterwards:
```python
with env(AWS_DEFAULT_REGION="eu-west-1", STAGE="staging"):
    $(aws s3 ls)
    $(serverless deploy)
# AWS_DEFAULT_REGION and STAGE are restored to their original values
```

#### Combining `cd()` and `env()`
You can combine multiple context managers cleanly on a single line:
```python
with cd("apps/backend"), env(DATABASE_URL="postgres://test:5432/db", LOG_LEVEL="DEBUG"):
    $(alembic upgrade head)!
    $(pytest -v)
```

---

### 9. Modular Architecture (`.spy` Imports)

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

### 10. Interactive REPL

`spy` includes a feature-packed interactive shell combining Python's power with interactive DevOps workflows:

```powershell
# Launch interactive shell
spy repl
# or simply
spy
```

#### Live Syntax Highlighting
Powered by `prompt_toolkit` and Pygments, the REPL colors Python statements and embedded `$(...)` shell expressions in real time as you type.

#### Intelligent Tab Completion
Press `Tab` anytime for rich autocompletion:
- **Python Identifiers & Builtins**: Complete variables, functions, and standard library modules.
- **Attribute Exploration**: Dot-completion for methods and properties (e.g. `res.st` -> `res.stdout`, `res.lines`).
- **Dynamic JSON / YAML Objects**: Autocomplete keys on `.json` dynamic objects (e.g. `cluster.fq` -> `cluster.fqdn`).
- **File System Paths**: Intelligent directory and file path completion (e.g. `cd src/` or `cat tests/`).

#### Shell Light Mode (Direct Shell Execution)
For common DevOps tasks, you don't even need to wrap commands in `$()`:
- **Direct Execution**: Simply type shell commands like `git status`, `docker ps`, `ls -la`, or `dir`.
- **In-Process Directory Navigation**: Running `cd <path>` changes the working directory in the Python process itself (`os.chdir`), affecting all subsequent shell and Python operations; `pwd` prints the current directory.
- **Command Output Assignment**: Assign shell command output directly to Python variables:
  ```text
  >>> branch = git branch --show-current
  >>> branch
  'main'
  >>> vms = az vm list
  ```
- **Automatic Fallback**: If an input is a Python variable or statement, it executes as pure Python.

#### Example REPL Session

```text
>>> branch = $(git branch --show-current).text
>>> branch
'main'
>>> git status -s
 M README.md
 M pyproject.toml
>>> cd src/pycli
>>> pwd
C:\Git-Sources\personal\vexvex\vlang\src\pycli
>>> for f in $(git ls-files):
...     if f.endswith(".py"):
...         print("Python file:", f)
...
```

*Exit the REPL at any time with `exit()`, `quit`, `Ctrl+D`, or `Ctrl+Z`.*

---

## Examples

The repository includes runnable `.spy` examples in the `examples/` directory:

- [examples/demo.spy](examples/demo.spy): Basic overview demonstrating variable interpolation, list expansion, and status checking.
- [examples/env_vars.spy](examples/env_vars.spy): First-class environment variable syntax ($VARNAME), setting variables, and subprocess propagation.
- [examples/syntax_reference.spy](examples/syntax_reference.spy): Comprehensive, executable reference covering every syntax construct and execution mode.
- [examples/advanced_features.spy](examples/advanced_features.spy): Practical demonstration of the 8 advanced productivity features (streaming, `.tee`, `.input(...)`, `cd()`/`env()`, background jobs `&`, safe mode `?`, quote semantics).
- [examples/parallel_async_jobs.spy](examples/parallel_async_jobs.spy): Concurrent process orchestration showing how to launch multiple background jobs with `&`, await them all with `wait_all(...)`, and coordinate async coroutines with `asyncio.gather(...)`.
- [examples/complex_devops.spy](examples/complex_devops.spy): Advanced pipeline orchestrator demonstrating Python dataclasses, object-oriented design, dynamic JSON parsing, strict mode error handling (`try/except CommandError`), and splat expansion.
- [examples/modular_demo.spy](examples/modular_demo.spy) & [examples/devops_utils.spy](examples/devops_utils.spy): Modular multi-file architecture demonstrating how a `.spy` file can seamlessly import reusable functions, classes, and shell workflows from another `.spy` file or package.

Run them directly with `spy`:
```powershell
spy examples/demo.spy
spy examples/env_vars.spy
spy examples/syntax_reference.spy
spy examples/advanced_features.spy
spy examples/parallel_async_jobs.spy
spy examples/complex_devops.spy
spy examples/modular_demo.spy
```


## Getting Started

### Prerequisites

- Python `>= 3.12`
- `uv` package manager
- Supported Platforms: Linux, macOS, and Windows (all tested in CI)

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

