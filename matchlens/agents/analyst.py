"""Ask-the-match agent: free-form viewer questions answered from live match state.

An Agent Framework agent with four read-only tools, followed by a critic pass.
The agent can only learn about the match through its tools, and the critic
rejects an answer that quotes a number no tool returned. One revision is
allowed; a second failure is returned flagged as unverified rather than hidden.
"""

from __future__ import annotations

from typing import Any

from agent_framework import Agent

from ..grounding import numbers_in, ungrounded_numbers
from ..i18n import LANGUAGE_NAMES
from ..models import Profile
from ..pipeline.orchestrator import Pipeline
from .tools import MatchTools

INSTRUCTIONS = """You are a football match analyst answering a viewer during a live match.
Call tools to get facts. Answer only from tool results: never state a number, \
name or event a tool did not return. If the tools cannot answer, say so.
Reply with the answer only: do not describe your tool calls or your plan.
Keep the answer to at most four sentences."""

AUDIENCE = {
    "analyst": "tactically literate; use the numbers and proper terms",
    "fan": "a casual fan; plain language, no jargon, at most two numbers",
}


def final_text(response: Any) -> str:
    """The agent's last message. `response.text` joins every message, including
    anything the model said before a tool call ("Let me check the stats...")."""
    for message in reversed(getattr(response, "messages", None) or []):
        if getattr(message, "role", None) == "assistant" and (message.text or "").strip():
            return message.text.strip()
    return (response.text or "").strip()


class AskAnalyst:
    def __init__(self, client: Any, pipeline: Pipeline) -> None:
        self.pipeline = pipeline
        self.agent = Agent(client, INSTRUCTIONS, name="match_analyst")

    async def ask(self, question: str, profile: Profile) -> dict[str, Any]:
        tools = MatchTools(self.pipeline)
        language = LANGUAGE_NAMES.get(profile.lang, profile.lang)
        prompt = f"Answer in {language}. Audience: {AUDIENCE[profile.mode]}.\n\nQuestion: {question}"
        response = await self.agent.run(prompt, tools=tools.as_tools())
        answer = final_text(response)
        bad = ungrounded_numbers(answer, tools.evidence | numbers_in(question))
        revisions = 0
        if bad:
            # Critic: send the answer back once with the specific problem.
            revisions = 1
            retry = (
                f"{prompt}\n\nYour previous answer was: {answer}\n"
                f"These numbers are not in any tool result: {', '.join(bad)}. "
                "Rewrite the answer using only numbers the tools return."
            )
            response = await self.agent.run(retry, tools=tools.as_tools())
            answer = final_text(response)
            bad = ungrounded_numbers(answer, tools.evidence | numbers_in(question))
        return {
            "answer": answer,
            "grounded": not bad,
            "ungrounded_numbers": bad,
            "tools_used": tools.calls,
            "revisions": revisions,
        }
