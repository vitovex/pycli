"""Parser and AST definitions for pycli command expressions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


class ParseError(Exception):
    """Raised when parsing fails."""


# ---------------------------------------------------------
# AST Nodes
# ---------------------------------------------------------


@dataclass
class ASTNode:
    pass


@dataclass
class CommandPartNode(ASTNode):
    pass


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
class SplatNode(CommandPartNode):
    expression: str


@dataclass
class RedirectionNode(CommandPartNode):
    operator: str  # '>', '>>', or '<'
    target: str


@dataclass
class SubcommandNode(CommandPartNode):
    pipeline: PipelineNode


@dataclass
class CommandNode(ASTNode):
    parts: List[CommandPartNode] = field(default_factory=list)

    def has_splat(self) -> bool:
        return any(isinstance(part, SplatNode) for part in self.parts)

    def has_interpolation(self) -> bool:
        return any(
            isinstance(part, (InterpolationNode, SplatNode)) for part in self.parts
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
    raw: str = ""


# ---------------------------------------------------------
# Parser
# ---------------------------------------------------------


class CommandParser:
    """Parses the content of $(...) into a CommandExpressionNode AST."""

    def __init__(self, raw: str, strict: bool = False) -> None:
        self.raw = raw.strip()
        self.strict = strict
        self.pos = 0
        self.length = len(self.raw)

    def parse(self) -> CommandExpressionNode:
        pipeline = self._parse_pipeline()
        return CommandExpressionNode(
            pipeline=pipeline,
            strict=self.strict,
            raw=self.raw,
        )

    def _parse_pipeline(self) -> PipelineNode:
        # Split by '|' outside quotes, braces, and subcommands
        command_strings = self._split_pipeline_stages(self.raw)
        commands: List[CommandNode] = []
        for cmd_str in command_strings:
            commands.append(self._parse_command(cmd_str.strip()))
        return PipelineNode(commands=commands)

    def _split_pipeline_stages(self, text: str) -> List[str]:
        stages: List[str] = []
        buf: list[str] = []
        i = 0
        n = len(text)

        while i < n:
            ch = text[i]

            # Skip quotes
            if ch in ("'", '"'):
                q = ch
                buf.append(ch)
                i += 1
                while i < n:
                    c = text[i]
                    buf.append(c)
                    if c == q:
                        i += 1
                        break
                    if c == "\\":
                        i += 1
                        if i < n:
                            buf.append(text[i])
                            i += 1
                    else:
                        i += 1
                continue

            # Skip braces { ... }
            if ch == "{":
                brace_depth = 1
                buf.append(ch)
                i += 1
                while i < n and brace_depth > 0:
                    bc = text[i]
                    buf.append(bc)
                    if bc == "{":
                        brace_depth += 1
                    elif bc == "}":
                        brace_depth -= 1
                    i += 1
                continue

            # Skip subcommands $( ... )
            if ch == "$" and i + 1 < n and text[i + 1] == "(":
                buf.append(ch)
                buf.append(text[i + 1])
                i += 2
                depth = 1
                while i < n and depth > 0:
                    sc = text[i]
                    buf.append(sc)
                    if sc == "(":
                        depth += 1
                    elif sc == ")":
                        depth -= 1
                    i += 1
                continue

            # Check for pipeline separator '|'
            if ch == "|":
                stages.append("".join(buf))
                buf.clear()
                i += 1
                continue

            buf.append(ch)
            i += 1

        if buf:
            stages.append("".join(buf))

        return stages

    def _parse_command(self, cmd_text: str) -> CommandNode:
        parts: List[CommandPartNode] = []
        i = 0
        n = len(cmd_text)

        while i < n:
            # Skip whitespace
            if cmd_text[i].isspace():
                i += 1
                continue

            # Check redirection (>>, >, <)
            if cmd_text[i : i + 2] == ">>":
                i += 2
                while i < n and cmd_text[i].isspace():
                    i += 1
                target, i = self._read_word_or_string(cmd_text, i)
                parts.append(RedirectionNode(operator=">>", target=target))
                continue
            if cmd_text[i] in (">", "<"):
                op = cmd_text[i]
                i += 1
                while i < n and cmd_text[i].isspace():
                    i += 1
                target, i = self._read_word_or_string(cmd_text, i)
                parts.append(RedirectionNode(operator=op, target=target))
                continue

            # Check subcommand $(...)
            if cmd_text[i : i + 2] == "$(":
                start = i
                i += 2
                depth = 1
                while i < n and depth > 0:
                    if cmd_text[i] == "(":
                        depth += 1
                    elif cmd_text[i] == ")":
                        depth -= 1
                    i += 1
                sub_inner = cmd_text[start + 2 : i - 1]
                sub_ast = CommandParser(sub_inner)._parse_pipeline()
                parts.append(SubcommandNode(pipeline=sub_ast))
                continue

            # Check splat {*...}
            if cmd_text[i : i + 2] == "{*":
                start = i + 2
                i += 2
                brace_depth = 1
                while i < n and brace_depth > 0:
                    if cmd_text[i] == "{":
                        brace_depth += 1
                    elif cmd_text[i] == "}":
                        brace_depth -= 1
                    i += 1
                expr = cmd_text[start : i - 1].strip()
                parts.append(SplatNode(expression=expr))
                continue

            # Check interpolation {...}
            if cmd_text[i] == "{":
                start = i + 1
                i += 1
                brace_depth = 1
                while i < n and brace_depth > 0:
                    if cmd_text[i] == "{":
                        brace_depth += 1
                    elif cmd_text[i] == "}":
                        brace_depth -= 1
                    i += 1
                expr = cmd_text[start : i - 1].strip()
                parts.append(InterpolationNode(expression=expr))
                continue

            # Check string literal
            if cmd_text[i] in ("'", '"'):
                q = cmd_text[i]
                i += 1
                buf: list[str] = []
                while i < n:
                    c = cmd_text[i]
                    if c == q:
                        i += 1
                        break
                    if c == "\\":
                        i += 1
                        if i < n:
                            buf.append(cmd_text[i])
                            i += 1
                    else:
                        buf.append(c)
                        i += 1
                parts.append(StringNode(value="".join(buf), quote=q))
                continue

            # Word / token
            start = i
            while i < n and not cmd_text[i].isspace() and cmd_text[i] not in ("|", ">", "<", "{", "$"):
                i += 1
            word = cmd_text[start:i]
            if word:
                parts.append(WordNode(value=word))

        return CommandNode(parts=parts)

    def _read_word_or_string(self, text: str, start: int) -> tuple[str, int]:
        n = len(text)
        i = start
        if i < n and text[i] in ("'", '"'):
            q = text[i]
            i += 1
            buf: list[str] = []
            while i < n:
                c = text[i]
                if c == q:
                    i += 1
                    break
                buf.append(c)
                i += 1
            return "".join(buf), i
        else:
            buf = []
            while i < n and not text[i].isspace() and text[i] not in ("|", ">", "<"):
                buf.append(text[i])
                i += 1
            return "".join(buf), i
