"""Runtime support for pycli: run(), run_expanded(), run_bg(), async_run(), CommandResult, cd(), env()."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
import contextvars
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

_current_expression: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_current_expression", default=None
)


@contextmanager
def set_current_expression(expr: str) -> Iterator[None]:
    """Context manager to set the current .spy original command expression."""
    token = _current_expression.set(expr)
    try:
        yield
    finally:
        _current_expression.reset(token)


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Terminate the process and all child processes it may have spawned."""
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def shell_quote(val: Any) -> str:
    """Quote a value for safe shell interpolation."""
    return shlex.quote(str(val))


class CommandError(Exception):
    """Raised when a command executed with check=True fails (non-zero exit code)."""

    def __init__(self, result: CommandResult) -> None:
        self.result = result
        parts = [f"Command {result.command!r} failed with exit code {result.exit_code}."]
        if result.original_expression:
            parts.append(f"Original expression: {result.original_expression}")
        stderr_text = result.stderr.strip()
        if stderr_text:
            parts.append(f"stderr: {stderr_text}")
        super().__init__("\n".join(parts))


class CommandTimeoutError(CommandError):
    """Raised when a command exceeds its configured timeout."""


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
        *,
        original_expression: str | None = None,
        truncated: bool = False,
    ) -> None:
        self.command = command
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.duration = duration
        self.original_expression = original_expression
        self.truncated = truncated
        self._parsed_json: Any = None
        self._json_parsed = False

    def __bool__(self) -> bool:
        """Command results evaluate according to exit code (0 -> True, non-zero -> False)."""
        return self.exit_code == 0

    @property
    def lines(self) -> list[str]:
        """Return stdout split into lines with trailing newlines stripped."""
        return [line.rstrip("\r\n") for line in self.stdout.splitlines()]

    @property
    def text(self) -> str:
        """Return stripped stdout content."""
        return self.stdout.strip()

    def __iter__(self) -> Iterator[str]:
        """Iterate directly over output lines: for line in $(git status --porcelain):"""
        return iter(self.lines)

    def __getitem__(self, index: int | slice) -> str | list[str]:
        """Allow indexing into output lines: $(cmd)[0]."""
        return self.lines[index]

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


class BackgroundJob:
    """Represents a background non-blocking shell process."""

    def __init__(
        self,
        proc: subprocess.Popen,
        command: str,
        start_time: float,
        *,
        encoding: str = "utf-8",
        original_expression: str | None = None,
    ) -> None:
        self.proc = proc
        self.command = command
        self.start_time = start_time
        self.encoding = encoding
        self.original_expression = original_expression
        self._result: CommandResult | None = None

    @property
    def is_running(self) -> bool:
        """Check if process is still running."""
        return self.proc.poll() is None

    def poll(self) -> int | None:
        """Return exit code if terminated, otherwise None."""
        return self.proc.poll()

    def kill(self) -> None:
        """Terminate the process."""
        _kill_process_tree(self.proc)

    def wait(self, timeout: float | None = None) -> CommandResult:
        """Wait for command to finish and return CommandResult."""
        if self._result is not None:
            return self._result
        try:
            stdout, stderr = self.proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as e:
            _kill_process_tree(self.proc)
            try:
                stdout, stderr = self.proc.communicate(timeout=1)
            except Exception:
                stdout, stderr = "", ""
            duration = time.perf_counter() - self.start_time
            self._result = CommandResult(
                command=self.command,
                stdout=stdout or "",
                stderr=stderr or "",
                exit_code=-1,
                duration=duration,
                original_expression=self.original_expression,
            )
            raise CommandTimeoutError(self._result) from e
        except KeyboardInterrupt:
            try:
                if sys.platform == "win32":
                    _kill_process_tree(self.proc)
                else:
                    self.proc.send_signal(signal.SIGINT)
                self.proc.wait(timeout=2)
            except Exception:
                _kill_process_tree(self.proc)
            raise
        except BrokenPipeError:
            stdout, stderr = "", ""

        duration = time.perf_counter() - self.start_time
        self._result = CommandResult(
            command=self.command,
            stdout=stdout or "",
            stderr=stderr or "",
            exit_code=self.proc.returncode if self.proc.returncode is not None else 0,
            duration=duration,
            original_expression=self.original_expression,
        )
        return self._result

    @classmethod
    def wait_all(
        cls,
        *jobs: BackgroundJob | Sequence[BackgroundJob],
        timeout: float | None = None,
    ) -> list[CommandResult]:
        """Wait for multiple BackgroundJob instances to finish and return their CommandResults."""
        return wait_all(*jobs, timeout=timeout)


def _truncate_output(text: str, max_bytes: int | None, encoding: str) -> tuple[str, bool]:
    if max_bytes is None:
        return text, False
    raw_b = text.encode(encoding, errors="replace")
    if len(raw_b) > max_bytes:
        truncated_text = raw_b[:max_bytes].decode(encoding, errors="replace")
        return truncated_text, True
    return text, False


def run(
    command: str | Sequence[str],
    *,
    capture: bool = True,
    check: bool = False,
    tee: bool = False,
    input: str | bytes | None = None,
    suppress_errors: bool = False,
    shell: bool | None = None,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    encoding: str = "utf-8",
    max_output_bytes: int | None = None,
    original_expression: str | None = None,
) -> CommandResult:
    """Execute a shell command and return a CommandResult.

    Args:
        command: The shell command string or argument list to execute.
        capture: If False, streams stdout/stderr directly to console without capturing.
        check: If True and exit_code != 0, raises CommandError (Strict Mode).
        tee: If True, streams output live to console AND captures it.
        input: Text or bytes to pipe into process stdin.
        suppress_errors: If True, prevents CommandError from being raised even if check=True.
        shell: Whether to run through system shell. Defaults to True for str, False for list.
        cwd: Directory to execute command in.
        env: Environment variables dict.
        timeout: Timeout in seconds. If exceeded, terminates process and raises CommandTimeoutError.
        encoding: Text encoding for command input and output (default "utf-8").
        max_output_bytes: Maximum stdout/stderr output bytes to capture before truncating.
        original_expression: The original .spy source expression for debugging and observability.
    """
    if shell is None:
        shell = isinstance(command, str)

    cmd_str = command if isinstance(command, str) else " ".join(str(c) for c in command)
    cwd_str = str(cwd) if cwd is not None else None
    env_dict = dict(env) if env is not None else None

    input_text = None
    if input is not None:
        if isinstance(input, bytes):
            input_text = input.decode(encoding, errors="replace")
        else:
            input_text = str(input)

    start = time.perf_counter()
    if original_expression is None:
        original_expression = _current_expression.get()

    if not capture and not tee and input_text is None:
        # Native direct passthrough streaming without buffering
        proc = subprocess.Popen(
            command,
            shell=shell,
            stdout=None,
            stderr=None,
            cwd=cwd_str,
            env=env_dict,
        )
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as e:
            _kill_process_tree(proc)
            try:
                proc.wait(timeout=1)
            except Exception:
                pass
            duration = time.perf_counter() - start
            result = CommandResult(
                command=cmd_str,
                stdout="",
                stderr="",
                exit_code=-1,
                duration=duration,
                original_expression=original_expression,
            )
            raise CommandTimeoutError(result) from e
        except KeyboardInterrupt:
            try:
                if sys.platform == "win32":
                    _kill_process_tree(proc)
                else:
                    proc.send_signal(signal.SIGINT)
                proc.wait(timeout=2)
            except Exception:
                _kill_process_tree(proc)
            raise

        duration = time.perf_counter() - start
        result = CommandResult(
            command=cmd_str,
            stdout="",
            stderr="",
            exit_code=proc.returncode if proc.returncode is not None else 0,
            duration=duration,
            original_expression=original_expression,
        )

    elif tee:
        # Live streaming while capturing
        proc = subprocess.Popen(
            command,
            shell=shell,
            stdin=subprocess.PIPE if input_text is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding=encoding,
            errors="replace",
            bufsize=1,
            cwd=cwd_str,
            env=env_dict,
        )

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        def reader(pipe, out_stream, chunks):
            try:
                for line in iter(pipe.readline, ""):
                    out_stream.write(line)
                    out_stream.flush()
                    chunks.append(line)
            finally:
                pipe.close()

        t_out = threading.Thread(target=reader, args=(proc.stdout, sys.stdout, stdout_chunks))
        t_err = threading.Thread(target=reader, args=(proc.stderr, sys.stderr, stderr_chunks))
        t_out.start()
        t_err.start()

        if input_text is not None and proc.stdin:
            proc.stdin.write(input_text)
            proc.stdin.close()

        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as e:
            proc.kill()
            t_out.join(timeout=2)
            t_err.join(timeout=2)
            duration = time.perf_counter() - start
            result = CommandResult(
                command=cmd_str,
                stdout="".join(stdout_chunks),
                stderr="".join(stderr_chunks),
                exit_code=-1,
                duration=duration,
                original_expression=original_expression,
            )
            raise CommandTimeoutError(result) from e
        except KeyboardInterrupt:
            try:
                if sys.platform == "win32":
                    _kill_process_tree(proc)
                else:
                    proc.send_signal(signal.SIGINT)
                proc.wait(timeout=2)
            except Exception:
                _kill_process_tree(proc)
            t_out.join(timeout=2)
            t_err.join(timeout=2)
            raise

        t_out.join()
        t_err.join()

        duration = time.perf_counter() - start
        raw_stdout = "".join(stdout_chunks)
        raw_stderr = "".join(stderr_chunks)

        stdout, out_trunc = _truncate_output(raw_stdout, max_output_bytes, encoding)
        stderr, err_trunc = _truncate_output(raw_stderr, max_output_bytes, encoding)

        result = CommandResult(
            command=cmd_str,
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode if proc.returncode is not None else 0,
            duration=duration,
            original_expression=original_expression,
            truncated=(out_trunc or err_trunc),
        )

    else:
        # Standard subprocess run
        proc = subprocess.Popen(
            command,
            shell=shell,
            stdin=subprocess.PIPE if input_text is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding=encoding,
            errors="replace",
            cwd=cwd_str,
            env=env_dict,
        )
        try:
            raw_stdout, raw_stderr = proc.communicate(input=input_text, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            _kill_process_tree(proc)
            try:
                raw_stdout, raw_stderr = proc.communicate(timeout=1)
            except Exception:
                raw_stdout, raw_stderr = "", ""
            duration = time.perf_counter() - start
            result = CommandResult(
                command=cmd_str,
                stdout=raw_stdout or "",
                stderr=raw_stderr or "",
                exit_code=-1,
                duration=duration,
                original_expression=original_expression,
            )
            raise CommandTimeoutError(result) from e
        except KeyboardInterrupt:
            try:
                if sys.platform == "win32":
                    _kill_process_tree(proc)
                else:
                    proc.send_signal(signal.SIGINT)
                proc.wait(timeout=2)
            except Exception:
                _kill_process_tree(proc)
            raise

        duration = time.perf_counter() - start

        stdout, out_trunc = _truncate_output(raw_stdout or "", max_output_bytes, encoding)
        stderr, err_trunc = _truncate_output(raw_stderr or "", max_output_bytes, encoding)

        if not capture:
            if stdout:
                sys.stdout.write(stdout)
                sys.stdout.flush()
            if stderr:
                sys.stderr.write(stderr)
                sys.stderr.flush()

        result = CommandResult(
            command=cmd_str,
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode if proc.returncode is not None else 0,
            duration=duration,
            original_expression=original_expression,
            truncated=(out_trunc or err_trunc),
        )

    if check and not suppress_errors and result.exit_code != 0:
        raise CommandError(result)

    return result


def run_expanded(
    *parts: Any,
    capture: bool = True,
    check: bool = False,
    tee: bool = False,
    input: str | bytes | None = None,
    suppress_errors: bool = False,
    shell: bool | None = None,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    encoding: str = "utf-8",
    max_output_bytes: int | None = None,
    original_expression: str | None = None,
) -> CommandResult:
    """Execute a command composed of multiple parts, supporting splat list expansion."""
    arg_strings: list[str] = []
    for part in parts:
        if isinstance(part, (list, tuple, set)):
            for item in part:
                arg_strings.append(str(item))
        else:
            arg_strings.append(str(part))

    shell_operators = {"|", ">", "<", ">>", "&&", "||"}
    has_operator = any(op in arg_strings for op in shell_operators) or any(
        any(op in a for op in [">", "<", "|"]) for a in arg_strings
    )

    if shell is None:
        shell = has_operator

    if shell:
        command_str = " ".join(arg_strings)
        return run(
            command_str,
            capture=capture,
            check=check,
            tee=tee,
            input=input,
            suppress_errors=suppress_errors,
            shell=True,
            cwd=cwd,
            env=env,
            timeout=timeout,
            encoding=encoding,
            max_output_bytes=max_output_bytes,
            original_expression=original_expression,
        )

    return run(
        arg_strings,
        capture=capture,
        check=check,
        tee=tee,
        input=input,
        suppress_errors=suppress_errors,
        shell=False,
        cwd=cwd,
        env=env,
        timeout=timeout,
        encoding=encoding,
        max_output_bytes=max_output_bytes,
        original_expression=original_expression,
    )


def run_bg(
    command: str | Sequence[str],
    *,
    shell: bool | None = None,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    encoding: str = "utf-8",
    original_expression: str | None = None,
) -> BackgroundJob:
    """Launch a non-blocking background command and return a BackgroundJob."""
    if shell is None:
        shell = isinstance(command, str)

    cmd_str = command if isinstance(command, str) else " ".join(str(c) for c in command)
    cwd_str = str(cwd) if cwd is not None else None
    env_dict = dict(env) if env is not None else None

    start = time.perf_counter()
    if original_expression is None:
        original_expression = _current_expression.get()
    proc = subprocess.Popen(
        command,
        shell=shell,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding=encoding,
        errors="replace",
        cwd=cwd_str,
        env=env_dict,
    )
    return BackgroundJob(
        proc,
        cmd_str,
        start,
        encoding=encoding,
        original_expression=original_expression,
    )


def wait_all(
    *jobs: BackgroundJob | Sequence[BackgroundJob],
    timeout: float | None = None,
) -> list[CommandResult]:
    """Wait for all specified BackgroundJob instances to complete and return their CommandResults.

    Accepts jobs as positional arguments or as a sequence:
        results = wait_all(job1, job2, job3)
        results = wait_all([job1, job2, job3])
    """
    flat_jobs: list[BackgroundJob] = []
    for item in jobs:
        if isinstance(item, (list, tuple, Sequence)) and not isinstance(item, (str, bytes)):
            flat_jobs.extend(item)
        elif isinstance(item, BackgroundJob):
            flat_jobs.append(item)
        else:
            raise TypeError(f"Expected BackgroundJob or sequence of BackgroundJob, got {type(item).__name__}")

    return [job.wait(timeout=timeout) for job in flat_jobs]


async def async_run(
    command: str,
    *,
    capture: bool = True,
    check: bool = False,
    suppress_errors: bool = False,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    encoding: str = "utf-8",
    max_output_bytes: int | None = None,
    original_expression: str | None = None,
) -> CommandResult:
    """Asynchronously execute a shell command using asyncio."""
    cwd_str = str(cwd) if cwd is not None else None
    env_dict = dict(env) if env is not None else None

    start = time.perf_counter()
    if original_expression is None:
        original_expression = _current_expression.get()
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE if capture else None,
        stderr=asyncio.subprocess.PIPE if capture else None,
        cwd=cwd_str,
        env=env_dict,
    )
    try:
        if timeout is not None:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        else:
            stdout_b, stderr_b = await proc.communicate()
    except asyncio.TimeoutError as e:
        try:
            if sys.platform == "win32" and proc.pid:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                proc.kill()
            await asyncio.wait_for(proc.communicate(), timeout=1)
        except Exception:
            pass
        duration = time.perf_counter() - start
        result = CommandResult(
            command=command,
            stdout="",
            stderr="",
            exit_code=-1,
            duration=duration,
            original_expression=original_expression,
        )
        raise CommandTimeoutError(result) from e

    duration = time.perf_counter() - start

    raw_stdout = stdout_b.decode(encoding, errors="replace") if stdout_b else ""
    raw_stderr = stderr_b.decode(encoding, errors="replace") if stderr_b else ""

    stdout, out_trunc = _truncate_output(raw_stdout, max_output_bytes, encoding)
    stderr, err_trunc = _truncate_output(raw_stderr, max_output_bytes, encoding)

    result = CommandResult(
        command=command,
        stdout=stdout,
        stderr=stderr,
        exit_code=proc.returncode if proc.returncode is not None else 0,
        duration=duration,
        original_expression=original_expression,
        truncated=(out_trunc or err_trunc),
    )

    if check and not suppress_errors and result.exit_code != 0:
        raise CommandError(result)

    return result


@contextmanager
def cd(path: str | Path) -> Iterator[Path]:
    """Context manager for safely and temporarily changing the current working directory."""
    prev_cwd = Path.cwd()
    target_path = Path(path).resolve()
    os.chdir(target_path)
    try:
        yield target_path
    finally:
        os.chdir(prev_cwd)


@contextmanager
def env(**kwargs: Any) -> Iterator[dict[str, str]]:
    """Context manager for temporarily setting or overriding environment variables."""
    old_env: dict[str, str | None] = {}
    for k, v in kwargs.items():
        old_env[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = str(v)
    try:
        yield dict(os.environ)
    finally:
        for k, old_val in old_env.items():
            if old_val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old_val
