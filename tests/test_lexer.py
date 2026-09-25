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
