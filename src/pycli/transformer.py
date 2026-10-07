"""Transformer that converts pycli (.spy) source code into standard Python (.py)."""

from __future__ import annotations

import ast
import re
import sys
from typing import Set

from pycli.lexer import Lexer, Token, TokenType
from pycli.parser import (
    CommandExpressionNode,
    CommandParser,
    EnvVarNode,
    InterpolationNode,
    RedirectionNode,
    SplatNode,
    StringNode,
    SubcommandNode,
    WordNode,
    is_valid_interpolation_expr,
    scan_balanced,
)


class TranspilerError(Exception):
    """Raised when transpilation produces invalid Python code or unsupportable constructs."""

    def __init__(self, message: str, line: int | None = None, column: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column

    def __str__(self) -> str:
        if self.line is not None:
            col_part = f", column {self.column}" if self.column is not None else ""
            return f"Line {self.line}{col_part}: {self.message}"
        return self.message


class Transformer:
    """Transforms .spy source code containing $(...) syntax into pure Python source code."""

    def __init__(
        self,
        auto_import: bool = True,
        unsafe_interpolation: bool = False,
        target_platform: str | None = None,
    ) -> None:
        self.auto_import = auto_import
        self.unsafe_interpolation = unsafe_interpolation
        self.target_platform = target_platform or sys.platform
        self.used_symbols: Set[str] = set()
        self.source_map: dict[int, int] = {}
        self._needs_os_import: bool = False

    def transform(self, source: str, validate: bool = False) -> str:
        self.used_symbols.clear()
        self.source_map.clear()
        self._needs_os_import = False

        lexer = Lexer(source)
        tokens = lexer.tokenize()

        output_chunks: list[str] = []
        token_values: dict[int, str] = {}
        recorded_chunks: list[tuple[str, int, bool]] = []

        for i, token in enumerate(tokens):
            if token.type == TokenType.PYTHON_CODE:
                val = token_values.get(i, token.value)
                output_chunks.append(val)
                recorded_chunks.append((val, token.line, False))
            elif token.type == TokenType.ENV_VAR:
                py_code = f'os.environ["{token.value}"]'
                self._needs_os_import = True
                output_chunks.append(py_code)
                recorded_chunks.append((py_code, token.line, False))
            elif token.type == TokenType.COMMAND_EXPR:
                # Check if preceded by 'await '
                prev_text = output_chunks[-1] if output_chunks else ""
                is_await = bool(re.search(r"\bawait\s+$", prev_text))

                # Check if followed by chaining (.tee, .input(...)) without mutating tokens
                is_tee = False
                input_expr = None
                if i + 1 < len(tokens) and tokens[i + 1].type == TokenType.PYTHON_CODE:
                    val = token_values.get(i + 1, tokens[i + 1].value)
                    while True:
                        if val.startswith(".tee") and (len(val) == 4 or not (val[4].isalnum() or val[4] == "_")):
                            is_tee = True
                            val = val[4:]
                            continue
                        m_input = re.match(r"^\.input\s*\(", val)
                        if m_input:
                            p_start = m_input.end()
                            try:
                                end_paren = scan_balanced(val, p_start, "(", ")")
                                input_expr = val[p_start : end_paren - 1].strip()
                                val = val[end_paren:]
                                continue
                            except Exception:
                                pass
                        break
                    token_values[i + 1] = val

                is_statement = (
                    self._is_statement_context(tokens, i, token_values)
                    and not is_tee
                    and not input_expr
                )
                parser = CommandParser(
                    token.value,
                    strict=token.strict,
                    safe=token.safe,
                    background=token.background,
                    base_line=token.line,
                    base_column=token.column + 2,
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
                recorded_chunks.append((py_code, token.line, True))

        # Build initial source map from character-level mapping to exact splitlines
        char_spy_lines: list[int] = []
        for chunk, orig_start_line, is_cmd in recorded_chunks:
            if is_cmd:
                char_spy_lines.extend([orig_start_line] * len(chunk))
            else:
                cur_spy_line = orig_start_line
                for ch in chunk:
                    char_spy_lines.append(cur_spy_line)
                    if ch == "\n":
                        cur_spy_line += 1

        result_code = "".join(output_chunks)

        char_idx = 0
        for py_ln, line in enumerate(result_code.splitlines(keepends=True), start=1):
            if char_idx < len(char_spy_lines):
                self.source_map[py_ln] = char_spy_lines[char_idx]
            else:
                self.source_map[py_ln] = 1
            char_idx += len(line)

        if not self.source_map:
            self.source_map[1] = 1

        if self.auto_import and self.used_symbols:
            orig_line_count = len(result_code.splitlines(keepends=True))
            result_code, lines_added, insert_idx = self._inject_imports(result_code, self.used_symbols)
            if lines_added > 0:
                shifted_map: dict[int, int] = {}
                for py_ln, spy_ln in self.source_map.items():
                    if py_ln <= insert_idx:
                        shifted_map[py_ln] = spy_ln
                    else:
                        shifted_map[py_ln + lines_added] = spy_ln
                for offset in range(lines_added):
                    shifted_map[insert_idx + 1 + offset] = 1
                self.source_map = shifted_map

        if self._needs_os_import and self.auto_import:
            result_code, lines_added, insert_idx = self._inject_os_import(result_code)
            if lines_added > 0:
                shifted_map: dict[int, int] = {}
                for py_ln, spy_ln in self.source_map.items():
                    if py_ln <= insert_idx:
                        shifted_map[py_ln] = spy_ln
                    else:
                        shifted_map[py_ln + lines_added] = spy_ln
                for offset in range(lines_added):
                    shifted_map[insert_idx + 1 + offset] = 1
                self.source_map = shifted_map

        if validate:
            try:
                ast.parse(result_code)
            except SyntaxError as e:
                raise TranspilerError(f"Transpiler produced invalid Python: {e}") from e

        return result_code

    def _is_statement_context(
        self, tokens: list[Token], index: int, token_values: dict[int, str]
    ) -> bool:
        """Determines if the command expression at tokens[index] is a standalone statement."""
        # 1. Inspect code preceding the command on the current line
        prev_code = token_values.get(index - 1, tokens[index - 1].value) if index > 0 else ""
        last_nl = prev_code.rfind("\n")
        line_before = prev_code[last_nl + 1 :] if last_nl != -1 else prev_code

        # Check for unclosed brackets/parentheses across all preceding code
        bracket_stack: list[str] = []
        in_quote: str | None = None
        idx_p = 0
        n_prev = len(prev_code)
        while idx_p < n_prev:
            ch = prev_code[idx_p]
            if in_quote:
                if ch == "\\":
                    idx_p += 2
                    continue
                if prev_code[idx_p : idx_p + len(in_quote)] == in_quote:
                    idx_p += len(in_quote)
                    in_quote = None
                    continue
            else:
                if ch == "#":
                    while idx_p < n_prev and prev_code[idx_p] != "\n":
                        idx_p += 1
                    continue
                if ch in ("'", '"'):
                    is_triple = prev_code[idx_p : idx_p + 3] == ch * 3
                    in_quote = ch * 3 if is_triple else ch
                    idx_p += len(in_quote)
                    continue
                if ch in "([{":
                    bracket_stack.append(ch)
                elif ch in ")]}":
                    if bracket_stack:
                        bracket_stack.pop()
            idx_p += 1

        if bracket_stack:
            return False

        stripped_before = line_before.strip()

        # Expression patterns preceding the command
        expression_before_patterns = [
            r"\blambda\b[^:]*:\s*$",
            r"\bif\b.*\belse\b\s*$",
            r"\belse\s*$",
            r"\[.*for\b",
            r"\bfor\b.*\bin\b\s*$",
            r"\breturn\b\s*$",
            r"\byield\b\s*$",
            r"\bassert\b\s*$",
        ]
        for pat in expression_before_patterns:
            if re.search(pat, stripped_before):
                return False

        # Statement must start after indentation, after a semicolon, after 'await', or after a block colon (e.g. if cond: $(cmd))
        is_valid_before = (
            stripped_before in ("", "await")
            or stripped_before.endswith(";")
            or stripped_before.endswith("; await")
            or stripped_before.endswith(": await")
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
        )
        if not is_valid_before:
            return False

        # 2. Inspect code following the command on the current line
        next_code = token_values.get(index + 1, tokens[index + 1].value) if index + 1 < len(tokens) else ""
        first_nl = next_code.find("\n")
        line_after = next_code[:first_nl] if first_nl != -1 else next_code
        stripped_after = line_after.strip()

        # Expression patterns following the command
        expression_after_patterns = [
            r"^\s*\bif\b",
            r"^\s*\bfor\b",
            r"^\s*(\+|-|\*|/|%|==|!=|<=|>=|<|>|\band\b|\bor\b|\bin\b|\bis\b)",
            r"^\s*(\]|\)|\})",
        ]
        for pat in expression_after_patterns:
            if re.search(pat, stripped_after):
                return False

        # Statement cannot be followed by operators, member access (.json), commas, brackets, etc.
        if stripped_after == "" or stripped_after.startswith("#") or stripped_after.startswith(";"):
            return True

        return False

    def _find_subcommand(self, node: CommandExpressionNode) -> SubcommandNode | None:
        for cmd in node.pipeline.commands:
            for part in cmd.parts:
                if isinstance(part, SubcommandNode):
                    return part
        return None

    def _transform_command_expr(
        self,
        node: CommandExpressionNode,
        is_statement: bool = False,
        is_async: bool = False,
        is_tee: bool = False,
        input_expr: str | None = None,
    ) -> str:
        subcmd = self._find_subcommand(node)
        if subcmd is not None and self.target_platform == "win32":
            raise TranspilerError(
                "Nested command substitutions $(...) are not supported on Windows (cmd.exe). "
                "Assign the inner command to a variable first and pass its output: "
                "inner = $(...); $(outer {inner.text})",
                line=subcmd.line,
                column=subcmd.column,
            )

        if node.background:
            if not self.unsafe_interpolation:
                return self._transform_expanded_command(
                    node,
                    is_statement=is_statement,
                    is_tee=is_tee,
                    input_expr=input_expr,
                    is_background=True,
                )
            else:
                self.used_symbols.add("run_bg")
                raw = node.raw
                cmd_arg = (
                    self._format_fstring(raw)
                    if node.pipeline.has_interpolation()
                    else self._format_literal_string(raw)
                )
                return f"run_bg({cmd_arg})"
        elif is_async:
            if not self.unsafe_interpolation:
                return self._transform_expanded_command(
                    node,
                    is_statement=is_statement,
                    is_tee=is_tee,
                    input_expr=input_expr,
                    is_async=True,
                )
            else:
                self.used_symbols.add("async_run")
                raw = node.raw
                cmd_arg = (
                    self._format_fstring(raw)
                    if node.pipeline.has_interpolation()
                    else self._format_literal_string(raw)
                )
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
        elif not self.unsafe_interpolation and (
            node.pipeline.has_interpolation()
            or len(node.pipeline.commands) > 1
            or any(
                isinstance(p, RedirectionNode)
                for cmd in node.pipeline.commands
                for p in cmd.parts
            )
        ):
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
        is_async: bool = False,
        is_background: bool = False,
    ) -> str:
        args: list[str] = []

        for cmd_idx, cmd in enumerate(node.pipeline.commands):
            if cmd_idx > 0:
                self.used_symbols.add("ShellOp")
                args.append('ShellOp("|")')

            groups: list[list[CommandPartNode]] = []
            current_group: list[CommandPartNode] = []

            for part in cmd.parts:
                if isinstance(part, (RedirectionNode, SplatNode, SubcommandNode)):
                    if current_group:
                        groups.append(current_group)
                        current_group = []
                    groups.append([part])
                    continue

                if not current_group:
                    current_group.append(part)
                else:
                    if getattr(part, "has_leading_space", True):
                        groups.append(current_group)
                        current_group = [part]
                    else:
                        current_group.append(part)

            if current_group:
                groups.append(current_group)

            for group in groups:
                if len(group) == 1:
                    part = group[0]
                    if isinstance(part, SplatNode):
                        args.append(f"*{part.expression}")
                    elif isinstance(part, InterpolationNode):
                        if part.expression:
                            args.append(f"({part.expression})")
                    elif isinstance(part, EnvVarNode):
                        self._needs_os_import = True
                        args.append(f'os.environ["{part.name}"]')
                    elif isinstance(part, WordNode):
                        if "{" in part.value and "}" in part.value:
                            args.append(self._format_fstring(part.value, for_argv=True))
                        else:
                            args.append(self._format_literal_string(part.value))
                    elif isinstance(part, StringNode):
                        if part.quote == '"' and "{" in part.value and "}" in part.value:
                            args.append(self._format_fstring(part.value, for_argv=True))
                        else:
                            args.append(self._format_literal_string(part.value))
                    elif isinstance(part, RedirectionNode):
                        target = part.target
                        self.used_symbols.add("ShellOp")
                        args.append(f'ShellOp("{part.operator}")')
                        if "{" in target and "}" in target:
                            target_unquoted = target.strip().strip("'\"")
                            if (
                                target_unquoted.startswith("{")
                                and target_unquoted.endswith("}")
                                and target_unquoted.count("{") == 1
                                and target_unquoted.count("}") == 1
                            ):
                                expr = target_unquoted[1:-1].strip()
                                args.append(f"({expr})")
                            else:
                                args.append(self._format_fstring(target, for_argv=True))
                        else:
                            args.append(self._format_literal_string(target))
                    elif isinstance(part, SubcommandNode):
                        self.used_symbols.add("ShellOp")
                        raw_sub = part.raw if part.raw else f"$({part.pipeline})"
                        if "{" in raw_sub and "}" in raw_sub:
                            args.append(f"ShellOp({self._format_fstring(raw_sub, for_argv=True)})")
                        else:
                            args.append(f"ShellOp({self._format_literal_string(raw_sub)})")
                else:
                    fstring_pieces: list[str] = []
                    for p in group:
                        if isinstance(p, InterpolationNode):
                            fstring_pieces.append("{" + p.expression + "}")
                        elif isinstance(p, EnvVarNode):
                            self._needs_os_import = True
                            fstring_pieces.append("{" + f'os.environ["{p.name}"]' + "}")
                        elif isinstance(p, WordNode):
                            if "{" in p.value and "}" in p.value:
                                fstring_pieces.append(self._escape_non_interpolations(p.value, for_argv=True))
                            else:
                                escaped = (
                                    p.value.replace("\\", "\\\\")
                                    .replace('"', '\\"')
                                    .replace("{", "{{")
                                    .replace("}", "}}")
                                )
                                fstring_pieces.append(escaped)
                        elif isinstance(p, StringNode):
                            if p.quote == '"' and "{" in p.value and "}" in p.value:
                                fstring_pieces.append(self._escape_non_interpolations(p.value, for_argv=True))
                            else:
                                escaped = (
                                    p.value.replace("\\", "\\\\")
                                    .replace('"', '\\"')
                                    .replace("{", "{{")
                                    .replace("}", "}}")
                                )
                                fstring_pieces.append(escaped)
                    combined = "".join(fstring_pieces)
                    args.append(f'f"{combined}"')

        if is_background:
            self.used_symbols.add("run_bg")
            return f"run_bg({', '.join(args)})"

        if is_async:
            fn_name = "async_run"
            self.used_symbols.add("async_run")
        else:
            fn_name = "run_expanded"
            self.used_symbols.add("run_expanded")

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

        return f"{fn_name}({', '.join(args)})"

    def _escape_non_interpolations(self, text: str, for_argv: bool = False) -> str:
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
                                if (
                                    not for_argv
                                    and not self.unsafe_interpolation
                                    and not inner.startswith("shell_quote(")
                                ):
                                    self.used_symbols.add("shell_quote")
                                    out.append("{" + f"shell_quote({inner}, in_double_quotes=True)" + "}")
                                else:
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
                        if (
                            not for_argv
                            and not self.unsafe_interpolation
                            and not inner.startswith("shell_quote(")
                        ):
                            self.used_symbols.add("shell_quote")
                            out.append("{" + f"shell_quote({inner})" + "}")
                        else:
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

    def _format_fstring(self, text: str, for_argv: bool = False) -> str:
        """Formats a string containing {...} expressions as a valid Python f-string literal."""
        text = self._escape_non_interpolations(text, for_argv=for_argv)
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

    def _inject_imports(self, code: str, symbols: Set[str]) -> tuple[str, int, int]:
        """Injects necessary pycli.runtime imports at the proper position.

        Returns (updated_code, lines_added, insert_line_index).
        """
        lines = code.splitlines(keepends=True)
        if not symbols:
            return code, 0, 0

        # Try AST-based injection first
        try:
            tree = ast.parse(code)
        except Exception:
            tree = None

        if tree is not None:
            # 1. Check for existing 'from pycli.runtime import ...'
            for stmt in tree.body:
                if isinstance(stmt, ast.ImportFrom) and stmt.module == "pycli.runtime":
                    imported = {alias.name for alias in stmt.names}
                    if "*" in imported:
                        return code, 0, stmt.lineno - 1
                    missing = sorted(symbols - imported)
                    if not missing:
                        return code, 0, stmt.lineno - 1
                    # If it's a single-line import without aliases, update it in place
                    if stmt.lineno == stmt.end_lineno and not any(alias.asname for alias in stmt.names):
                        all_symbols = sorted(imported.union(missing))
                        newline = "\n" if lines[stmt.lineno - 1].endswith("\n") else ""
                        lines[stmt.lineno - 1] = f"from pycli.runtime import {', '.join(all_symbols)}{newline}"
                        return "".join(lines), 0, stmt.lineno - 1
                    else:
                        # Multiline or aliased: insert right after end_lineno
                        insert_idx = stmt.end_lineno
                        import_stmt = f"from pycli.runtime import {', '.join(missing)}\n"
                        result = "".join(lines[:insert_idx]) + import_stmt + "".join(lines[insert_idx:])
                        return result, 1, insert_idx

            # 2. Determine insertion point
            future_stmts = [
                stmt
                for stmt in tree.body
                if isinstance(stmt, ast.ImportFrom) and stmt.module == "__future__"
            ]
            if future_stmts:
                insert_idx = max(stmt.end_lineno for stmt in future_stmts)
            elif (
                tree.body
                and isinstance(tree.body[0], ast.Expr)
                and isinstance(tree.body[0].value, ast.Constant)
                and isinstance(tree.body[0].value.value, str)
            ):
                insert_idx = tree.body[0].end_lineno
            else:
                insert_idx = 0
                while insert_idx < len(lines):
                    line_str = lines[insert_idx].strip()
                    if insert_idx == 0 and line_str.startswith("#!"):
                        insert_idx += 1
                    elif line_str.startswith("#") and ("coding:" in line_str or "coding=" in line_str):
                        insert_idx += 1
                    else:
                        break

            sorted_symbols = sorted(symbols)
            import_stmt = f"from pycli.runtime import {', '.join(sorted_symbols)}\n"
            result = "".join(lines[:insert_idx]) + import_stmt + "".join(lines[insert_idx:])
            return result, 1, insert_idx

        # Fallback when AST cannot parse code
        insert_idx = 0
        if lines and lines[0].startswith("#!"):
            insert_idx = 1

        sorted_symbols = sorted(symbols)
        import_stmt = f"from pycli.runtime import {', '.join(sorted_symbols)}\n"
        result = "".join(lines[:insert_idx]) + import_stmt + "".join(lines[insert_idx:])
        return result, 1, insert_idx

    def _inject_os_import(self, code: str) -> tuple[str, int, int]:
        """Inject 'import os' at the proper position if not already present.

        Returns (updated_code, lines_added, insert_line_index).
        """
        import ast as _ast
        # Check if 'import os' or 'from os import ...' is already present
        try:
            tree = _ast.parse(code)
        except Exception:
            tree = None

        if tree is not None:
            for stmt in tree.body:
                if isinstance(stmt, _ast.Import):
                    for alias in stmt.names:
                        if alias.name == "os" and alias.asname is None:
                            return code, 0, 0  # already imported
                if isinstance(stmt, _ast.ImportFrom) and stmt.module == "os":
                    return code, 0, 0  # already imported from os

        lines = code.splitlines(keepends=True)
        # Insert after shebang, coding comment, future imports, pycli.runtime import, and docstrings
        insert_idx = 0
        if lines and lines[0].startswith("#!"):
            insert_idx = 1
        # Skip coding comment
        if insert_idx < len(lines):
            stripped = lines[insert_idx].strip()
            if stripped.startswith("#") and ("coding:" in stripped or "coding=" in stripped):
                insert_idx += 1
        # Skip future imports, pycli.runtime import, and module docstrings
        try:
            if tree is not None:
                future_and_runtime = [
                    stmt for stmt in tree.body
                    if (isinstance(stmt, _ast.ImportFrom) and stmt.module in ("__future__", "pycli.runtime"))
                ]
                if future_and_runtime:
                    last_line = max(stmt.end_lineno for stmt in future_and_runtime)
                    insert_idx = max(insert_idx, last_line)
                elif (
                    tree.body
                    and isinstance(tree.body[0], _ast.Expr)
                    and isinstance(tree.body[0].value, _ast.Constant)
                    and isinstance(tree.body[0].value.value, str)
                ):
                    insert_idx = max(insert_idx, tree.body[0].end_lineno)
        except Exception:
            pass

        import_line = "import os\n"
        result = "".join(lines[:insert_idx]) + import_line + "".join(lines[insert_idx:])
        return result, 1, insert_idx


def transpile(
    source: str,
    auto_import: bool = True,
    validate: bool = False,
    unsafe_interpolation: bool = False,
    target_platform: str | None = None,
) -> str:
    """Convenience function to transpile .spy source code to Python."""
    return Transformer(
        auto_import=auto_import,
        unsafe_interpolation=unsafe_interpolation,
        target_platform=target_platform,
    ).transform(source, validate=validate)
