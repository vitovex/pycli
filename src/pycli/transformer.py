"""Transformer that converts pycli (.spy) source code into standard Python (.py)."""

from __future__ import annotations

import ast
import re
from typing import Set

def is_valid_interpolation_expr(expr: str) -> bool:
    try:
        p = ast.parse(expr.strip(), mode="eval")
        return not isinstance(p.body, ast.Dict)
    except Exception:
        return False


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
                # Check if preceded by 'await '
                prev_text = output_chunks[-1] if output_chunks else ""
                is_await = bool(re.search(r"\bawait\s+$", prev_text))

                # Check if followed by chaining (.tee, .input(...))
                is_tee = False
                input_expr = None
                if i + 1 < len(tokens) and tokens[i + 1].type == TokenType.PYTHON_CODE:
                    val = tokens[i + 1].value
                    m_tee = re.match(r"^\.tee\b", val)
                    if m_tee:
                        is_tee = True
                        tokens[i + 1].value = val[m_tee.end() :]
                        val = tokens[i + 1].value

                    m_input = re.match(r"^\.input\s*\(", val)
                    if m_input:
                        p_start = m_input.end()
                        p_depth = 1
                        idx = p_start
                        while idx < len(val) and p_depth > 0:
                            if val[idx] == "(":
                                p_depth += 1
                            elif val[idx] == ")":
                                p_depth -= 1
                            idx += 1
                        if p_depth == 0:
                            input_expr = val[p_start : idx - 1].strip()
                            tokens[i + 1].value = val[idx :]

                is_statement = self._is_statement_context(tokens, i) and not is_tee and not input_expr
                parser = CommandParser(
                    token.value,
                    strict=token.strict,
                    safe=token.safe,
                    background=token.background,
                )
                ast_node = parser.parse()
                py_code = self._transform_command_expr(
                    ast_node,
                    is_statement=is_statement,
                    is_async=is_await,
                    is_tee=is_tee,
                    input_expr=input_expr,
                )
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
        if stripped_after == "" or stripped_after.startswith("#") or stripped_after.startswith(";"):
            return True

        return False

    def _transform_command_expr(
        self,
        node: CommandExpressionNode,
        is_statement: bool = False,
        is_async: bool = False,
        is_tee: bool = False,
        input_expr: str | None = None,
    ) -> str:
        if node.background:
            self.used_symbols.add("run_bg")
            raw = node.raw
            cmd_arg = self._format_fstring(raw) if node.pipeline.has_interpolation() else self._format_literal_string(raw)
            return f"run_bg({cmd_arg})"
        elif is_async:
            self.used_symbols.add("async_run")
            raw = node.raw
            cmd_arg = self._format_fstring(raw) if node.pipeline.has_interpolation() else self._format_literal_string(raw)
            kwargs: list[str] = []
            if is_statement and not is_tee:
                kwargs.append("capture=False")
            if is_tee:
                kwargs.append("tee=True")
            if input_expr is not None:
                kwargs.append(f"input={input_expr}")
            if node.strict:
                kwargs.append("check=True")
            if node.safe:
                kwargs.append("suppress_errors=True")
            kw_str = f", {', '.join(kwargs)}" if kwargs else ""
            return f"async_run({cmd_arg}{kw_str})"
        elif node.pipeline.has_splat():
            self.used_symbols.add("run_expanded")
            return self._transform_expanded_command(
                node,
                is_statement=is_statement,
                is_tee=is_tee,
                input_expr=input_expr,
            )
        else:
            self.used_symbols.add("run")
            return self._transform_simple_command(
                node,
                is_statement=is_statement,
                is_tee=is_tee,
                input_expr=input_expr,
            )

    def _transform_simple_command(
        self,
        node: CommandExpressionNode,
        is_statement: bool = False,
        is_tee: bool = False,
        input_expr: str | None = None,
    ) -> str:
        raw = node.raw
        has_interpolation = node.pipeline.has_interpolation()

        if not has_interpolation:
            cmd_arg = self._format_literal_string(raw)
        else:
            cmd_arg = self._format_fstring(raw)

        kwargs: list[str] = []
        if is_statement and not is_tee:
            kwargs.append("capture=False")
        if is_tee:
            kwargs.append("tee=True")
        if input_expr is not None:
            kwargs.append(f"input={input_expr}")
        if node.strict:
            kwargs.append("check=True")
        if node.safe:
            kwargs.append("suppress_errors=True")

        if kwargs:
            return f"run({cmd_arg}, {', '.join(kwargs)})"
        return f"run({cmd_arg})"

    def _transform_expanded_command(
        self,
        node: CommandExpressionNode,
        is_statement: bool = False,
        is_tee: bool = False,
        input_expr: str | None = None,
    ) -> str:
        args: list[str] = []

        for cmd in node.pipeline.commands:
            for part in cmd.parts:
                if isinstance(part, SplatNode):
                    args.append(f"*{part.expression}")
                elif isinstance(part, InterpolationNode):
                    if part.expression:
                        args.append(f'f"{{{part.expression}}}"')
                elif isinstance(part, WordNode):
                    if "{" in part.value and "}" in part.value:
                        args.append(self._format_fstring(part.value))
                    else:
                        args.append(self._format_literal_string(part.value))
                elif isinstance(part, StringNode):
                    if part.quote == '"' and "{" in part.value and "}" in part.value:
                        args.append(self._format_fstring(part.value))
                    else:
                        args.append(self._format_literal_string(part.value))
                elif isinstance(part, RedirectionNode):
                    args.append(self._format_literal_string(f"{part.operator} {part.target}"))
                elif isinstance(part, SubcommandNode):
                    args.append(self._format_literal_string(f"$({part.pipeline})"))

        if is_statement and not is_tee:
            args.append("capture=False")
        if is_tee:
            args.append("tee=True")
        if input_expr is not None:
            args.append(f"input={input_expr}")
        if node.strict:
            args.append("check=True")
        if node.safe:
            args.append("suppress_errors=True")

        return f"run_expanded({', '.join(args)})"

    def _escape_non_interpolations(self, text: str) -> str:
        """Escape non-interpolations in f-strings: single-quoted segments, awk/json braces, and invalid expressions."""
        out: list[str] = []
        i = 0
        n = len(text)
        while i < n:
            if text[i] == "'":
                out.append("'")
                i += 1
                while i < n and text[i] != "'":
                    if text[i] == "\\":
                        out.append(text[i])
                        i += 1
                        if i < n:
                            out.append(text[i])
                            i += 1
                        continue
                    if text[i] == "{":
                        out.append("{{")
                    elif text[i] == "}":
                        out.append("}}")
                    else:
                        out.append(text[i])
                    i += 1
                if i < n:
                    out.append("'")
                    i += 1
            elif text[i] == '"':
                out.append('"')
                i += 1
                while i < n and text[i] != '"':
                    if text[i] == "\\":
                        out.append(text[i])
                        i += 1
                        if i < n:
                            out.append(text[i])
                            i += 1
                        continue
                    if text[i] == "{":
                        start = i + 1
                        depth = 1
                        j = start
                        while j < n and depth > 0 and text[j] != '"':
                            if text[j] == "{":
                                depth += 1
                            elif text[j] == "}":
                                depth -= 1
                            j += 1
                        if depth == 0:
                            inner = text[start : j - 1]
                            if is_valid_interpolation_expr(inner):
                                out.append("{" + inner + "}")
                            else:
                                out.append("{{" + inner + "}}")
                            i = j
                            continue
                        else:
                            out.append("{{")
                            i += 1
                            continue
                    elif text[i] == "}":
                        out.append("}}")
                        i += 1
                    else:
                        out.append(text[i])
                        i += 1
                if i < n:
                    out.append('"')
                    i += 1
            elif text[i] == "{":
                start = i + 1
                depth = 1
                j = start
                while j < n and depth > 0:
                    if text[j] == "{":
                        depth += 1
                    elif text[j] == "}":
                        depth -= 1
                    j += 1
                if depth == 0:
                    inner = text[start : j - 1]
                    if is_valid_interpolation_expr(inner):
                        out.append("{" + inner + "}")
                    else:
                        out.append("{{" + inner + "}}")
                    i = j
                    continue
                else:
                    out.append("{{")
                    i += 1
            elif text[i] == "}":
                out.append("}}")
                i += 1
            else:
                out.append(text[i])
                i += 1
        return "".join(out)

    def _format_literal_string(self, text: str) -> str:
        """Formats a string literal."""
        if text.endswith('"'):
            if "'''" not in text:
                return f"'''{text}'''"
            escaped = text.replace('"', '\\"')
            return f'"{escaped}"'
        elif text.endswith("'"):
            if '"""' not in text:
                return f'"""{text}"""'
            escaped = text.replace("'", "\\'")
            return f"'{escaped}'"
        elif '"' not in text:
            return f'"{text}"'
        elif "'" not in text:
            return f"'{text}'"
        elif '"""' not in text:
            return f'"""{text}"""'
        elif "'''" not in text:
            return f"'''{text}'''"
        else:
            escaped = text.replace('"', '\\"')
            return f'"{escaped}"'

    def _format_fstring(self, text: str) -> str:
        """Formats a string containing {...} expressions as a valid Python f-string literal."""
        text = self._escape_non_interpolations(text)
        if text.endswith('"'):
            if "'''" not in text:
                return f"f'''{text}'''"
            escaped = text.replace('"', '\\"')
            return f'f"{escaped}"'
        elif text.endswith("'"):
            if '"""' not in text:
                return f'f"""{text}"""'
            escaped = text.replace("'", "\\'")
            return f"f'{escaped}'"
        elif '"""' not in text:
            if '"' not in text:
                return f'f"{text}"'
            elif "'" not in text:
                return f"f'{text}'"
            else:
                return f'f"""{text}"""'
        elif "'''" not in text:
            return f"f'''{text}'''"
        else:
            escaped = text.replace('"', '\\"')
            return f'f"{escaped}"'


    def _inject_imports(self, code: str, symbols: Set[str]) -> str:
        """Injects necessary pycli.runtime imports at the proper position."""
        lines = code.splitlines(keepends=True)

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

        # Check for docstrings or shebang at top
        insert_idx = 0
        if lines and lines[0].startswith("#!"):
            insert_idx = 1

        return "".join(lines[:insert_idx]) + import_stmt + "".join(lines[insert_idx:])


def transpile(source: str, auto_import: bool = True) -> str:
    """Convenience function to transpile .spy source code to Python."""
    return Transformer(auto_import=auto_import).transform(source)
