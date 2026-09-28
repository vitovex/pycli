"""Transformer that converts pycli (.spy) source code into standard Python (.py)."""

from __future__ import annotations

import re
from typing import Set

from pycli.lexer import Lexer, Token, TokenType
from pycli.parser import (
    CommandExpressionNode,
    CommandParser,
    InterpolationNode,
    RedirectionNode,
    SplatNode,
    StringNode,
    SubcommandNode,
    WordNode,
)


class Transformer:
    """Transforms .spy source code containing $(...) syntax into pure Python source code."""

    def __init__(self, auto_import: bool = True) -> None:
        self.auto_import = auto_import
        self.used_symbols: Set[str] = set()

    def transform(self, source: str) -> str:
        self.used_symbols.clear()
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        output_chunks: list[str] = []

        for i, token in enumerate(tokens):
            if token.type == TokenType.PYTHON_CODE:
                output_chunks.append(token.value)
            elif token.type == TokenType.COMMAND_EXPR:
                is_statement = self._is_statement_context(tokens, i)
                parser = CommandParser(token.value, strict=token.strict)
                ast_node = parser.parse()
                py_code = self._transform_command_expr(ast_node, is_statement=is_statement)
                output_chunks.append(py_code)

        result_code = "".join(output_chunks)

        if self.auto_import and self.used_symbols:
            result_code = self._inject_imports(result_code, self.used_symbols)

        return result_code

    def _is_statement_context(self, tokens: list[Token], index: int) -> bool:
        """Determines if the command expression at tokens[index] is a standalone statement."""
        # 1. Inspect code preceding the command on the current line
        prev_code = tokens[index - 1].value if index > 0 else ""
        last_nl = prev_code.rfind("\n")
        line_before = prev_code[last_nl + 1 :] if last_nl != -1 else prev_code

        # Check for unclosed brackets/parentheses in line_before
        bracket_stack: list[str] = []
        in_quote: str | None = None
        for ch in line_before:
            if in_quote:
                if ch == in_quote:
                    in_quote = None
            elif ch in ("'", '"'):
                in_quote = ch
            elif ch in "([{":
                bracket_stack.append(ch)
            elif ch in ")]}":
                if bracket_stack:
                    bracket_stack.pop()

        if bracket_stack:
            return False

        stripped_before = line_before.strip()
        # Statement must start after indentation, after a semicolon, or after a block colon (e.g. if cond: $(cmd))
        if not (
            stripped_before == ""
            or stripped_before.endswith(";")
            or (
                stripped_before.endswith(":")
                and any(
                    stripped_before.startswith(kw)
                    for kw in (
                        "if ",
                        "elif ",
                        "else:",
                        "try:",
                        "finally:",
                        "except",
                        "for ",
                        "while ",
                        "with ",
                        "def ",
                    )
                )
            )
        ):
            return False

        # 2. Inspect code following the command on the current line
        next_code = tokens[index + 1].value if index + 1 < len(tokens) else ""
        first_nl = next_code.find("\n")
        line_after = next_code[:first_nl] if first_nl != -1 else next_code
        stripped_after = line_after.strip()

        # Statement cannot be followed by operators, member access (.json), commas, brackets, etc.
        # It can only be empty, or followed by a comment (# ...) or semicolon (; ...)
        if stripped_after == "" or stripped_after.startswith("#") or stripped_after.startswith(";"):
            return True

        return False

    def _transform_command_expr(self, node: CommandExpressionNode, is_statement: bool = False) -> str:
        if node.pipeline.has_splat():
            self.used_symbols.add("run_expanded")
            return self._transform_expanded_command(node, is_statement=is_statement)
        else:
            self.used_symbols.add("run")
            return self._transform_simple_command(node, is_statement=is_statement)

    def _transform_simple_command(self, node: CommandExpressionNode, is_statement: bool = False) -> str:
        raw = node.raw
        has_interpolation = node.pipeline.has_interpolation()

        if not has_interpolation:
            # Literal string - prefer double quotes
            cmd_arg = self._format_literal_string(raw)
        else:
            # F-string
            cmd_arg = self._format_fstring(raw)

        kwargs: list[str] = []
        if is_statement:
            kwargs.append("capture=False")
        if node.strict:
            kwargs.append("check=True")

        if kwargs:
            return f"run({cmd_arg}, {', '.join(kwargs)})"
        return f"run({cmd_arg})"

    def _transform_expanded_command(self, node: CommandExpressionNode, is_statement: bool = False) -> str:
        args: list[str] = []

        for cmd in node.pipeline.commands:
            for part in cmd.parts:
                if isinstance(part, SplatNode):
                    args.append(f"*{part.expression}")
                elif isinstance(part, InterpolationNode):
                    args.append(f'f"{{{part.expression}}}"')
                elif isinstance(part, WordNode):
                    # Check if the word itself has embedded interpolation e.g. --key={val}
                    if "{" in part.value and "}" in part.value:
                        args.append(self._format_fstring(part.value))
                    else:
                        args.append(self._format_literal_string(part.value))
                elif isinstance(part, StringNode):
                    args.append(self._format_literal_string(part.value))
                elif isinstance(part, RedirectionNode):
                    args.append(self._format_literal_string(f"{part.operator} {part.target}"))
                elif isinstance(part, SubcommandNode):
                    args.append(self._format_literal_string(f"$({part.pipeline})"))

        if is_statement:
            args.append("capture=False")
        if node.strict:
            args.append("check=True")

        return f"run_expanded({', '.join(args)})"

    def _format_literal_string(self, text: str) -> str:
        """Formats a string literal preferring double quotes."""
        if '"' not in text:
            return f'"{text}"'
        elif "'" not in text:
            return f"'{text}'"
        else:
            escaped = text.replace('"', '\\"')
            return f'"{escaped}"'

    def _format_fstring(self, text: str) -> str:
        """Formats a string containing {...} expressions as a valid Python f-string literal."""
        if '"""' not in text:
            # If text has no triple-quotes, check simple quoting
            if '"' not in text:
                return f'f"{text}"'
            elif "'" not in text:
                return f"f'{text}'"
            else:
                return f'f"""{text}"""'
        else:
            # Fallback escape quotes
            escaped = text.replace('"', '\\"')
            return f'f"{escaped}"'

    def _inject_imports(self, code: str, symbols: Set[str]) -> str:
        """Injects necessary pycli.runtime imports at the proper position."""
        lines = code.splitlines(keepends=True)

        # Check for existing 'from pycli.runtime import ...' line
        runtime_import_regex = re.compile(r"^from\s+pycli\.runtime\s+import\s+(.+)$")
        for idx, line in enumerate(lines):
            match = runtime_import_regex.match(line.strip())
            if match:
                raw_imports = match.group(1).strip()
                if raw_imports == "*":
                    return code
                existing = {item.strip() for item in raw_imports.split(",") if item.strip()}
                missing = sorted(symbols - existing)
                if not missing:
                    return code
                all_symbols = sorted(existing.union(missing))
                newline = "\n" if line.endswith("\n") else ""
                lines[idx] = f"from pycli.runtime import {', '.join(all_symbols)}{newline}"
                return "".join(lines)

        sorted_symbols = sorted(symbols)
        import_stmt = f"from pycli.runtime import {', '.join(sorted_symbols)}\n"

        # Find location after shebang, docstrings, or __future__ imports
        insert_idx = 0

        # Skip shebang
        if lines and lines[0].startswith("#!"):
            insert_idx = 1

        # Skip encoding or future imports or module docstring
        in_docstring = False
        docstring_delim = None

        i = insert_idx
        while i < len(lines):
            line = lines[i].strip()

            if not in_docstring:
                if line.startswith('"""') or line.startswith("'''"):
                    delim = line[:3]
                    if line.count(delim) >= 2 and len(line) > 3:
                        # Single-line docstring
                        i += 1
                        insert_idx = i
                        continue
                    in_docstring = True
                    docstring_delim = delim
                    i += 1
                    continue
                elif line.startswith("#"):
                    i += 1
                    continue
                elif line.startswith("from __future__ import"):
                    i += 1
                    insert_idx = i
                    continue
                elif not line:
                    i += 1
                    continue
                else:
                    insert_idx = i
                    break
            else:
                if docstring_delim and docstring_delim in line:
                    in_docstring = False
                    docstring_delim = None
                    i += 1
                    insert_idx = i
                    continue
                i += 1

        lines.insert(insert_idx, import_stmt)
        return "".join(lines)


def transpile(source: str, auto_import: bool = True) -> str:
    """Convenience function to transpile .spy source to Python."""
    return Transformer(auto_import=auto_import).transform(source)
