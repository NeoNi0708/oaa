"""Tests for Anthropic client — message conversion and tool schema transformation."""
import json
from oaa.llm.anthropic_client import (
    _openai_tool_to_anthropic,
    _convert_messages_to_anthropic,
)


def test_openai_tool_to_anthropic():
    tool = {
        "type": "function",
        "function": {
            "name": "code_run",
            "description": "Execute code",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "type": {"type": "string", "enum": ["python", "powershell"]},
                },
                "required": ["code"],
            },
        },
    }
    result = _openai_tool_to_anthropic(tool)
    assert result["name"] == "code_run"
    assert result["description"] == "Execute code"
    assert result["input_schema"]["type"] == "object"
    assert result["input_schema"]["required"] == ["code"]
    assert "code" in result["input_schema"]["properties"]


def test_convert_simple_messages():
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
    ]
    system, converted = _convert_messages_to_anthropic(messages)
    assert system == "You are helpful."
    assert len(converted) == 1
    assert converted[0]["role"] == "user"
    assert converted[0]["content"] == "Hello"


def test_convert_with_tool_calls():
    messages = [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "Search for apples"},
        {
            "role": "assistant",
            "content": "Let me search.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "web_search", "arguments": '{"query": "apples"}'},
                }
            ],
        },
        {"role": "tool", "content": "Found 5 apples", "tool_call_id": "call_1"},
    ]

    system, converted = _convert_messages_to_anthropic(messages)
    assert system == "System prompt"
    assert len(converted) == 3

    # Assistant with tool_use
    assert converted[1]["role"] == "assistant"
    assert isinstance(converted[1]["content"], list)
    assert converted[1]["content"][0]["type"] == "text"
    assert converted[1]["content"][1]["type"] == "tool_use"
    assert converted[1]["content"][1]["name"] == "web_search"
    assert converted[1]["content"][1]["input"]["query"] == "apples"

    # User with tool_result
    assert converted[2]["role"] == "user"
    assert isinstance(converted[2]["content"], list)
    assert converted[2]["content"][0]["type"] == "tool_result"
    assert converted[2]["content"][0]["tool_use_id"] == "call_1"
    assert "Found 5 apples" in converted[2]["content"][0]["content"]


def test_convert_no_system_prompt():
    messages = [{"role": "user", "content": "Hi"}]
    system, converted = _convert_messages_to_anthropic(messages)
    assert system == ""
    assert len(converted) == 1


def test_convert_tool_result_message():
    """User text after tool result is a separate user message — tool_result
    is a standalone tool role message, not combined with user text."""
    messages = [
        {"role": "tool", "content": "Done", "tool_call_id": "call_1"},
        {"role": "user", "content": "Thanks, next try oranges"},
    ]
    _, converted = _convert_messages_to_anthropic(messages)
    # First message: tool → tool_result block in user role
    assert converted[0]["role"] == "user"
    assert isinstance(converted[0]["content"], list)
    assert converted[0]["content"][0]["type"] == "tool_result"
    assert converted[0]["content"][0]["tool_use_id"] == "call_1"
    # Second message: plain user text
    assert converted[1]["role"] == "user"
    assert converted[1]["content"] == "Thanks, next try oranges"
