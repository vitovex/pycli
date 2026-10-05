import pytest

from pycli.parser import (
    CommandParser,
    EnvVarNode,
    ParseError,
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


def test_parse_dollar_variables_and_termination_par02():
    # Verify bare $, non-env dollar variables terminate and preserve words, and env vars are parsed as EnvVarNode
    parser = CommandParser("echo $HOME $? $ $1 prefix$VAR")
    node = parser.parse()
    cmd = node.pipeline.commands[0]
    words = [p.value for p in cmd.parts if isinstance(p, WordNode)]
    env_vars = [p.name for p in cmd.parts if isinstance(p, EnvVarNode)]
    assert env_vars == ["HOME", "VAR"]
    assert words == ["echo", "$?", "$", "$1", "prefix"]


def test_parse_quotes_with_delimiters_par03():
    # Delimiters inside quotes must not break pipeline or subcommands
    parser = CommandParser('echo "a | b" $(echo ")") {get_val("{")}')
    node = parser.parse()
    cmd = node.pipeline.commands[0]
    assert len(node.pipeline.commands) == 1
    # Check subcommand was parsed properly
    subcmds = [p for p in cmd.parts if isinstance(p, SubcommandNode)]
    assert len(subcmds) == 1
    assert subcmds[0].raw == '$(echo ")")'


def test_parse_subcommand_preserves_raw_par05():
    parser = CommandParser("echo $(printf x) {*items}")
    node = parser.parse()
    cmd = node.pipeline.commands[0]
    subcmds = [p for p in cmd.parts if isinstance(p, SubcommandNode)]
    assert len(subcmds) == 1
    assert subcmds[0].raw == "$(printf x)"


def test_parse_diagnostics_par06():
    import pytest
    from pycli.parser import ParseError

    # Empty pipeline stage
    with pytest.raises(ParseError, match="Empty pipeline stage"):
        CommandParser("echo a | | cat").parse()

    with pytest.raises(ParseError, match="Empty pipeline stage"):
        CommandParser("echo a |").parse()

    # Redirection missing target
    with pytest.raises(ParseError, match="Missing target"):
        CommandParser("echo >").parse()

    with pytest.raises(ParseError, match="Missing target"):
        CommandParser("echo > | cat").parse()

    # Empty splat
    with pytest.raises(ParseError, match="Empty splat"):
        CommandParser("rm {*}").parse()

    # Unclosed subcommand
    with pytest.raises(ParseError, match="Unclosed"):
        CommandParser("echo $(git status").parse()


def test_cor05_parse_error_precise_locations():
    from pycli import transpile

    # 1. Pipeline vuota su seconda riga
    source_pipe = "a = 1\n$(echo | | cat)\n"
    with pytest.raises(ParseError) as exc_info:
        transpile(source_pipe)
    assert exc_info.value.line == 2
    assert exc_info.value.column == 10

    # 2. Redirection target mancante su seconda riga
    source_redir = "a = 1\n$(echo >)\n"
    with pytest.raises(ParseError) as exc_info:
        transpile(source_redir)
    assert exc_info.value.line == 2
    assert exc_info.value.column == 8

    # 3. Splat vuota su seconda riga
    source_splat = "a = 1\n$(rm {*})\n"
    with pytest.raises(ParseError) as exc_info:
        transpile(source_splat)
    assert exc_info.value.line == 2
    assert exc_info.value.column == 6

    # 4. Multiline command
    source_multi = "x = 10\n$(\n  echo | | cat\n)\n"
    with pytest.raises(ParseError) as exc_info:
        transpile(source_multi)
    assert exc_info.value.line == 3
    assert exc_info.value.column == 10


def test_parser_env_var_in_command():
    parser = CommandParser("kubectl -n $NAMESPACE get pods")
    node = parser.parse()
    cmd = node.pipeline.commands[0]
    env_parts = [p for p in cmd.parts if isinstance(p, EnvVarNode)]
    assert len(env_parts) == 1
    assert env_parts[0].name == "NAMESPACE"


def test_parser_env_var_has_interpolation():
    parser = CommandParser("echo $HOME")
    node = parser.parse()
    assert node.pipeline.has_interpolation()


def test_parser_env_var_mixed_with_interpolation():
    parser = CommandParser("aws s3 cp {local} s3://$BUCKET/out/")
    node = parser.parse()
    cmd = node.pipeline.commands[0]
    env_parts = [p for p in cmd.parts if isinstance(p, EnvVarNode)]
    interp_parts = [p for p in cmd.parts if isinstance(p, InterpolationNode)]
    assert len(env_parts) == 1
    assert env_parts[0].name == "BUCKET"
    assert len(interp_parts) == 1

