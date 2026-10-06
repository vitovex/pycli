# AGENTS.md - Developer & Agent Guide for pycli

## Project Overview

`pycli` is a Python-compatible DevOps DSL that extends Python syntax with first-class shell command execution (`$(...)`).
It transpiles `.spy` files into standard Python (`.py`) for execution on standard CPython runtimes with zero custom VM requirements.

The full language specification is defined in [docs/pycli-grammar.md](docs/pycli-grammar.md).

---

## Architecture & Pipeline

```text
.spy Source
      ↓
    Lexer
      ↓
    Parser
      ↓
AST Transform
      ↓
Python Source (using pycli runtime: run(), CommandResult)
      ↓
   CPython
```

### Key Implemented Features

1. **Command Expressions**: `$(git status)` or `vms = $(az vm list)`
2. **Statement & Expression Forms**: Standalone commands or assigned results.
3. **Interpolation**: Variables/expressions `{var}`, `{obj.attr}`, `{func()}`.
4. **List Expansion (Splat)**: `$(rm {*files})` -> `rm a.txt b.txt`.
5. **Subcommands**: `$(echo $(git branch --show-current))`.
6. **Pipelines**: `$(kubectl get pods | grep api)`.
7. **Redirection**: `$(git status > status.txt)`.
8. **CommandResult Object**: `.stdout`, `.stderr`, `.exit_code`, `.command`, `.duration`, `.lines`, `.text`, `.tee`, `.input(...)`.
9. **Truthiness & Safe Probing**: `if $(git diff --quiet): ...` (truthy if exit_code == 0) and safe mode `$(cmd)?`.
10. **Structured Output (JSON/YAML)**: `vms = $(az vm list).json` with dynamic attribute access (`vm.name`).
11. **Strict Mode**: `$(git status)!` raises on non-zero exit code (`run(..., check=True)`).
12. **Context Managers**: `cd(...)` for temporary working directory and `env(...)` for scoped environment variables.
13. **Background Jobs & Async**: `job = $(long_task) &` with `job.wait()` and `wait_all(...)`.
14. **Modular Architecture**: Native import hook (`pycli.importer`) to import `.spy` files directly into other scripts.
15. **Interactive REPL with Tab Completion & Shell Light**: Interactive console (`spy repl`), syntax highlighting, Tab completion for Python symbols/attributes and file paths, and Shell Light mode (auto-detection and direct execution of shell commands, in-process `cd`/`pwd`, and command assignment).

---

## Tooling & Environment

- **Package Manager**: `uv`
- **Python Version**: `>= 3.12`
- **Testing**: `pytest`
- **CI Matrix**: GitHub Actions runs on `ubuntu-latest`, `macos-latest`, and `windows-latest`
- **Virtual Environment**: Managed via `uv` (`uv sync`, `uv run pytest`, `uv run pycli`)

### Common Commands

```powershell
# Sync dependencies
uv sync

# Run all tests (138+ unit, integration, and security tests)
uv run pytest

# Run the CLI
uv run pycli
# or via short alias
uv run spy
```

---

## Repository Structure

```text
.
├── .gitignore
├── .python-version
├── pyproject.toml
├── README.md
├── AGENTS.md
├── docs/
│   └── pycli-grammar.md    # Language and grammar specification
├── editors/
│   ├── vscode/             # VS Code & Antigravity IDE syntax extension & snippets
│   └── notepadplusplus/    # Notepad++ UDL definition
├── examples/               # Executable .spy demonstration scripts
├── src/
│   └── pycli/
│       ├── __init__.py     # Package entrypoint & CLI dispatcher (spy / pycli)
│       ├── lexer.py        # Tokenizer / scanner for $(...) and interpolation
│       ├── parser.py       # Grammar parser and AST node builder
│       ├── transformer.py  # AST to Python source transformer
│       ├── runtime.py      # Runtime support: run(), CommandResult, DynamicObj, cd, env
│       ├── importer.py     # PEP 302/451 import hook for loading .spy modules
│       ├── highlighter.py  # ANSI syntax highlighting for terminal & CLI
│       └── repl.py         # Interactive REPL session with live transpilation
└── tests/                  # Test suite covering lexer, parser, runtime, CLI, security
```
