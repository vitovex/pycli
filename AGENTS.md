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

### Key Language Features to Implement

1. **Command Expressions**: `$(git status)` or `vms = $(az vm list)`
2. **Statement & Expression Forms**: Standalone commands or assigned results.
3. **Interpolation**: Variables/expressions `{var}`, `{obj.attr}`, `{func()}`.
4. **List Expansion (Splat)**: `$(rm {*files})` -> `rm a.txt b.txt`.
5. **Subcommands**: `$(echo $(git branch --show-current))`.
6. **Pipelines**: `$(kubectl get pods | grep api)`.
7. **Redirection**: `$(git status > status.txt)`.
8. **CommandResult Object**: `.stdout`, `.stderr`, `.exit_code`, `.command`, `.duration`.
9. **Truthiness**: `if $(git diff --quiet): ...` (truthy if exit_code == 0).
10. **Structured Output (JSON)**: `vms = $(az vm list).json` with dynamic attribute access (`vm.name`).
11. **Strict Mode**: `$(git status)!` raises on non-zero exit code (`run(..., check=True)`).

---

## Tooling & Environment

- **Package Manager**: `uv`
- **Python Version**: `>= 3.12`
- **Testing**: `pytest`
- **Virtual Environment**: Managed via `uv` (`uv sync`, `uv run pytest`, `uv run pycli`)

### Common Commands

```powershell
# Sync dependencies
uv sync

# Run tests
uv run pytest

# Run the CLI
uv run pycli
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
└── src/
    └── pycli/
        ├── __init__.py     # Package entrypoint & CLI
        ├── lexer.py        # Tokenizer / scanner (to be implemented)
        ├── parser.py       # Grammar parser (to be implemented)
        ├── transformer.py  # AST to Python source transformer (to be implemented)
        └── runtime.py      # Runtime support: run(), CommandResult, DynamicObj
```
