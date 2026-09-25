"""Runtime support for pycli: run(), run_expanded(), CommandResult, DynamicObj."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from typing import Any, Iterator, Mapping, Sequence


class CommandError(Exception):
    """Raised when a command executed with check=True fails (non-zero exit code)."""

    def __init__(self, result: CommandResult) -> None:
        self.result = result
        super().__init__(
            f"Command {result.command!r} failed with exit code {result.exit_code}.\n"
            f"stderr: {result.stderr.strip()}"
        )


class DynamicObj:
    """Provides JavaScript/PowerShell-style dot attribute access for parsed JSON dictionaries.

    Supports both attribute access (obj.foo) and dict-style indexing (obj['foo']),
    as well as nested dictionaries and lists.
    """

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data: dict[str, Any] = dict(data)
        for key, value in self._data.items():
            self._data[key] = wrap_json(value)

    def __getattribute__(self, name: str) -> Any:
        if not name.startswith("_") and name != "to_dict":
            try:
                data = object.__getattribute__(self, "_data")
                if name in data:
                    return data[name]
            except AttributeError:
                pass
        return object.__getattribute__(self, name)

    def __getattr__(self, name: str) -> Any:
        if name in self._data:
            return self._data[name]
        raise AttributeError(f"'DynamicObj' object has no attribute {name!r}")

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()

    def to_dict(self) -> dict[str, Any]:
        """Convert back to primitive dict recursively."""
        result: dict[str, Any] = {}
        for k, v in self._data.items():
            if isinstance(v, DynamicObj):
                result[k] = v.to_dict()
            elif isinstance(v, list):
                result[k] = [
                    item.to_dict() if isinstance(item, DynamicObj) else item
                    for item in v
                ]
            else:
                result[k] = v
        return result

    def __repr__(self) -> str:
        return f"DynamicObj({self._data!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, DynamicObj):
            return self._data == other._data
        if isinstance(other, dict):
            return self._data == other
        return False


def wrap_json(data: Any) -> Any:
    """Recursively wrap dicts into DynamicObj and lists of dicts."""
    if isinstance(data, Mapping):
        return DynamicObj(data)
    if isinstance(data, list):
        return [wrap_json(item) for item in data]
    return data


class CommandResult:
    """The result of executing a shell command via pycli."""

    def __init__(
        self,
        command: str,
        stdout: str,
        stderr: str,
        exit_code: int,
        duration: float,
    ) -> None:
        self.command = command
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.duration = duration
        self._parsed_json: Any = None
        self._json_parsed = False

    def __bool__(self) -> bool:
        """Command results evaluate according to exit code (0 -> True, non-zero -> False)."""
        return self.exit_code == 0

    @property
    def json(self) -> Any:
        """Parse stdout as JSON and return dynamic structures (DynamicObj or list)."""
        if not self._json_parsed:
            content = self.stdout.strip()
            if not content:
                raise ValueError(f"Cannot parse JSON from empty stdout for command: {self.command!r}")
            raw = json.loads(content)
            self._parsed_json = wrap_json(raw)
            self._json_parsed = True
        return self._parsed_json

    def __str__(self) -> str:
        return self.stdout

    def __repr__(self) -> str:
        return (
            f"CommandResult(command={self.command!r}, exit_code={self.exit_code}, "
            f"duration={self.duration:.3f}s)"
        )


def run(
    command: str | Sequence[str],
    *,
    check: bool = False,
    shell: bool | None = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Execute a shell command and return a CommandResult.

    Args:
        command: The shell command string or argument list to execute.
        check: If True, raises CommandError if exit_code != 0 (Strict Mode).
        shell: Whether to run command through the system shell. If None, True for str, False for list.
        cwd: Working directory to run command in.
        env: Environment variables dictionary.
    """
    if shell is None:
        shell = isinstance(command, str)

    cmd_str = command if isinstance(command, str) else " ".join(str(c) for c in command)

    start = time.perf_counter()
    proc = subprocess.run(
        command,
        shell=shell,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=dict(env) if env is not None else None,
    )
    duration = time.perf_counter() - start

    result = CommandResult(
        command=cmd_str,
        stdout=proc.stdout,
        stderr=proc.stderr,
        exit_code=proc.returncode,
        duration=duration,
    )

    if check and result.exit_code != 0:
        raise CommandError(result)

    return result


def run_expanded(
    *parts: Any,
    check: bool = False,
    shell: bool | None = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Execute a command composed of multiple parts, supporting splat list expansion.

    Example:
        run_expanded("rm", *files)
    """
    arg_strings: list[str] = []
    for part in parts:
        if isinstance(part, (list, tuple, set)):
            for item in part:
                arg_strings.append(str(item))
        else:
            arg_strings.append(str(part))

    # If any part contains shell operators (| > < >> && ||), run as shell string
    shell_operators = {"|", ">", "<", ">>", "&&", "||"}
    has_operator = any(op in arg_strings for op in shell_operators) or any(
        any(op in a for op in [">", "<", "|"]) for a in arg_strings
    )

    if shell is None:
        shell = has_operator

    if shell:
        command_str = " ".join(arg_strings)
        return run(command_str, check=check, shell=True, cwd=cwd, env=env)

    return run(arg_strings, check=check, shell=False, cwd=cwd, env=env)
