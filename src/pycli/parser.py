"""Parser and AST definitions for pycli command expressions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


import ast
import re

import warnings

def is_valid_interpolation_expr(expr: str) -> bool:
    try:
        p = ast.parse(expr.strip(), mode="eval")
        return not isinstance(p.body, ast.Dict)
    except SyntaxError:
        return False
    except Exception as e:
        warnings.warn(
            f"Unexpected error validating interpolation expr {expr!r}: {e}",
            RuntimeWarning,
            stacklevel=3,
        )
        return False


class ParseError(Exception):
    """Raised when parsing fails."""

    def __init__(
        self,
        message: str,
        line: int = 1,
        column: int = 1,
        fragment: str = "",
    ) -> None:
        self.message = message
        self.line = line
        self.column = column
        self.fragment = fragment
        loc = f"Line {line}, Column {column}: " if line or column else ""
        frag = f" near {fragment!r}" if fragment else ""
        super().__init__(f"{loc}{message}{frag}")


def scan_balanced(
    text: str,
    start: int,
    open_ch: str,
    close_ch: str,
    line: int = 1,
    col: int = 1,
) -> int:
    """Scan from `start` until matching `close_ch` is found at depth 0, ignoring quotes and escapes."""
    depth = 1
    i = start
    n = len(text)
    cur_line = line
    cur_col = col

    while i < n and depth > 0:
        c = text[i]
        if c == "\n":
            cur_line += 1
            cur_col = 1
        else:
            cur_col += 1

        if c in ("'", '"'):
            q = c
            is_triple = text[i : i + 3] == q * 3
            delim = q * 3 if is_triple else q
            i += len(delim)
            while i < n:
                if text[i : i + len(delim)] == delim:
                    i += len(delim)
                    break
                if text[i] == "\\":
                    i += 2
                else:
                    if text[i] == "\n":
                        cur_line += 1
                        cur_col = 1
                    else:
                        cur_col += 1
                    i += 1
            continue

        if c == open_ch:
            depth += 1
            i += 1
        elif c == close_ch:
            depth -= 1
            i += 1
        else:
            i += 1

    if depth > 0:
        frag = text[max(0, start - 5) : min(n, start + 20)]
        raise ParseError(
            f"Unclosed '{open_ch}' expression",
            line=line,
            column=col,
            fragment=frag,
        )
    return i


# ---------------------------------------------------------
# AST Nodes
# ---------------------------------------------------------


@dataclass
class ASTNode:
    pass


class CommandPartNode(ASTNode):
    has_leading_space: bool = True



@dataclass
class WordNode(CommandPartNode):
    value: str


@dataclass
class StringNode(CommandPartNode):
    value: str
    quote: str = '"'


@dataclass
class InterpolationNode(CommandPartNode):
    expression: str


@dataclass
class EnvVarNode(CommandPartNode):
    """Represents a $VARNAME environment variable reference inside a $(...) command."""
    name: str


@dataclass
class SplatNode(CommandPartNode):
    expression: str


@dataclass
class RedirectionNode(CommandPartNode):
    operator: str  # '>', '>>', or '<'
    target: str


@dataclass
class SubcommandNode(CommandPartNode):
    pipeline: PipelineNode
    raw: str = ""
    line: int = 1
    column: int = 1


@dataclass
class CommandNode(ASTNode):
    parts: List[CommandPartNode] = field(default_factory=list)

    def has_splat(self) -> bool:
        return any(isinstance(part, SplatNode) for part in self.parts)

    def has_interpolation(self) -> bool:
        return any(
            isinstance(part, (InterpolationNode, SplatNode, EnvVarNode))
            or (isinstance(part, RedirectionNode) and "{" in part.target and "}" in part.target)
            or (isinstance(part, (WordNode, StringNode)) and "{" in part.value and "}" in part.value)
            for part in self.parts
        )


@dataclass
class PipelineNode(ASTNode):
    commands: List[CommandNode] = field(default_factory=list)

    def has_splat(self) -> bool:
        return any(cmd.has_splat() for cmd in self.commands)

    def has_interpolation(self) -> bool:
        return any(cmd.has_interpolation() for cmd in self.commands)


@dataclass
class CommandExpressionNode(ASTNode):
    pipeline: PipelineNode
    strict: bool = False
    safe: bool = False
    background: bool = False
    raw: str = ""


# ---------------------------------------------------------
# Parser
# ---------------------------------------------------------


class CommandParser:
    """Parses the content of $(...) into a CommandExpressionNode AST."""

    def __init__(
        self,
        raw: str,
        strict: bool = False,
        safe: bool = False,
        background: bool = False,
        base_line: int = 1,
        base_column: int = 1,
    ) -> None:
        self.raw = raw
        self.strict = strict
        self.safe = safe
        self.background = background
        self.base_line = base_line
        self.base_column = base_column
        self.pos = 0
        self.length = len(self.raw)

    def get_pos(self, index: int) -> tuple[int, int]:
        """Convert a character index in self.raw to (line, column) in the original .spy file."""
        prefix = self.raw[:index]
        lines = prefix.split("\n")
        if len(lines) == 1:
            return (self.base_line, self.base_column + len(prefix))
        return (self.base_line + len(lines) - 1, 1 + len(lines[-1]))

    def parse(self) -> CommandExpressionNode:
        pipeline = self._parse_pipeline()
        return CommandExpressionNode(
            pipeline=pipeline,
            strict=self.strict,
            safe=self.safe,
            background=self.background,
            raw=self.raw,
        )

    def _parse_pipeline(self) -> PipelineNode:
        # Split by '|' outside quotes, braces, and subcommands
        stage_items = self._split_pipeline_stages(self.raw)
        commands: List[CommandNode] = []
        for cmd_str, offset in stage_items:
            stripped = cmd_str.strip()
            if stripped:
                leading_ws = len(cmd_str) - len(cmd_str.lstrip())
                commands.append(self._parse_command(stripped, text_offset=offset + leading_ws))
        return PipelineNode(commands=commands)

    def _split_pipeline_stages(self, text: str) -> List[tuple[str, int]]:
        stages: List[tuple[str, int]] = []
        buf: list[str] = []
        stage_start = 0
        i = 0
        n = len(text)

        while i < n:
            ch = text[i]

            # Quotes
            if ch in ("'", '"'):
                q = ch
                is_triple = text[i : i + 3] == q * 3
                delim = q * 3 if is_triple else q
                start_q = i
                i += len(delim)
                while i < n:
                    if text[i : i + len(delim)] == delim:
                        i += len(delim)
                        break
                    if text[i] == "\\":
                        i += 2
                    else:
                        i += 1
                buf.append(text[start_q:i])
                continue

            # Braces { ... }
            if ch == "{":
                start_b = i
                cur_line, cur_col = self.get_pos(i)
                i = scan_balanced(text, i + 1, "{", "}", line=cur_line, col=cur_col)
                buf.append(text[start_b:i])
                continue

            # Subcommands $( ... )
            if ch == "$" and i + 1 < n and text[i + 1] == "(":
                start_s = i
                cur_line, cur_col = self.get_pos(i)
                i = scan_balanced(text, i + 2, "(", ")", line=cur_line, col=cur_col)
                buf.append(text[start_s:i])
                continue

            # $VARNAME inside pipeline — append as-is to current stage buffer
            if ch == "$" and i + 1 < n and text[i + 1] != "(" and (text[i + 1].isupper() or text[i + 1] == "_"):
                buf.append(ch)
                i += 1
                while i < n and (text[i].isalnum() or text[i] == "_"):
                    buf.append(text[i])
                    i += 1
                continue

            # Pipeline separator '|'
            if ch == "|":
                stage_str = "".join(buf)
                if not stage_str.strip():
                    line, col = self.get_pos(i)
                    raise ParseError("Empty pipeline stage near '|'", line=line, column=col)
                stages.append((stage_str, stage_start))
                buf.clear()
                i += 1
                stage_start = i
                continue

            buf.append(ch)
            i += 1

        stage_str = "".join(buf)
        if stages and not stage_str.strip():
            line, col = self.get_pos(i)
            raise ParseError("Empty pipeline stage at end of command", line=line, column=col)
        if stage_str.strip():
            stages.append((stage_str, stage_start))

        return stages

    def _parse_command(self, cmd_text: str, text_offset: int = 0) -> CommandNode:
        parts: List[CommandPartNode] = []
        i = 0
        n = len(cmd_text)
        had_space = False

        while i < n:
            prev_i = i

            # Skip whitespace
            if cmd_text[i].isspace():
                had_space = True
                i += 1
                continue

            # Check redirection (>>, >, <)
            if cmd_text[i : i + 2] == ">>":
                op = ">>"
                op_pos = i
                i += 2
                while i < n and cmd_text[i].isspace():
                    i += 1
                if i >= n or cmd_text[i] in ("|", ">", "<"):
                    line, col = self.get_pos(text_offset + op_pos)
                    raise ParseError(
                        f"Missing target for redirection operator '{op}'",
                        line=line,
                        column=col,
                    )
                target, i = self._read_word_or_string(cmd_text, i)
                redir_node = RedirectionNode(operator=op, target=target)
                redir_node.has_leading_space = had_space
                parts.append(redir_node)
                had_space = False
                continue
            if cmd_text[i] in (">", "<"):
                op = cmd_text[i]
                op_pos = i
                i += 1
                while i < n and cmd_text[i].isspace():
                    i += 1
                if i >= n or cmd_text[i] in ("|", ">", "<"):
                    line, col = self.get_pos(text_offset + op_pos)
                    raise ParseError(
                        f"Missing target for redirection operator '{op}'",
                        line=line,
                        column=col,
                    )
                target, i = self._read_word_or_string(cmd_text, i)
                redir_node = RedirectionNode(operator=op, target=target)
                redir_node.has_leading_space = had_space
                parts.append(redir_node)
                had_space = False
                continue

            # Check $VARNAME environment variable (uppercase or underscore, not followed by '(')
            if (
                cmd_text[i] == "$"
                and i + 1 < n
                and cmd_text[i + 1] != "("
                and (cmd_text[i + 1].isupper() or cmd_text[i + 1] == "_")
            ):
                i += 1  # skip '$'
                var_start = i
                while i < n and (cmd_text[i].isalnum() or cmd_text[i] == "_"):
                    i += 1
                var_name = cmd_text[var_start:i]
                if not var_name:
                    # bare '$' with no valid identifier — treat as word char
                    w_node = WordNode(value="$")
                    w_node.has_leading_space = had_space
                    parts.append(w_node)
                    had_space = False
                    continue
                env_node = EnvVarNode(name=var_name)
                env_node.has_leading_space = had_space
                parts.append(env_node)
                had_space = False
                continue

            # Check subcommand $(...)
            if cmd_text[i : i + 2] == "$(":
                start = i
                cur_line, cur_col = self.get_pos(text_offset + start)
                end = scan_balanced(cmd_text, i + 2, "(", ")", line=cur_line, col=cur_col)
                sub_inner = cmd_text[start + 2 : end - 1]
                sub_ast = CommandParser(
                    sub_inner,
                    base_line=cur_line,
                    base_column=cur_col + 2,
                )._parse_pipeline()
                sub_node = SubcommandNode(
                    pipeline=sub_ast,
                    raw=cmd_text[start:end],
                    line=cur_line,
                    column=cur_col,
                )
                sub_node.has_leading_space = had_space
                parts.append(sub_node)
                had_space = False
                i = end
                continue

            # Check splat {*...}
            if cmd_text[i : i + 2] == "{*":
                start = i
                cur_line, cur_col = self.get_pos(text_offset + start)
                end = scan_balanced(cmd_text, i + 2, "{", "}", line=cur_line, col=cur_col)
                expr = cmd_text[start + 2 : end - 1].strip()
                if not expr:
                    line, col = self.get_pos(text_offset + start)
                    raise ParseError("Empty splat expression '{*}'", line=line, column=col)
                splat_node = SplatNode(expression=expr)
                splat_node.has_leading_space = had_space
                parts.append(splat_node)
                had_space = False
                i = end
                continue

            # Check interpolation {...}
            if cmd_text[i] == "{":
                start = i
                cur_line, cur_col = self.get_pos(text_offset + start)
                end = scan_balanced(cmd_text, i + 1, "{", "}", line=cur_line, col=cur_col)
                expr = cmd_text[start + 1 : end - 1].strip()
                if not expr:
                    line, col = self.get_pos(text_offset + start)
                    raise ParseError("Empty interpolation expression '{}'", line=line, column=col)
                interp_node = InterpolationNode(expression=expr)
                interp_node.has_leading_space = had_space
                parts.append(interp_node)
                had_space = False
                i = end
                continue

            # Check string literal
            if cmd_text[i] in ("'", '"'):
                q = cmd_text[i]
                start_q = i
                i += 1
                while i < n:
                    if cmd_text[i] == q:
                        i += 1
                        break
                    if cmd_text[i] == "\\":
                        i += 2
                    else:
                        i += 1
                str_val = cmd_text[start_q + 1 : i - 1]
                str_node = StringNode(value=str_val, quote=q)
                str_node.has_leading_space = had_space
                parts.append(str_node)
                had_space = False
                continue

            # Word / token (preserves shell variables like $HOME, $?, $1, or bare $)
            start = i
            while (
                i < n
                and not cmd_text[i].isspace()
                and cmd_text[i] not in ("|", ">", "<", "{", "'", '"')
                and cmd_text[i : i + 2] != "$("
                and not (
                    cmd_text[i] == "$"
                    and i + 1 < n
                    and (cmd_text[i + 1].isupper() or cmd_text[i + 1] == "_")
                )
            ):
                i += 1
            word = cmd_text[start:i]
            if word:
                w_node = WordNode(value=word)
                w_node.has_leading_space = had_space
                parts.append(w_node)
                had_space = False

            # Guarantee loop progress to avoid infinite loops
            if i == prev_i:
                ch_node = WordNode(value=cmd_text[i])
                ch_node.has_leading_space = had_space
                parts.append(ch_node)
                had_space = False
                i += 1

        return CommandNode(parts=parts)

    def _read_word_or_string(self, text: str, start: int) -> tuple[str, int]:
        n = len(text)
        i = start
        if i < n and text[i] in ("'", '"'):
            q = text[i]
            buf: list[str] = [q]
            i += 1
            while i < n:
                c = text[i]
                buf.append(c)
                i += 1
                if c == q:
                    break
                if c == "\\":
                    if i < n:
                        buf.append(text[i])
                        i += 1
            return "".join(buf), i
        else:
            buf = []
            while i < n and not text[i].isspace() and text[i] not in ("|", ">", "<"):
                buf.append(text[i])
                i += 1
            return "".join(buf), i
