# pycli: Python-Compatible DevOps DSL Specification
## Complete Language Specification (V1)

---

# Overview

This document specifies a lightweight Python-compatible scripting language that extends Python with first-class shell command execution.

The language is designed primarily for:

- DevOps
- Cloud automation
- Infrastructure as Code
- SRE tooling
- CI/CD scripting

The language is **not a new runtime**.

Instead, it is a:

```text
Source-to-Source Transpiler
```

that transforms a custom dialect into standard Python.

Execution flow:

```text
.spy Source
      ↓
Lexer
      ↓
Parser
      ↓
AST Transform
      ↓
Python Source
      ↓
CPython
```

Example:

Input:

```python
vms = $(az vm list)
```

Generated Python:

```python
vms = run("az vm list")
```

---

# Design Goals

## Goals

- Stay very close to Python.
- Avoid introducing new keywords.
- Allow inline shell commands.
- Preserve Bash and PowerShell syntax.
- Support structured command outputs.
- Support Python interpolation.
- Remain transpileable to ordinary Python.

## Non Goals

- Custom VM.
- Custom bytecode.
- Python replacement.
- Shell replacement.
- Runtime interpreter.

---

# File Extensions

Example:

```text
deploy.spy
infra.spy
pipeline.spy
```

Transpilation:

```text
*.spy
    ↓
*.py
```

---

# Core Concept

The language introduces one new expression:

```python
$(...)
```

called a:

```text
CommandExpression
```

Examples:

```python
$(git status)

$(az vm list)

$(kubectl get pods)
```

---

# Command Expression

## Syntax

```ebnf
command_expression
    ::= "$("
           command_body
        ")"
```

Examples:

```python
result = $(git status)

vms = $(az vm list)

pods = $(kubectl get pods -o json)
```

---

# Statement Form

Command expressions may appear as standalone statements.

Example:

```python
$(git status)

$(terraform apply)
```

Generated Python:

```python
run("git status", capture=False)

run("terraform apply", capture=False)
```

Output is streamed to the console (sys.stdout/sys.stderr) and the return value is discarded.

---

# Expression Form

Command expressions may be assigned.

Example:

```python
branch = $(git branch --show-current)
```

Generated:

```python
branch = run(
    "git branch --show-current"
)
```

---

# Parsing Model

The parser recognizes only the outermost DSL command expression.

Example:

```python
$(echo $(hostname))
```

The outer expression:

```python
$( ...)
```

belongs to the DSL.

The inner expression:

```bash
$(hostname)
```

belongs to Bash.

Generated:

```python
run(
    "echo $(hostname)"
)
```

---

# Fundamental Rule

The transpiler must only interpret:

```python
{python_expression}
```

All other command syntax is preserved.

This includes:

```bash
$(...)
$HOME
$1
$VAR
|
>
>>
<
```

---

# Syntax and Grammar Specification (V1 Completed)

## Modifiers

Command expressions support trailing modifiers:

- `$(...)!`: Strict mode — raises `CommandError` if exit code is non-zero (transpiles with `check=True`).
- `$(...)?`: Safe mode — suppresses exceptions even if error occurs (transpiles with `suppress_errors=True`).
- `$(...)&`: Background execution — returns a `BackgroundJob` non-blocking process (transpiles with `run_bg(...)`).

## Method Chaining

- `$(...).tee`: Live streaming while capturing output (`tee=True`).
- `$(...).input(data)`: Feeds string or bytes to standard input of the process (`input=data`).
- `$(...).json`: Parses JSON output and wraps dicts into attribute-accessible dynamic objects.
- `$(...).lines`: Splitted list of output lines with trailing newlines stripped.
- `$(...).text`: Stripped stdout string.

## DSL Boundaries & Execution Matrix

| Construct | Example | Ownership | Generated Form |
|---|---|---|---|
| Command Statement | `$(git status)` | DSL | `run("git status", capture=False)` |
| Command Expression | `res = $(git status)` | DSL | `res = run("git status")` |
| Strict Mode | `$(git status)!` | DSL | `run("git status", check=True)` |
| Safe Mode | `$(git status)?` | DSL | `run("git status", suppress_errors=True)` |
| Background Execution | `$(sleep 5)&` | DSL | `run_bg("sleep 5")` |
| Chaining `.tee` | `$(build).tee` | DSL | `run("build", tee=True)` |
| Chaining `.input` | `$(grep api).input(text)` | DSL | `run("grep api", input=text)` |
| Environment Variable | `$VAR` / `$(echo $VAR)` | DSL | `os.environ["VAR"]` / `run_expanded(..., os.environ["VAR"])` |
| Shell Variable (non-env) | `$(echo $1)` / `$(echo $?)` | Shell | Literal word token passed to shell |
| Inner Subcommand | `$(echo $(uname -r))` | Shell (POSIX) | `run("echo $(uname -r)")` (raises TranspilerError on Windows) |
| Interpolation | `$(echo {name})` | DSL (Python) | `run(f"echo {shell_quote(name)}")` |
| Splat Expansion | `$(rm {*files})` | DSL (Python) | `run_expanded("rm", *files)` |
| Splat with Pipeline | `$(echo {*items} \| cat)` | Hybrid | `run_expanded("echo", *items, ShellOp("\|"), "cat")` |
| Splat with Redirection | `$(echo {*items} > {out})` | Hybrid | `run_expanded("echo", *items, ShellOp(">"), (out))` |
| Single Quotes | `$(awk '{print $1}')` | Shell | `run("awk '{print $1}'")` (braces untouched) |
| Double Quotes | `$(echo "Hello {name}")` | Hybrid | `run(f'echo "Hello {shell_quote(name)}"')` |

---

# Error Handling & Validation Contract

- **DSL Errors (Compile/Transpile Time)**:
  - Unclosed command expression `$(`: raises `LexerError` with line and column.
  - Unclosed `{` interpolation or `{*` splat: raises `ParseError` with line and column.
  - Empty splat `{*}` or empty interpolation `{}`: raises `ParseError`.
  - Empty pipeline stage (e.g. `$(cat \| \| grep)` or `$(cat \|)`): raises `ParseError`.
  - Missing redirection target (e.g. `$(echo >)`): raises `ParseError`.
  - Nested command substitution on Windows: raises `TranspilerError` with line and column.
- **Shell Errors (Runtime)**:
  - Exit code non-zero in default mode: sets `.exit_code`, truthiness `bool(result)` evaluates to `False`.
  - Exit code non-zero with `!` (strict mode): raises `CommandError`.
  - Exit code non-zero with `?` (safe mode): suppresses exceptions even if check is requested.

---

# Interoperability & Portability Matrix (COR-01, COR-04, COR-07)

`pycli` separates portable DSL constructs from platform-specific host shell features:

| Construct | Category | POSIX (`/bin/sh`, Bash) | Windows (`cmd.exe`) | Notes |
|---|---|---|---|---|
| Direct commands `$(cmd arg)` | **Portable** | Supported | Supported | Executable resolved via system `PATH` or Python virtual environment |
| Unquoted Interpolation `{var}` | **Portable** | Supported | Supported | Routed to structured `argv` (`shell=False`); spaces and special characters remain a single discrete data argument |
| Quoted Interpolation `"{var}"` | **Portable** | Supported | Supported | Formatted as argument f-strings without shell quoting; internal quotes and delimiters preserved as data |
| Splat expansion `{*items}` | **Portable** | Supported | Supported | Elements in `{*items}` are opaque data arguments; elements like `"\|"` or `"> file"` never morph into operators |
| Pipeline `\|` | **Portable** | Supported | Supported | Connects stdout of left stage to stdin of right stage via OS pipes; concurrent draining prevents buffer deadlocks |
| Redirection `>`, `>>`, `<` | **Portable** | Supported | Supported | Redirects stdout/stdin to file target via Python `open()` with `finally` cleanup |
| Built-in `echo` | **Portable** | Supported | Supported | Emitted directly without invoking `cmd.exe` built-in `echo` |
| Modifiers `!`, `?`, `&` | **Portable** | Supported | Supported | Strict mode, safe mode, and background jobs managed directly by runtime |
| Chaining `.tee`, `.input()` | **Portable** | Supported | Supported | Streaming and stdin injection managed directly by runtime |
| Shell variables `$VAR` vs `%VAR%` | **Platform-specific** | `$VAR`, `$HOME` | `%VAR%` | Preserved verbatim; interpreted only by the host shell |
| Shell command substitution `$(cmd)` | **Platform-specific** | Native subshell | Explicit DSL error | On Windows, `cmd.exe` does not support subshells; raises `TranspilerError` with line/col pointing to inner `$(` |

---

# Platform Notes: POSIX vs Windows

1. **POSIX (Linux / macOS)**:
   - Commands execute through the POSIX standard shell (`/bin/sh`) or direct argv for portable pipelines.
   - Argument quoting for native shell uses `shlex.quote()`.
   - Pipelines, redirections, and subshells `$(...)` execute natively.
2. **Windows**:
   - Portable pipelines, splat commands, and interpolated commands execute with `shell=False` via direct `argv`, avoiding all `cmd.exe` quoting hazards (`%`, `^`, `&`, `\`, newlines).
   - Shell-native commands without interpolation execute through `cmd.exe` (`%COMSPEC%`).
   - When shell operators are present in portable stages, non-operator arguments are passed as discrete data arguments, ensuring metacharacters (`|`, `>`, `<`) within data variables cannot hijack command structure.
   - Built-in `echo` is handled by the portable runner, writing arguments joined by spaces with newline to stdout/pipe/redirection.
3. **CI Matrix & Portability Status**:
   - Multi-platform CI configuration is provided in `.github/workflows/ci.yml` (`ubuntu-latest`, `windows-latest`, `macos-latest` using `uv sync --locked --dev` and `uv run --locked pytest`).
   - Verified on local Windows (tests passing without manual `PATH` modifications) and Ubuntu WSL2. macOS validation is pending remote CI workflow execution on GitHub.