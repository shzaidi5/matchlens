"""A scripted Agent Framework chat client, so agent workflows run in tests with no network."""

from __future__ import annotations

import json
from typing import Any, Callable

from agent_framework import BaseChatClient, ChatResponse, Content, FunctionInvocationLayer, Message


def call(name: str, **arguments: Any) -> tuple[str, dict[str, Any]]:
    """A scripted reply that asks for a tool call."""
    return name, arguments


class ScriptedChat(FunctionInvocationLayer, BaseChatClient):
    """`script(agent, messages)` returns text, a `call(...)`, or raises.

    `agent` is "analyst", "studio", "fan" or "ask", worked out from the
    instructions the framework sends with each request.
    """

    def __init__(self, script: Callable[[str, list[Message]], Any]) -> None:
        super().__init__()
        self.script = script
        self.requests: list[tuple[str, list[Message]]] = []

    @staticmethod
    def agent_of(instructions: str) -> str:
        if "tactical analyst" in instructions:
            return "analyst"
        if "analyst desk" in instructions:
            return "studio"
        if "casual fans" in instructions:
            return "fan"
        return "ask"

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):
        agent = self.agent_of(options.get("instructions") or "")
        self.requests.append((agent, list(messages)))
        reply = self.script(agent, list(messages))
        if isinstance(reply, tuple):
            name, arguments = reply
            contents = [Content.from_function_call(call_id=f"call_{len(self.requests)}", name=name, arguments=json.dumps(arguments))]
        else:
            contents = [Content.from_text(reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False))]
        return ChatResponse(messages=[Message(role="assistant", contents=contents)], response_id="scripted")

    def count(self, agent: str) -> int:
        return sum(1 for a, _ in self.requests if a == agent)


def last_kinds(messages: list[Message]) -> list[str]:
    return [c.type for c in messages[-1].contents]


def payload_of(messages: list[Message]) -> dict[str, Any]:
    """The JSON a writer or analyst was sent as its user message."""
    return json.loads(messages[0].text)
