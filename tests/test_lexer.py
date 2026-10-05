import pytest
from pycli.lexer import Lexer, LexerError, TokenType


def test_lexer_simple_command():
    source = "x = $(git status)\n"
    lexer = Lexer(source)
    tokens = lexer.tokenize()

    assert len(tokens) == 3
    assert tokens[0].type == TokenType.PYTHON_CODE
    assert tokens[0].value == "x = "
    assert tokens[1].type == TokenType.COMMAND_EXPR
    assert tokens[1].value == "git status"
    assert tokens[1].strict is False
    assert tokens[2].type == TokenType.PYTHON_CODE
    assert tokens[2].value == "\n"


def test_lexer_strict_command():
    source = "$(git status)!\n"
    lexer = Lexer(source)
    tokens = lexer.tokenize()

    assert len(tokens) == 2
    assert tokens[0].type == TokenType.COMMAND_EXPR
    assert tokens[0].value == "git status"
    assert tokens[0].strict is True


def test_lexer_interpolation_with_parens():
    source = "res = $(echo {get_val(1, 2)})\n"
    lexer = Lexer(source)
    tokens = lexer.tokenize()

    assert len(tokens) == 3
    assert tokens[1].type == TokenType.COMMAND_EXPR
    assert tokens[1].value == "echo {get_val(1, 2)}"


def test_lexer_quotes_with_parens_inside_command():
    source = '$(echo ")")\n'
    lexer = Lexer(source)
    tokens = lexer.tokenize()

    assert tokens[0].type == TokenType.COMMAND_EXPR
    assert tokens[0].value == 'echo ")"'


def test_lexer_nested_subcommand():
    source = "$(echo $(git branch --show-current))\n"
    lexer = Lexer(source)
    tokens = lexer.tokenize()

    assert tokens[0].type == TokenType.COMMAND_EXPR
    assert tokens[0].value == "echo $(git branch --show-current)"


def test_lexer_ignores_dollar_in_strings_and_comments():
    source = '# $(comment)\ns = "$(not_a_command)"\n'
    lexer = Lexer(source)
    tokens = lexer.tokenize()

    assert len(tokens) == 1
    assert tokens[0].type == TokenType.PYTHON_CODE
    assert tokens[0].value == source


def test_lexer_unclosed_command_raises():
    source = "x = $(git status"
    lexer = Lexer(source)
    with pytest.raises(LexerError):
        lexer.tokenize()


def test_lexer_consecutive_commands_offsets():
    # Verify exact column/line of consecutive commands with modifiers
    source = "$(echo 1)!\n$(echo 2)\n"
    lexer = Lexer(source)
    tokens = lexer.tokenize()
    assert tokens[0].type == TokenType.COMMAND_EXPR
    assert tokens[0].line == 1
    assert tokens[0].column == 1
    assert tokens[0].strict is True

    # Token 1 is newline
    assert tokens[1].type == TokenType.PYTHON_CODE
    assert tokens[1].value == "\n"

    # Token 2 is the second command, must be at line 2, col 1
    assert tokens[2].type == TokenType.COMMAND_EXPR
    assert tokens[2].line == 2
    assert tokens[2].column == 1


def test_lexer_modifier_with_spaces():
    source = "$(cmd)   !\n"
    lexer = Lexer(source)
    tokens = lexer.tokenize()
    assert tokens[0].strict is True
    # Next token is newline on line 1, column 11
    assert tokens[1].line == 1
    assert tokens[1].column == 11


def test_lexer_bitwise_and_not_background():
    # In 'res = $(cmd) & mask', '&' is followed by 'mask' so it's a Python operator
    source = "res = $(cmd) & mask\n"
    lexer = Lexer(source)
    tokens = lexer.tokenize()
    assert tokens[1].type == TokenType.COMMAND_EXPR
    assert tokens[1].background is False
    assert tokens[2].type == TokenType.PYTHON_CODE
    assert tokens[2].value.startswith(" & mask")


def test_lexer_env_var_simple():
    source = "home = $HOME\n"
    lexer = Lexer(source)
    tokens = lexer.tokenize()

    assert len(tokens) == 3
    assert tokens[0].type == TokenType.PYTHON_CODE
    assert tokens[0].value == "home = "
    assert tokens[1].type == TokenType.ENV_VAR
    assert tokens[1].value == "HOME"
    assert tokens[2].type == TokenType.PYTHON_CODE
    assert tokens[2].value == "\n"


def test_lexer_env_var_underscore_led():
    source = "x = $_MY_VAR\n"
    lexer = Lexer(source)
    tokens = lexer.tokenize()

    assert tokens[1].type == TokenType.ENV_VAR
    assert tokens[1].value == "_MY_VAR"


def test_lexer_env_var_mixed_after_command():
    source = "x = $(git rev-parse HEAD)\ny = $CI_COMMIT_SHA\n"
    lexer = Lexer(source)
    tokens = lexer.tokenize()

    env_tokens = [t for t in tokens if t.type == TokenType.ENV_VAR]
    assert len(env_tokens) == 1
    assert env_tokens[0].value == "CI_COMMIT_SHA"


def test_lexer_env_var_inside_string_is_not_captured():
    # Inside a Python string, $HOME must NOT be tokenized as ENV_VAR
    source = 's = "$HOME"\n'
    lexer = Lexer(source)
    tokens = lexer.tokenize()

    assert all(t.type == TokenType.PYTHON_CODE for t in tokens)
    assert "$HOME" in "".join(t.value for t in tokens)


def test_lexer_lowercase_dollar_is_not_env_var():
    # $home is NOT a valid ENV_VAR — stays as Python code (SyntaxError territory)
    source = "x = $home\n"
    lexer = Lexer(source)
    tokens = lexer.tokenize()

    assert all(t.type == TokenType.PYTHON_CODE for t in tokens)


def test_lexer_dollar_open_paren_is_not_env_var():
    source = "$(echo hi)\n"
    lexer = Lexer(source)
    tokens = lexer.tokenize()

    assert tokens[0].type == TokenType.COMMAND_EXPR

