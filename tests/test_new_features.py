"""Tests for high-impact features: .table, .retry(), .until(), .stream, and dry_run."""

from __future__ import annotations

import os
import sys
import pytest
from pycli.runtime import (
    CommandError,
    CommandResult,
    CommandStream,
    dry_run,
    parse_table,
    run,
    run_expanded,
)
from pycli.transformer import transpile


def test_command_result_table_docker_ps():
    stdout = (
        "CONTAINER ID   IMAGE          COMMAND                  CREATED         STATUS         PORTS     NAMES\n"
        "a1b2c3d4e5f6   nginx:alpine   \"/docker-entrypoint...\"  2 minutes ago   Up 2 minutes   80/tcp    my-nginx\n"
        "f6e5d4c3b2a1   redis:7        \"docker-entrypoint...\"   10 hours ago    Up 10 hours    6379/tcp  my-redis\n"
    )
    res = CommandResult("docker ps", stdout, "", 0, 0.1)
    table = res.table

    assert len(table) == 2
    assert table[0].container_id == "a1b2c3d4e5f6"
    assert table[0].image == "nginx:alpine"
    assert table[0].created == "2 minutes ago"
    assert table[0].status == "Up 2 minutes"
    assert table[0].ports == "80/tcp"
    assert table[0].names == "my-nginx"

    assert table[1].container_id == "f6e5d4c3b2a1"
    assert table[1].image == "redis:7"
    assert table[1].status == "Up 10 hours"
    assert table[1]["STATUS"] == "Up 10 hours"


def test_command_result_table_kubectl_get_pods():
    stdout = (
        "NAME                     READY   STATUS    RESTARTS   AGE\n"
        "web-server-7df4f-1       1/1     Running   0          10m\n"
        "worker-backend-8bf       2/2     Pending   1          5m\n"
    )
    res = CommandResult("kubectl get pods", stdout, "", 0, 0.1)
    table = res.table

    assert len(table) == 2
    assert table[0].name == "web-server-7df4f-1"
    assert table[0].ready == "1/1"
    assert table[0].status == "Running"
    assert table[0].restarts == "0"
    assert table[0].age == "10m"

    assert table[1].name == "worker-backend-8bf"
    assert table[1].status == "Pending"


def test_command_result_table_tab_delimited():
    stdout = "col1\tcol2\tcol3\nval1\tval2\tval3\n"
    res = CommandResult("dummy", stdout, "", 0, 0.1)
    assert len(res.table) == 1
    assert res.table[0].col1 == "val1"
    assert res.table[0].col2 == "val2"
    assert res.table[0].col3 == "val3"


def test_command_result_table_empty():
    res = CommandResult("dummy", "", "", 0, 0.1)
    assert res.table == []


def test_command_result_retry_success():
    call_count = 0

    def mock_rerun():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            return CommandResult("flaky", "", "error", 1, 0.01, rerun_fn=mock_rerun)
        return CommandResult("flaky", "ok", "", 0, 0.01, rerun_fn=mock_rerun)

    initial = mock_rerun()
    assert initial.exit_code == 1

    final_res = initial.retry(attempts=3, delay=0.01)
    assert final_res.exit_code == 0
    assert final_res.stdout == "ok"
    assert call_count == 2


def test_command_result_retry_failure_exhaustion():
    def mock_fail():
        return CommandResult("always_fail", "", "failed", 1, 0.01, rerun_fn=mock_fail)

    initial = mock_fail()
    res = initial.retry(attempts=2, delay=0.01)
    assert res.exit_code == 1

    with pytest.raises(CommandError):
        initial.retry(attempts=2, delay=0.01, check=True)


def test_command_result_until_predicate():
    step = 0

    def mock_step():
        nonlocal step
        step += 1
        return CommandResult("poll", f"step_{step}", "", 0, 0.01, rerun_fn=mock_step)

    initial = mock_step()
    res = initial.until(lambda r: r.stdout.strip() == "step_3", timeout=2.0, delay=0.01)
    assert res.stdout.strip() == "step_3"


def test_command_stream_live_iteration():
    cmd = [sys.executable, "-c", "import sys; sys.stdout.write('line1\\nline2\\nline3\\n')"]
    res = run(cmd, stream=True)
    assert isinstance(res, CommandStream)

    lines = list(res)
    assert lines == ["line1", "line2", "line3"]
    assert res.exit_code == 0


def test_stream_transpilation():
    code = "for line in $(kubectl logs pod).stream:\n    print(line)"
    transpiled = transpile(code)
    assert "stream=True" in transpiled


def test_dry_run_context_manager():
    with dry_run():
        res = run("rm -rf /some/dir/that/does/not/exist")
        assert res.exit_code == 0
        assert res.command == "rm -rf /some/dir/that/does/not/exist"
        assert res.stdout == ""

        res_exp = run_expanded("rm", "-rf", "/other/dir")
        assert res_exp.exit_code == 0
