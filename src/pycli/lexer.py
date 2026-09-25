"""Lexer / Scanner for pycli (.spy) source code."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import List


class TokenType(Enum):
    PYTHON_CODE = auto()
    COMMAND_EXPR = auto()


@dataclass
class Token:
    type: TokenType
    value: str
    line: int
    column: int
    strict: bool = False  # True if followed by '!'


class LexerError(Exception):
    """Raised when lexical scanning fails."""

    def __init__(self, message: str, line: int, column: int) -> None:
        self.line = line
        self.column = column
        super().__init__(f"Line {line}, Column {column}: {message}")


class Lexer:
    """Scans .spy source code into alternating Python code and CommandExpression tokens."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.pos = 0
        self.line = 1
        self.col = 1
        self.length = len(source)

    def _peek(self, offset: int = 0) -> str:
        idx = self.pos + offset
        if idx < self.length:
            return self.source[idx]
        return ""

    def _advance(self) -> str:
        ch = self.source[self.pos]
        self.pos += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def tokenize(self) -> List[Token]:
        tokens: List[Token] = []
        py_buf: list[str] = []
        py_start_line = self.line
        py_start_col = self.col

        def flush_py():
            if py_buf:
                tokens.append(
                    Token(
                        type=TokenType.PYTHON_CODE,
                        value="".join(py_buf),
                        line=py_start_line,
                        column=py_start_col,
                    )
                )
                py_buf.clear()

        while self.pos < self.length:
            ch = self._peek()

            # 1. Check for comments
            if ch == "#":
                py_buf.append(self._advance())
                while self.pos < self.length and self._peek() != "\n":
                    py_buf.append(self._advance())
                continue

            # 2. Check for string literals in Python
            if ch in ("'", '"'):
                # Check for triple-quote
                quote_char = ch
                is_triple = self.source[self.pos : self.pos + 3] == quote_char * 3
                if is_triple:
                    py_buf.append(self._advance())
                    py_buf.append(self._advance())
                    py_buf.append(self._advance())
                    delim = quote_char * 3
                    while self.pos < self.length:
                        if self.source[self.pos : self.pos + 3] == delim:
                            py_buf.append(self._advance())
                            py_buf.append(self._advance())
                            py_buf.append(self._advance())
                            break
                        if self._peek() == "\\":
                            py_buf.append(self._advance())
                            if self.pos < self.length:
                                py_buf.append(self._advance())
                        else:
                            py_buf.append(self._advance())
                else:
                    # Single quoted string
                    py_buf.append(self._advance())
                    while self.pos < self.length:
                        c = self._peek()
                        if c == quote_char:
                            py_buf.append(self._advance())
                            break
                        if c == "\\":
                            py_buf.append(self._advance())
                            if self.pos < self.length:
                                py_buf.append(self._advance())
                        elif c == "\n":
                            # Unterminated string on this line in Python
                            py_buf.append(self._advance())
                            break
                        else:
                            py_buf.append(self._advance())
                continue

            # 3. Check for $( command expression
            if ch == "$" and self._peek(1) == "(":
                flush_py()
                cmd_token = self._scan_command_expr()
                tokens.append(cmd_token)
                py_start_line = self.line
                py_start_col = self.col
                continue

            # Regular Python character
            py_buf.append(self._advance())

        flush_py()
        return tokens

    def _scan_command_expr(self) -> Token:
        cmd_line = self.line
        cmd_col = self.col

        # Consume '$('
        self._advance()  # $
        self._advance()  # (

        content_buf: list[str] = []
        depth = 1  # Parentheses depth inside $( ... )

        while self.pos < self.length and depth > 0:
            c = self._peek()

            # Handle quotes inside command
            if c in ("'", '"'):
                quote = c
                content_buf.append(self._advance())
                while self.pos < self.length:
                    qc = self._peek()
                    if qc == quote:
                        content_buf.append(self._advance())
                        break
                    if qc == "\\":
                        content_buf.append(self._advance())
                        if self.pos < self.length:
                            content_buf.append(self._advance())
                    else:
                        content_buf.append(self._advance())
                continue

            # Handle braces { ... } (interpolation / splat)
            if c == "{":
                brace_depth = 1
                content_buf.append(self._advance())
                while self.pos < self.length and brace_depth > 0:
                    bc = self._peek()
                    if bc in ("'", '"'):
                        b_quote = bc
                        content_buf.append(self._advance())
                        while self.pos < self.length:
                            b_qc = self._peek()
                            if b_qc == b_quote:
                                content_buf.append(self._advance())
                                break
                            if b_qc == "\\":
                                content_buf.append(self._advance())
                                if self.pos < self.length:
                                    content_buf.append(self._advance())
                            else:
                                content_buf.append(self._advance())
                        continue

                    if bc == "{":
                        brace_depth += 1
                        content_buf.append(self._advance())
                    elif bc == "}":
                        brace_depth -= 1
                        content_buf.append(self._advance())
                    else:
                        content_buf.append(self._advance())
                continue

            # Nested $(...) subcommands or regular ( ... )
            if c == "(":
                depth += 1
                content_buf.append(self._advance())
            elif c == ")":
                depth -= 1
                if depth == 0:
                    # Matched outer closing ')'
                    self._advance()
                    break
                else:
                    content_buf.append(self._advance())
            else:
                content_buf.append(self._advance())

        if depth != 0:
            raise LexerError("Unclosed command expression '$('", cmd_line, cmd_col)

        # Check for strict mode '!'
        strict = False
        if self.pos < self.length and self._peek() == "!":
            self._advance()
            strict = True

        raw_cmd = "".join(content_buf)
        return Token(
            type=TokenType.COMMAND_EXPR,
            value=raw_cmd,
            line=cmd_line,
            column=cmd_col,
            strict=strict,
        )
