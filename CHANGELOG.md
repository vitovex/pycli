# Changelog

All notable changes to `pycli` (`pycli-dsl`) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [v0.3.2] - 2026-10-07

### Added
- **Structured YAML Output**: Added `.yaml` property to `CommandResult` (e.g. `$(kubectl get svc -o yaml).yaml`), returning a `DynamicObj` supporting both attribute dot notation (`obj.metadata.name`) and key indexing (`obj["spec"]`). Gracefully handles optional `pyyaml` dependency.
- **Cross-Platform Target Transpilation**: Added `--platform` / `--target-platform` flag (`linux`, `win32`, `darwin`) to `spy transpile` to generate platform-specific target code independently of the host machine.
- **Standard Input (Stdin) & Pipeline Execution**: Added support for reading and running code directly from pipes or stdin using `-` (e.g. `echo "$(git status)" | spy transpile -` and `echo "..." | spy run -`).

### Fixed
- **Source Map Shift on Import Injection**: Fixed line number off-by-one errors in transpiled files by adjusting `Transformer.source_map` whenever `import os` is automatically injected for `$VARNAME` syntax.
- **Module Docstring Preservation**: Fixed `_inject_os_import` to insert after any module docstrings, preserving `__doc__` integrity.
- **Python 3.11+ Traceback Diagnostics**: Updated `_make_spy_excepthook` to map both `frame.lineno` and `frame.end_lineno` to the original `.spy` source lines for precise caret error diagnostics.
- **REPL Shell Light Multiline Continuation**: Fixed false-positive shell execution on unclosed Python expressions (e.g. `x = (1 +`), correctly presenting the continuation prompt (`...`).
- **REPL Shell Light Command Assignment**: Improved disambiguation of single-word commands (`res = pwd`, `files = ls`) and subtraction-syntax flags (`res = ls -la`) in the interactive REPL.

---

## [v0.3.1] - 2026-10-06

### Fixed
- **REPL Syntax Highlighting Dependencies**: Added `pygments` as a core dependency in `pyproject.toml` to guarantee live syntax highlighting works in standalone pip / pipx / uv tool installations.

---

## [v0.3.0] - 2026-10-06

### Added
- **Interactive REPL Enhancements**:
  - Live ANSI and prompt-toolkit syntax highlighting for `.spy` and Python syntax.
  - Tab completion for Python builtins, keywords, imported symbols, object attributes, and filesystem paths.
  - **Shell Light Mode**: Auto-detection and direct execution of bare shell commands, in-process directory navigation (`cd`, `pwd`), and direct command assignment (`output = git status`).

---

## [v0.2.0] - 2026-10-05

### Added
- **Environment Variable Syntax (`$VARNAME`)**:
  - Direct reading of environment variables via `$VARNAME` in Python expressions and `$(...)` command blocks.
  - In-place mutation and assignment (`$NODE_ENV = "production"` transpiles to `os.environ["NODE_ENV"] = "production"`).
  - Automatic `import os` injection when `$VARNAME` syntax is detected.

---

## [v0.1.1] - 2026-10-01

### Changed
- **Package Distribution Name**: Renamed PyPI package distribution to `pycli-dsl` to avoid naming conflicts on PyPI, keeping the CLI binary names as `spy` and `pycli`.
- Updated PyPI badges, installation guides, and documentation.

---

## [v0.1.0] - 2026-10-01

### Added
- Initial release of the `pycli` DevOps DSL transpiler and runner.
- First-class shell command execution syntax via `$(...)` in statement and expression forms.
- String interpolation (`{var}`) and list expansion / splat (`{*files}`).
- Execution modifiers: strict mode (`!`), safe probing (`?`), and background execution (`&`).
- Native shell pipelines (`|`), redirections (`>`, `>>`, `<`), and nested subcommands.
- `CommandResult` object featuring `.stdout`, `.stderr`, `.exit_code`, `.json`, `.lines`, `.text`, `.tee`, and `.input()`.
- Context managers: `cd(...)` for temporary working directory scoping and `env(...)` for scoped environment variables.
- PEP 302/451 import hook (`pycli.importer`) for loading `.spy` scripts directly into standard Python projects.
