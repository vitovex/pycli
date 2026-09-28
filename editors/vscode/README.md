# pycli VSCode & Antigravity IDE Extension

Official syntax highlighting, bracket matching, and code snippets for `pycli` (`.spy`) files in Visual Studio Code and Google Antigravity IDE.

## Features

- **Full Python Syntax**: Inherits complete Python language grammar for statements, functions, classes, decorators, and docstrings.
- **Embedded Shell Syntax**:
  - Highlights `$(...)` command blocks with custom delimiter styling.
  - Highlights subcommands `$(...)`, strict mode `!`, pipelines `|`, and redirections `>`, `>>`, `<`.
  - Distinguishes CLI command names (`git`, `az`, `kubectl`, `docker`, `terraform`, etc.) and command flags (`--subscription`, `-v`).
- **Python Interpolation Highlighting**:
  - Highlights `{expression}` and splat expansions `{*files}` inside command expressions with Python grammar.
- **Productivity Snippets**:
  - `cmd` -> `$()`
  - `cmdvar` -> `res = $()`
  - `cmdjson` -> `vms = $().json`
  - `cmdstrict` -> `$()!`
  - `cmdsplat` -> `$(rm {*files})`
  - `cmdif` -> `if $(...):`

## Installation

### Method 1: Instant Local Install (No build required)

Copy this `editors/vscode` directory to the extensions folder of your editor:

#### Visual Studio Code:
- **Windows (PowerShell)**:
  ```powershell
  Copy-Item -Recurse -Force "editors/vscode" "$env:USERPROFILE\.vscode\extensions\pycli-vscode"
  ```
- **Linux / macOS (Bash)**:
  ```bash
  cp -r editors/vscode ~/.vscode/extensions/pycli-vscode
  ```

#### Google Antigravity IDE:
Antigravity IDE is fully compatible with VS Code extensions and uses its own extensions directory:
- **Windows (PowerShell)**:
  ```powershell
  Copy-Item -Recurse -Force "editors/vscode" "$env:USERPROFILE\.antigravity-ide\extensions\pycli-vscode"
  ```
- **Linux / macOS (Bash)**:
  ```bash
  cp -r editors/vscode ~/.antigravity-ide/extensions/pycli-vscode
  ```

After copying, reload the window (`Ctrl+Shift+P` / `Cmd+Shift+P` -> `Developer: Reload Window`) or restart the editor.

---

### Method 2: Package & Install via VSIX

You can also package the extension into a standard `.vsix` file and install it in either editor:

1. Package the extension:
   ```bash
   cd editors/vscode
   npx @vscode/vsce package
   ```
2. In **VSCode** or **Antigravity IDE**:
   - Open the Command Palette (`Ctrl+Shift+P` / `Cmd+Shift+P`).
   - Run `Extensions: Install from VSIX...`.
   - Select the generated `pycli-vscode-*.vsix` file.
   - Reload or restart the editor.

