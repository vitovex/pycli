from pycli.parser import (
    CommandParser,
    InterpolationNode,
    PipelineNode,
    RedirectionNode,
    SplatNode,
    SubcommandNode,
    WordNode,
)


def test_parse_simple_command():
    parser = CommandParser("git status")
    node = parser.parse()

    assert len(node.pipeline.commands) == 1
    cmd = node.pipeline.commands[0]
    assert len(cmd.parts) == 2
    assert isinstance(cmd.parts[0], WordNode)
    assert cmd.parts[0].value == "git"
    assert isinstance(cmd.parts[1], WordNode)
    assert cmd.parts[1].value == "status"
    assert node.strict is False


def test_parse_pipeline():
    parser = CommandParser("kubectl get pods | grep api")
    node = parser.parse()

    assert len(node.pipeline.commands) == 2
    cmd1 = node.pipeline.commands[0]
    cmd2 = node.pipeline.commands[1]
    assert [p.value for p in cmd1.parts if isinstance(p, WordNode)] == ["kubectl", "get", "pods"]
    assert [p.value for p in cmd2.parts if isinstance(p, WordNode)] == ["grep", "api"]


def test_parse_interpolation():
    parser = CommandParser("az account set --subscription {subscription}")
    node = parser.parse()

    cmd = node.pipeline.commands[0]
    interpolations = [p for p in cmd.parts if isinstance(p, InterpolationNode)]
    assert len(interpolations) == 1
    assert interpolations[0].expression == "subscription"


def test_parse_splat():
    parser = CommandParser("rm {*files}", strict=True)
    node = parser.parse()

    cmd = node.pipeline.commands[0]
    assert node.strict is True
    assert cmd.has_splat() is True
    splats = [p for p in cmd.parts if isinstance(p, SplatNode)]
    assert len(splats) == 1
    assert splats[0].expression == "files"


def test_parse_redirection():
    parser = CommandParser("git status > status.txt")
    node = parser.parse()

    cmd = node.pipeline.commands[0]
    redirs = [p for p in cmd.parts if isinstance(p, RedirectionNode)]
    assert len(redirs) == 1
    assert redirs[0].operator == ">"
    assert redirs[0].target == "status.txt"


def test_parse_subcommand():
    parser = CommandParser("echo $(git branch --show-current)")
    node = parser.parse()

    cmd = node.pipeline.commands[0]
    subcmds = [p for p in cmd.parts if isinstance(p, SubcommandNode)]
    assert len(subcmds) == 1
    sub_pipeline = subcmds[0].pipeline
    assert len(sub_pipeline.commands) == 1
    sub_cmd = sub_pipeline.commands[0]
    assert [p.value for p in sub_cmd.parts if isinstance(p, WordNode)] == ["git", "branch", "--show-current"]
