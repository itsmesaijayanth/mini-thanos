from thanos.agent.parsing import extract_text_tool_calls

KNOWN = ["play_youtube", "get_current_time"]


def test_plain_json() -> None:
    [c] = extract_text_tool_calls('{"name": "play_youtube", "arguments": {"query": "x"}}', KNOWN)
    assert c.name == "play_youtube" and c.arguments == {"query": "x"}


def test_wrapped_in_tags_and_parameters_key() -> None:
    text = '<tool_call>{"name": "play_youtube", "parameters": {"query": "y"}}</tool_call>'
    [c] = extract_text_tool_calls(text, KNOWN)
    assert c.arguments == {"query": "y"}


def test_openai_style_with_string_args() -> None:
    text = '{"function": {"name": "get_current_time", "arguments": "{}"}}'
    [c] = extract_text_tool_calls(text, KNOWN)
    assert c.name == "get_current_time" and c.arguments == {}


def test_unknown_names_and_prose_ignored() -> None:
    assert extract_text_tool_calls('{"name": "rm_rf", "arguments": {}}', KNOWN) == []
    assert extract_text_tool_calls("Sure, playing it now {smile}", KNOWN) == []
