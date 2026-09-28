# pycli Notepad++ Language Definition (UDL)

Syntax highlighting support for `pycli` (`.spy`) files in Notepad++.

## Features

- Highlighting for Python keywords (`def`, `class`, `if`, `for`, `import`, etc.)
- Highlighting for common DevOps CLI commands (`git`, `az`, `kubectl`, `docker`, `terraform`, etc.)
- Highlighting for command expressions `$(...)` and strict mode `!`
- Highlighting for string literals, comments (`#`), and shell operators (`|`, `>`, `>>`, `<`)

## Installation

### Method 1: Automatic (Recommended on Windows)

Copy the definition files (`pycli.xml` for light mode and `pycli_DM.xml` for dark mode) to your Notepad++ `userDefineLangs` directory:

```powershell
Copy-Item "editors/notepadplusplus/*.xml" "$env:APPDATA\Notepad++\userDefineLangs\"
```

Restart Notepad++. Any file with the `.spy` extension will now be automatically recognized and syntax-highlighted in both Light Mode and Dark Mode.

### Method 2: Manual Import via Notepad++ GUI

1. Open **Notepad++**.
2. Go to **Language** menu -> **User Defined Language** -> **Define your language...**
3. Click the **Import...** button.
4. Select `editors/notepadplusplus/pycli.xml` (or `pycli_DM.xml` for Dark Mode).
5. Restart Notepad++.
