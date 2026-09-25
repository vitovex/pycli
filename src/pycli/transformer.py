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

        for token in tokens:
            if token.type == TokenType.PYTHON_CODE:
                output_chunks.append(token.value)
            elif token.type == TokenType.COMMAND_EXPR:
                parser = CommandParser(token.value, strict=token.strict)
                ast_node = parser.parse()
                py_code = self._transform_command_expr(ast_node)
                output_chunks.append(py_code)

        result_code = "".join(output_chunks)

        if self.auto_import and self.used_symbols:
            result_code = self._inject_imports(result_code, self.used_symbols)

        return result_code

    def _transform_command_expr(self, node: CommandExpressionNode) -> str:
        if node.pipeline.has_splat():
            self.used_symbols.add("run_expanded")
            return self._transform_expanded_command(node)
        else:
            self.used_symbols.add("run")
            return self._transform_simple_command(node)

    def _transform_simple_command(self, node: CommandExpressionNode) -> str:
        raw = node.raw
        has_interpolation = node.pipeline.has_interpolation()

        if not has_interpolation:
            # Literal string - prefer double quotes
            cmd_arg = self._format_literal_string(raw)
        else:
            # F-string
            cmd_arg = self._format_fstring(raw)

        if node.strict:
            return f"run({cmd_arg}, check=True)"
        return f"run({cmd_arg})"

    def _transform_expanded_command(self, node: CommandExpressionNode) -> str:
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
