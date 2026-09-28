"""Tests for the ANSI syntax highlighter."""

import os
import re
from pycli.highlighter import highlight_python, should_colorize


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return re.sub(r"\033\[[0-9;]*m", "", text)


def test_highlight_python_lossless():
    """Verify that highlighting preserves exact code content and spacing."""
    code = """from pycli.runtime import run, run_expanded

def fetch_data(url: str) -> dict:
    # Fetch JSON output
    res = run(f"curl -s {url}").json
    return res

if __name__ == "__main__":
    data = fetch_data("https://api.example.com")
    print(data)
"""
    colored = highlight_python(code)
    assert colored != code  # Must contain ANSI codes
    assert strip_ansi(colored) == code  # Must reconstruct perfectly


def test_highlight_python_syntax_elements():
    """Verify that keywords, comments, strings, and pycli runtime symbols get colored."""
    code = 'def test():\n    # comment\n    return run("git status").json\n'
    colored = highlight_python(code)

    # Keywords should have \033[94;1m
    assert "\033[94;1mdef\033[0m" in colored
    assert "\033[94;1mreturn\033[0m" in colored

    # Comments should have \033[90;3m
    assert "\033[90;3m# comment\033[0m" in colored

    # Strings should have \033[92m
    assert '\033[92m"git status"\033[0m' in colored

    # pycli symbols should have \033[95;1m
    assert "\033[95;1mrun\033[0m" in colored
    assert "\033[95;1mjson\033[0m" in colored


def test_should_colorize():
    """Test should_colorize behavior with flags and NO_COLOR env var."""
    assert should_colorize(force_color=True, no_color=False) is True
    assert should_colorize(force_color=True, no_color=True) is False

    os.environ["NO_COLOR"] = "1"
    try:
        assert should_colorize(force_color=False, no_color=False) is False
    finally:
        del os.environ["NO_COLOR"]
