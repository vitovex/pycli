"""Runtime support for pycli: run(), run_expanded(), run_bg(), async_run(), CommandResult, cd(), env()."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
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


def shell_quote(val: Any, in_double_quotes: bool = False) -> str:
    """Quote a value for safe shell interpolation, respecting context and platform."""
    s = str(val)
    if in_double_quotes:
        if sys.platform == "win32":
            # Inside cmd.exe double quotes, escape internal " as \" and % as %%
            return s.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
        else:
            # Inside POSIX double quotes, escape ", \, $, `
            return s.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    else:
        if sys.platform == "win32":
            # Safe characters for cmd.exe unquoted arguments: strictly alphanumeric, '-', '_', '.'
            if s and all(c.isalnum() or c in "-_." for c in s):
                return s
            quoted = subprocess.list2cmdline([s])
            if not (quoted.startswith('"') and quoted.endswith('"')):
                quoted = f'"{quoted}"'
            return quoted.replace("%", "%%")
        else:
            return shlex.quote(s)


class ShellOp(str):
    """Represents an explicit shell operator in command parts."""
    pass


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

    def __dir__(self) -> list[str]:
        attrs = set(super().__dir__())
        attrs.update(str(k) for k in self._data.keys() if str(k).isidentifier())
        return sorted(attrs)

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
        upstream_procs: list[subprocess.Popen] | None = None,
    ) -> None:
        self.proc = proc
        self.command = command
        self.start_time = start_time
        self.encoding = encoding
        self.original_expression = original_expression
        self.upstream_procs = upstream_procs or []
        self._result: CommandResult | None = None
        self._upstream_stderr_chunks: list[list[str]] = [[] for _ in self.upstream_procs]
        self._reader_threads: list[threading.Thread] = []

        def _drain_stream(stream: Any, chunks: list[str]) -> None:
            try:
                for chunk in iter(lambda: stream.read(8192), ""):
                    chunks.append(chunk)
            except Exception:
                pass
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

        for p_idx, p in enumerate(self.upstream_procs):
            if p.stderr is not None:
                t = threading.Thread(
                    target=_drain_stream,
                    args=(p.stderr, self._upstream_stderr_chunks[p_idx]),
                    daemon=True,
                )
                t.start()
                self._reader_threads.append(t)

    @property
    def is_running(self) -> bool:
        """Check if process is still running."""
        return self.proc.poll() is None

    def poll(self) -> int | None:
        """Return exit code if terminated, otherwise None."""
        return self.proc.poll()

    def kill(self) -> None:
        """Terminate the process and any upstream processes in the pipeline."""
        for p in self.upstream_procs:
            _kill_process_tree(p)
        _kill_process_tree(self.proc)

    def wait(self, timeout: float | None = None) -> CommandResult:
        """Wait for command to finish and return CommandResult."""
        if self._result is not None:
            return self._result

        deadline = (time.perf_counter() + timeout) if timeout is not None else None

        def _remaining_timeout() -> float | None:
            if deadline is None:
                return None
            return max(0.0, deadline - time.perf_counter())

        try:
            stdout, stderr = self.proc.communicate(timeout=_remaining_timeout())
        except subprocess.TimeoutExpired as e:
            for p in self.upstream_procs:
                _kill_process_tree(p)
            _kill_process_tree(self.proc)
            for t in self._reader_threads:
                t.join(timeout=0.2)
            try:
                stdout, stderr = self.proc.communicate(timeout=0.5)
            except Exception:
                stdout, stderr = "", ""
            duration = time.perf_counter() - self.start_time
            upstream_err = "".join("".join(c) for c in self._upstream_stderr_chunks)
            total_stderr = (upstream_err + (stderr or "")) if upstream_err else (stderr or "")
            self._result = CommandResult(
                command=self.command,
                stdout=stdout or "",
                stderr=total_stderr,
                exit_code=-1,
                duration=duration,
                original_expression=self.original_expression,
            )
            raise CommandTimeoutError(self._result) from e
        except KeyboardInterrupt:
            try:
                for p in self.upstream_procs:
                    _kill_process_tree(p)
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

        for p in self.upstream_procs:
            try:
                rem = _remaining_timeout()
                p.wait(timeout=rem if rem is not None else 2.0)
            except Exception:
                _kill_process_tree(p)

        for t in self._reader_threads:
            t.join(timeout=0.5)

        duration = time.perf_counter() - self.start_time
        upstream_err = "".join("".join(c) for c in self._upstream_stderr_chunks)
        total_stderr = (upstream_err + (stderr or "")) if upstream_err else (stderr or "")

        self._result = CommandResult(
            command=self.command,
            stdout=stdout or "",
            stderr=total_stderr,
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


@dataclass
class _PipelineStage:
    argv: list[str]
    stdin_file: str | None = None
    stdout_file: str | None = None
    stdout_mode: str = "w"


def _parse_stages_from_parts(flat_parts: list[Any]) -> list[_PipelineStage]:
    raw_stages: list[list[Any]] = []
    current_raw: list[Any] = []
    for p in flat_parts:
        if isinstance(p, ShellOp) and str(p).strip() == "|":
            raw_stages.append(current_raw)
            current_raw = []
        else:
            current_raw.append(p)
    raw_stages.append(current_raw)

    stages: list[_PipelineStage] = []
    for raw in raw_stages:
        argv: list[str] = []
        stdin_file: str | None = None
        stdout_file: str | None = None
        stdout_mode = "w"
        i = 0
        n = len(raw)
        while i < n:
            item = raw[i]
            if isinstance(item, ShellOp):
                op_str = str(item).strip()
                if op_str == ">":
                    if i + 1 < n:
                        stdout_file = str(raw[i + 1])
                        stdout_mode = "w"
                        i += 2
                        continue
                elif op_str == ">>":
                    if i + 1 < n:
                        stdout_file = str(raw[i + 1])
                        stdout_mode = "a"
                        i += 2
                        continue
                elif op_str == "<":
                    if i + 1 < n:
                        stdin_file = str(raw[i + 1])
                        i += 2
                        continue
                elif op_str.startswith(">>"):
                    stdout_file = op_str[2:].strip()
                    stdout_mode = "a"
                    i += 1
                    continue
                elif op_str.startswith(">"):
                    stdout_file = op_str[1:].strip()
                    stdout_mode = "w"
                    i += 1
                    continue
                elif op_str.startswith("<"):
                    stdin_file = op_str[1:].strip()
                    i += 1
                    continue
                else:
                    argv.append(str(item))
                    i += 1
                    continue
            else:
                argv.append(str(item))
                i += 1
        stages.append(
            _PipelineStage(
                argv=argv,
                stdin_file=stdin_file,
                stdout_file=stdout_file,
                stdout_mode=stdout_mode,
            )
        )
    return stages


def _execute_pipeline_stages(
    stages: list[_PipelineStage],
    *,
    flat_parts: list[Any],
    capture: bool,
    check: bool,
    tee: bool,
    input: str | bytes | None,
    suppress_errors: bool,
    cwd: str | Path | None,
    env: Mapping[str, str] | None,
    timeout: float | None,
    encoding: str,
    max_output_bytes: int | None,
    original_expression: str | None,
) -> CommandResult:
    opened_files: list[Any] = []
    processes: list[subprocess.Popen] = []
    start = time.perf_counter()
    full_cmd_str = " ".join(str(p) for p in flat_parts)

    cwd_str = str(cwd) if cwd is not None else None
    env_dict = dict(env) if env is not None else None

    input_text = None
    if input is not None:
        if isinstance(input, bytes):
            input_text = input.decode(encoding, errors="replace")
        else:
            input_text = str(input)

    feeder_threads: list[threading.Thread] = []

    try:
        prev_stdout: Any = None

        for idx, stage in enumerate(stages):
            is_first = (idx == 0)
            is_last = (idx == len(stages) - 1)

            # Determine stage_stdin
            if is_first:
                if stage.stdin_file:
                    f_in = open(stage.stdin_file, "r", encoding=encoding, errors="replace")
                    opened_files.append(f_in)
                    stage_stdin = f_in
                elif input_text is not None:
                    stage_stdin = subprocess.PIPE
                else:
                    stage_stdin = None
            else:
                stage_stdin = prev_stdout

            # Determine stage_stdout
            if is_last:
                if stage.stdout_file:
                    f_out = open(stage.stdout_file, stage.stdout_mode, encoding=encoding, errors="replace")
                    opened_files.append(f_out)
                    stage_stdout = f_out
                elif capture or tee:
                    stage_stdout = subprocess.PIPE
                else:
                    stage_stdout = None
            else:
                stage_stdout = subprocess.PIPE

            # Handle portable echo stage vs external process
            if stage.argv and stage.argv[0] == "echo":
                echo_text = " ".join(stage.argv[1:]) + "\n"
                if is_last:
                    if stage.stdout_file:
                        f_out.write(echo_text)
                        f_out.flush()
                        proc_out = ""
                    else:
                        proc_out = echo_text
                    duration = time.perf_counter() - start
                    if tee and proc_out:
                        sys.stdout.write(proc_out)
                        sys.stdout.flush()
                    elif not capture and proc_out:
                        sys.stdout.write(proc_out)
                        sys.stdout.flush()
                        proc_out = ""
                    stdout, out_trunc = _truncate_output(proc_out, max_output_bytes, encoding)
                    res = CommandResult(
                        command=full_cmd_str,
                        stdout=stdout,
                        stderr="",
                        exit_code=0,
                        duration=duration,
                        original_expression=original_expression,
                        truncated=out_trunc,
                    )
                    return res
                else:
                    r_fd, w_fd = os.pipe()
                    echo_bytes = echo_text.encode(encoding)

                    def _feed_echo(fd: int, data: bytes) -> None:
                        try:
                            with os.fdopen(fd, "wb") as f:
                                f.write(data)
                        except (BrokenPipeError, OSError):
                            pass

                    t_feeder = threading.Thread(
                        target=_feed_echo, args=(w_fd, echo_bytes), daemon=True
                    )
                    t_feeder.start()
                    feeder_threads.append(t_feeder)
                    prev_stdout = r_fd
                    continue
            else:
                proc = subprocess.Popen(
                    stage.argv,
                    shell=False,
                    stdin=stage_stdin,
                    stdout=stage_stdout,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding=encoding,
                    errors="replace",
                    cwd=cwd_str,
                    env=env_dict,
                )
                processes.append(proc)
                if prev_stdout is not None:
                    if isinstance(prev_stdout, int):
                        try:
                            os.close(prev_stdout)
                        except OSError:
                            pass
                    elif hasattr(prev_stdout, "close"):
                        prev_stdout.close()
                prev_stdout = proc.stdout

        reader_threads: list[threading.Thread] = []
        stderr_chunks: list[list[str]] = [[] for _ in processes]
        stdout_chunks: list[str] = []

        def _drain_stream(stream: Any, chunks: list[str]) -> None:
            try:
                for chunk in iter(lambda: stream.read(8192), ""):
                    chunks.append(chunk)
            except Exception:
                pass
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

        # Concurrently drain stderr of all pipeline processes
        for p_idx, p in enumerate(processes):
            if p.stderr is not None:
                t_err = threading.Thread(
                    target=_drain_stream, args=(p.stderr, stderr_chunks[p_idx]), daemon=True
                )
                t_err.start()
                reader_threads.append(t_err)

        # Concurrently drain stdout of the final process if piped
        if processes and processes[-1].stdout is not None:
            t_out = threading.Thread(
                target=_drain_stream, args=(processes[-1].stdout, stdout_chunks), daemon=True
            )
            t_out.start()
            reader_threads.append(t_out)

        # Feed input to first stage if needed
        if input_text is not None and processes and processes[0].stdin is not None:
            def _feed_stdin(stream: Any, text: str) -> None:
                try:
                    stream.write(text)
                    stream.close()
                except (BrokenPipeError, OSError):
                    pass

            t_in = threading.Thread(
                target=_feed_stdin, args=(processes[0].stdin, input_text), daemon=True
            )
            t_in.start()
            reader_threads.append(t_in)

        deadline = (time.perf_counter() + timeout) if timeout is not None else None

        def _remaining_timeout() -> float | None:
            if deadline is None:
                return None
            rem = deadline - time.perf_counter()
            return max(0.0, rem)

        if processes:
            last_proc = processes[-1]
            try:
                rem = _remaining_timeout()
                last_proc.wait(timeout=rem)
            except subprocess.TimeoutExpired as e:
                for p in processes:
                    _kill_process_tree(p)
                for t in reader_threads:
                    t.join(timeout=0.5)
                for t in feeder_threads:
                    t.join(timeout=0.5)
                duration = time.perf_counter() - start
                res = CommandResult(
                    command=full_cmd_str,
                    stdout="",
                    stderr="",
                    exit_code=-1,
                    duration=duration,
                    original_expression=original_expression,
                )
                raise CommandTimeoutError(res) from e
            except KeyboardInterrupt:
                for p in processes:
                    if sys.platform == "win32":
                        _kill_process_tree(p)
                    else:
                        try:
                            p.send_signal(signal.SIGINT)
                            p.wait(timeout=2)
                        except Exception:
                            _kill_process_tree(p)
                for t in reader_threads:
                    t.join(timeout=0.5)
                for t in feeder_threads:
                    t.join(timeout=0.5)
                raise

            for p in processes[:-1]:
                try:
                    rem = _remaining_timeout()
                    p.wait(timeout=rem if rem is not None else 2)
                except subprocess.TimeoutExpired as e:
                    for proc_to_kill in processes:
                        _kill_process_tree(proc_to_kill)
                    for t in reader_threads:
                        t.join(timeout=0.5)
                    for t in feeder_threads:
                        t.join(timeout=0.5)
                    duration = time.perf_counter() - start
                    res = CommandResult(
                        command=full_cmd_str,
                        stdout="",
                        stderr="",
                        exit_code=-1,
                        duration=duration,
                        original_expression=original_expression,
                    )
                    raise CommandTimeoutError(res) from e
                except Exception:
                    _kill_process_tree(p)

            for t in reader_threads:
                t.join(timeout=2)
            for t in feeder_threads:
                t.join(timeout=2)

            exit_code = last_proc.returncode if last_proc.returncode is not None else 0
            raw_stdout = "".join(stdout_chunks)
            raw_stderr = "".join("".join(chunks) for chunks in stderr_chunks)
        else:
            exit_code = 0
            raw_stdout = ""
            raw_stderr = ""

        duration = time.perf_counter() - start
        stdout, out_trunc = _truncate_output(raw_stdout or "", max_output_bytes, encoding)
        stderr, err_trunc = _truncate_output(raw_stderr or "", max_output_bytes, encoding)

        if tee and stdout:
            sys.stdout.write(stdout)
            sys.stdout.flush()
        elif not capture and stdout:
            sys.stdout.write(stdout)
            sys.stdout.flush()
            stdout = ""

        res = CommandResult(
            command=full_cmd_str,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration=duration,
            original_expression=original_expression,
            truncated=(out_trunc or err_trunc),
        )

        if check and not suppress_errors and res.exit_code != 0:
            raise CommandError(res)

        return res

    finally:
        for f in opened_files:
            try:
                f.close()
            except Exception:
                pass


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
    flat_parts: list[Any] = []
    for part in parts:
        if isinstance(part, (list, tuple, set)):
            for item in part:
                flat_parts.append(item)
        else:
            flat_parts.append(part)

    has_operator = any(isinstance(p, ShellOp) for p in flat_parts)

    if shell is False and has_operator:
        raise ValueError(
            "run_expanded with shell=False cannot accept typed ShellOp operators "
            "(pipeline or redirection). Execute without shell=False or omit shell parameter."
        )

    if shell is True:
        cmd_tokens: list[str] = []
        for p in flat_parts:
            if isinstance(p, ShellOp):
                cmd_tokens.append(str(p))
            else:
                cmd_tokens.append(shell_quote(p))
        command_str = " ".join(cmd_tokens)
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

    stages = _parse_stages_from_parts(flat_parts)

    # Single stage without redirection: execute directly with shell=False
    if len(stages) == 1 and stages[0].stdin_file is None and stages[0].stdout_file is None:
        stage = stages[0]
        if not stage.argv:
            return CommandResult(
                command="",
                stdout="",
                stderr="",
                exit_code=0,
                duration=0.0,
                original_expression=original_expression,
            )
        if stage.argv[0] == "echo":
            start = time.perf_counter()
            echo_text = " ".join(stage.argv[1:]) + "\n"
            duration = time.perf_counter() - start
            if tee:
                sys.stdout.write(echo_text)
                sys.stdout.flush()
                proc_out = echo_text
            elif not capture:
                sys.stdout.write(echo_text)
                sys.stdout.flush()
                proc_out = ""
            else:
                proc_out = echo_text
            stdout, out_trunc = _truncate_output(proc_out, max_output_bytes, encoding)
            return CommandResult(
                command=" ".join(stage.argv),
                stdout=stdout,
                stderr="",
                exit_code=0,
                duration=duration,
                original_expression=original_expression,
                truncated=out_trunc,
            )

        return run(
            stage.argv,
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

    # Multi-stage pipeline or redirected execution
    return _execute_pipeline_stages(
        stages,
        flat_parts=flat_parts,
        capture=capture,
        check=check,
        tee=tee,
        input=input,
        suppress_errors=suppress_errors,
        cwd=cwd,
        env=env,
        timeout=timeout,
        encoding=encoding,
        max_output_bytes=max_output_bytes,
        original_expression=original_expression,
    )


def run_bg(
    *parts: Any,
    shell: bool | None = None,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    encoding: str = "utf-8",
    original_expression: str | None = None,
) -> BackgroundJob:
    """Launch a non-blocking background command and return a BackgroundJob."""
    cwd_str = str(cwd) if cwd is not None else None
    env_dict = dict(env) if env is not None else None
    start = time.perf_counter()
    if original_expression is None:
        original_expression = _current_expression.get()

    if len(parts) == 1 and isinstance(parts[0], str):
        command = parts[0]
        if shell is None:
            shell = True
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
            command,
            start,
            encoding=encoding,
            original_expression=original_expression,
        )

    if len(parts) == 1 and isinstance(parts[0], (list, tuple, Sequence)) and not isinstance(parts[0], (str, bytes)):
        command = list(parts[0])
        if shell is None:
            shell = False
        cmd_str = " ".join(str(c) for c in command)
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

    # Flatten parts
    flat_parts: list[Any] = []
    for part in parts:
        if isinstance(part, (list, tuple, set)):
            for item in part:
                flat_parts.append(item)
        else:
            flat_parts.append(part)

    stages = _parse_stages_from_parts(flat_parts)
    if not stages:
        proc = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            shell=False,
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
            "",
            start,
            encoding=encoding,
            original_expression=original_expression,
        )

    # Multi-stage or single stage with redirections
    procs: list[subprocess.Popen] = []
    opened_files: list[Any] = []
    prev_stdout = None
    for idx, stage in enumerate(stages):
        is_first = (idx == 0)
        is_last = (idx == len(stages) - 1)

        # stdin
        if is_first:
            if stage.stdin_file:
                cur_stdin = open(stage.stdin_file, "r", encoding=encoding)
                opened_files.append(cur_stdin)
            else:
                cur_stdin = None
        else:
            cur_stdin = prev_stdout

        # stdout
        if is_last:
            if stage.stdout_file:
                cur_stdout = open(stage.stdout_file, stage.stdout_mode, encoding=encoding)
                opened_files.append(cur_stdout)
            else:
                cur_stdout = subprocess.PIPE
        else:
            cur_stdout = subprocess.PIPE

        cmd_to_run = (
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.write(' '.join(sys.argv[1:]) + '\\n')",
            ] + stage.argv[1:]
            if (stage.argv and stage.argv[0] == "echo")
            else stage.argv
        )

        p = subprocess.Popen(
            cmd_to_run,
            shell=False,
            stdin=cur_stdin,
            stdout=cur_stdout,
            stderr=subprocess.PIPE,
            text=True,
            encoding=encoding,
            errors="replace",
            cwd=cwd_str,
            env=env_dict,
        )
        procs.append(p)
        if prev_stdout is not None and hasattr(prev_stdout, "close"):
            prev_stdout.close()
        prev_stdout = p.stdout

    for f in opened_files:
        try:
            f.close()
        except Exception:
            pass

    cmd_tokens = []
    for p in flat_parts:
        if isinstance(p, ShellOp):
            cmd_tokens.append(str(p))
        else:
            cmd_tokens.append(str(p))
    cmd_str = " ".join(cmd_tokens)

    return BackgroundJob(
        procs[-1],
        cmd_str,
        start,
        encoding=encoding,
        original_expression=original_expression,
        upstream_procs=procs[:-1],
    )


run_bg_expanded = run_bg


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


async def _cleanup_async_proc(proc: asyncio.subprocess.Process) -> None:
    """Terminate async process and its process tree, ensuring event loop wait completes."""
    if proc.returncode is not None:
        return
    try:
        if sys.platform == "win32" and proc.pid:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGKILL)
            except Exception:
                proc.kill()
    except ProcessLookupError:
        pass
    except Exception as cleanup_err:
        warnings.warn(f"Process cleanup warning: {cleanup_err}", RuntimeWarning)

    try:
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        warnings.warn("Timed out waiting for process termination in async_run", RuntimeWarning)
    except Exception as wait_err:
        warnings.warn(f"Error waiting for process in async_run: {wait_err}", RuntimeWarning)

    # Allow a tick for pending transport callbacks to execute before event loop teardown
    await asyncio.sleep(0.01)


async def async_run(
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
    """Asynchronously execute a shell or structured command using asyncio."""
    cwd_str = str(cwd) if cwd is not None else None
    env_dict = dict(env) if env is not None else None
    start = time.perf_counter()
    if original_expression is None:
        original_expression = _current_expression.get()

    if len(parts) == 1 and isinstance(parts[0], str):
        command = parts[0]
        if shell is None:
            shell = True
        cmd_str = command
    elif len(parts) == 1 and isinstance(parts[0], (list, tuple, Sequence)) and not isinstance(parts[0], (str, bytes)):
        command = list(parts[0])
        if shell is None:
            shell = False
        cmd_str = " ".join(str(c) for c in command)
    else:
        flat_parts: list[Any] = []
        for part in parts:
            if isinstance(part, (list, tuple, set)):
                for item in part:
                    flat_parts.append(item)
            else:
                flat_parts.append(part)

        stages = _parse_stages_from_parts(flat_parts)
        if not stages:
            return CommandResult(
                command="",
                stdout="",
                stderr="",
                exit_code=0,
                duration=0.0,
                original_expression=original_expression,
            )

        if len(stages) == 1 and stages[0].stdin_file is None and stages[0].stdout_file is None:
            stage = stages[0]
            if stage.argv and stage.argv[0] == "echo":
                duration = time.perf_counter() - start
                echo_text = " ".join(stage.argv[1:]) + "\n"
                if tee:
                    sys.stdout.write(echo_text)
                    sys.stdout.flush()
                    proc_out = echo_text
                elif not capture:
                    sys.stdout.write(echo_text)
                    sys.stdout.flush()
                    proc_out = ""
                else:
                    proc_out = echo_text
                stdout, out_trunc = _truncate_output(proc_out, max_output_bytes, encoding)
                return CommandResult(
                    command=" ".join(stage.argv),
                    stdout=stdout,
                    stderr="",
                    exit_code=0,
                    duration=duration,
                    original_expression=original_expression,
                    truncated=out_trunc,
                )
            command = stage.argv
            cmd_str = " ".join(stage.argv)
            if shell is None:
                shell = False
        else:
            return await asyncio.to_thread(
                _execute_pipeline_stages,
                stages,
                flat_parts=flat_parts,
                capture=capture,
                check=check,
                tee=tee,
                input=input,
                suppress_errors=suppress_errors,
                cwd=cwd,
                env=env,
                timeout=timeout,
                encoding=encoding,
                max_output_bytes=max_output_bytes,
                original_expression=original_expression,
            )

    extra_kwargs: dict[str, Any] = {}
    if sys.platform != "win32":
        extra_kwargs["start_new_session"] = True

    stdin_mode = asyncio.subprocess.PIPE if input is not None else None
    stdout_mode = asyncio.subprocess.PIPE if (capture or tee) else None
    stderr_mode = asyncio.subprocess.PIPE if (capture or tee) else None

    if shell:
        proc = await asyncio.create_subprocess_shell(
            cmd_str,
            stdin=stdin_mode,
            stdout=stdout_mode,
            stderr=stderr_mode,
            cwd=cwd_str,
            env=env_dict,
            **extra_kwargs,
        )
    else:
        cmd_args = [str(c) for c in command] if not isinstance(command, str) else [command]
        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            stdin=stdin_mode,
            stdout=stdout_mode,
            stderr=stderr_mode,
            cwd=cwd_str,
            env=env_dict,
            **extra_kwargs,
        )

    input_b = None
    if input is not None:
        if isinstance(input, str):
            input_b = input.encode(encoding)
        elif isinstance(input, (bytes, bytearray)):
            input_b = bytes(input)
        else:
            input_b = str(input).encode(encoding)

    try:
        if timeout is not None:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=input_b), timeout=timeout
            )
        else:
            stdout_b, stderr_b = await proc.communicate(input=input_b)
    except asyncio.TimeoutError as e:
        await _cleanup_async_proc(proc)
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
    except asyncio.CancelledError:
        await _cleanup_async_proc(proc)
        raise

    duration = time.perf_counter() - start

    raw_stdout = stdout_b.decode(encoding, errors="replace") if stdout_b else ""
    raw_stderr = stderr_b.decode(encoding, errors="replace") if stderr_b else ""

    if tee:
        if raw_stdout:
            sys.stdout.write(raw_stdout)
            sys.stdout.flush()
        if raw_stderr:
            sys.stderr.write(raw_stderr)
            sys.stderr.flush()
        stdout = raw_stdout
    elif not capture:
        if raw_stdout:
            sys.stdout.write(raw_stdout)
            sys.stdout.flush()
        if raw_stderr:
            sys.stderr.write(raw_stderr)
            sys.stderr.flush()
        stdout = ""
    else:
        stdout = raw_stdout

    stdout, out_trunc = _truncate_output(stdout, max_output_bytes, encoding)
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

    if check and not suppress_errors and result.exit_code != 0:
        raise CommandError(result)

    return result


async_run_expanded = async_run


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
