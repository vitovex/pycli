"""ANSI syntax highlighting for Python code using Python's standard library tokenizer."""

from __future__ import annotations

import builtins
import io
import keyword
import os
import sys
import tokenize

RESET = "\033[0m"
KW = "\033[94;1m"          # Bright Blue bold (Python keywords)
DEF_NAME = "\033[93;1m"    # Yellow bold (def / class names)
BUILTIN = "\033[96m"       # Cyan (Python builtins)
STR = "\033[92m"           # Bright Green (String literals)
NUM = "\033[93m"           # Yellow (Numeric literals)
COMMENT = "\033[90;3m"     # Gray italic (Comments)
OP = "\033[97m"            # Bright White (Operators & punctuation)
PYCLI = "\033[95;1m"       # Magenta bold (pycli runtime symbols)

BUILTIN_NAMES = set(dir(builtins))
PYCLI_NAMES = {
    "run",
    "run_expanded",
    "CommandResult",
    "CommandError",
    "DynamicObj",
    "stdout",
    "stderr",
    "exit_code",
    "duration",
    "command",
    "json",
    "to_dict",
}


def enable_windows_ansi() -> None:
    """Enable virtual terminal processing on Windows consoles if needed."""
    if os.name == "nt":
        os.system("")


def highlight_python(code: str) -> str:
    """Apply ANSI syntax highlighting to Python source code.

    Uses Python's standard library `tokenize` module for lossless, dependency-free
    syntax coloring.
    """
    enable_windows_ansi()

    try:
        tokens = list(tokenize.tokenize(io.BytesIO(code.encode("utf-8")).readline))
    except Exception:
        # Fallback if code cannot be tokenized
        return code

    lines = code.splitlines(keepends=True)
    cur_line, cur_col = 1, 0
    result: list[str] = []
    prev_tok: tokenize.TokenInfo | None = None

    for t in tokens:
        if t.type in (tokenize.ENCODING, tokenize.ENDMARKER):
            continue

        # Emit any whitespace/newlines preceding this token
        while cur_line < t.start[0]:
            if cur_line - 1 < len(lines):
                result.append(lines[cur_line - 1][cur_col:])
            cur_line += 1
            cur_col = 0
        if cur_line - 1 < len(lines) and cur_col < t.start[1]:
            result.append(lines[cur_line - 1][cur_col : t.start[1]])
            cur_col = t.start[1]

        # Determine token color
        color: str | None = None
        if t.type == tokenize.NAME:
            if keyword.iskeyword(t.string):
                color = KW
            elif prev_tok and prev_tok.string in ("def", "class"):
                color = DEF_NAME
            elif t.string in PYCLI_NAMES:
                color = PYCLI
            elif t.string in BUILTIN_NAMES:
                color = BUILTIN
        elif t.type == tokenize.STRING:
            color = STR
        elif t.type == tokenize.NUMBER:
            color = NUM
        elif t.type == tokenize.COMMENT:
            color = COMMENT
        elif t.type == tokenize.OP:
            color = OP

        if color:
            result.append(f"{color}{t.string}{RESET}")
        else:
            result.append(t.string)

        cur_line, cur_col = t.end
        if t.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT):
            prev_tok = t

    # Emit any remaining trailing whitespace or content
    if cur_line - 1 < len(lines):
        result.append(lines[cur_line - 1][cur_col:])
        for rem in lines[cur_line:]:
            result.append(rem)

    return "".join(result)


def should_colorize(force_color: bool = False, no_color: bool = False) -> bool:
    """Determine whether syntax coloring should be applied to stdout."""
    if no_color or os.environ.get("NO_COLOR"):
        return False
    if force_color:
        return True
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
