import sys
import pytest
from pycli.runtime import CommandError, CommandResult, DynamicObj, run, run_expanded, wrap_json


def test_command_result_properties():
    res = CommandResult(
        command="echo hello",
        stdout="hello\n",
        stderr="",
        exit_code=0,
        duration=0.05,
    )
    assert res.command == "echo hello"
    assert res.stdout == "hello\n"
    assert res.stderr == ""
    assert res.exit_code == 0
    assert res.duration == 0.05
    assert bool(res) is True
    assert str(res) == "hello\n"


def test_command_result_truthiness_fail():
    res = CommandResult(
        command="false",
        stdout="",
        stderr="error",
        exit_code=1,
        duration=0.01,
    )
    assert bool(res) is False


def test_dynamic_obj_attribute_and_item_access():
    data = {
        "name": "vm01",
        "size": "Standard_D2s_v5",
        "nested": {"id": 123},
        "tags": [{"key": "env", "val": "prod"}],
    }
    obj = DynamicObj(data)

    assert obj.name == "vm01"
    assert obj.size == "Standard_D2s_v5"
    assert obj["name"] == "vm01"
    assert obj.nested.id == 123
    assert obj.tags[0].key == "env"
    assert obj.tags[0].val == "prod"
    assert "name" in obj
    assert len(obj) == 4
    assert obj.get("nonexistent", "default") == "default"


def test_command_result_json():
    json_text = '{"name": "vm01", "items": [{"id": 1}, {"id": 2}]}'
    res = CommandResult(
        command="az vm list",
        stdout=json_text,
        stderr="",
        exit_code=0,
        duration=0.1,
    )
    assert res.json.name == "vm01"
    assert res.json.items[0].id == 1
    assert res.json.items[1].id == 2


def test_command_result_json_list():
    json_text = '[{"id": 10}, {"id": 20}]'
    res = CommandResult(
        command="az vm list",
        stdout=json_text,
        stderr="",
        exit_code=0,
        duration=0.1,
    )
    assert len(res.json) == 2
    assert res.json[0].id == 10
    assert res.json[1].id == 20


def test_run_command_execution():
    res = run([sys.executable, "-c", "print('hello pycli')"])
    assert res.exit_code == 0
    assert "hello pycli" in res.stdout
    assert bool(res) is True


def test_run_command_strict_mode():
    with pytest.raises(CommandError) as exc_info:
        run([sys.executable, "-c", "import sys; sys.exit(2)"], check=True)
    assert exc_info.value.result.exit_code == 2


def test_run_expanded():
    res = run_expanded(sys.executable, "-c", "import sys; print(sys.argv[1:])", "arg1", ["arg2", "arg3"])
    assert res.exit_code == 0
    assert "arg1" in res.stdout
    assert "arg2" in res.stdout
    assert "arg3" in res.stdout


def test_run_capture_false(capfd):
    res = run([sys.executable, "-c", "print('streaming to terminal')"], capture=False)
    assert res.exit_code == 0
    assert res.stdout == ""
    captured = capfd.readouterr()
    assert "streaming to terminal" in captured.out


def test_command_result_yaml(monkeypatch):
    yaml_text = "service: frontend\nreplicas: 3\nports:\n  - 80\n  - 443\n"
    res = CommandResult(
        command="kubectl get svc",
        stdout=yaml_text,
        stderr="",
        exit_code=0,
        duration=0.1,
    )
    # If pyyaml is not installed, it should raise informative ImportError
    try:
        import yaml
        assert res.yaml.service == "frontend"
        assert res.yaml.replicas == 3
        assert res.yaml.ports == [80, 443]
    except ImportError:
        with pytest.raises(ImportError, match="YAML parsing requires the 'pyyaml' package"):
            _ = res.yaml
