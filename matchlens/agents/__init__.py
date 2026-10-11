"""Agent layer, built on Microsoft Agent Framework.

    client.py   Foundry (or Azure OpenAI) chat client from environment settings
    tools.py    read-only match tools the agents call
    desk.py     the editorial desk: a multi-agent workflow that writes overlay copy
    analyst.py  the ask-the-match agent
"""

from .analyst import AskAnalyst  # noqa: F401
from .client import chat_client_from_env, describe_client  # noqa: F401
from .desk import EditorialDesk, NarrationCache  # noqa: F401
from .tools import MatchTools  # noqa: F401
