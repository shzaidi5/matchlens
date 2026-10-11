"""Chat client selection.

Preferred: a Microsoft Foundry project (`FOUNDRY_PROJECT_ENDPOINT`,
`FOUNDRY_MODEL`). Also supported: a plain Azure OpenAI resource
(`AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`).

Auth is Microsoft Entra ID through `DefaultAzureCredential`: `az login` on a
laptop, managed identity in Azure. An API key is only used for the Azure OpenAI
path, and only if `AZURE_OPENAI_API_KEY` is set.
"""

from __future__ import annotations

import os
from typing import Any


def chat_client_from_env() -> Any | None:
    """Return an Agent Framework chat client, or None when nothing is configured."""
    foundry_endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT")
    foundry_model = os.getenv("FOUNDRY_MODEL")
    if foundry_endpoint and foundry_model:
        from agent_framework.foundry import FoundryChatClient
        from azure.identity import DefaultAzureCredential

        return FoundryChatClient(
            project_endpoint=foundry_endpoint, model=foundry_model, credential=DefaultAzureCredential()
        )

    aoai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    aoai_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    if aoai_endpoint and aoai_deployment:
        from agent_framework.openai import OpenAIChatClient

        api_key = os.getenv("AZURE_OPENAI_API_KEY")
        if api_key:
            return OpenAIChatClient(model=aoai_deployment, azure_endpoint=aoai_endpoint, api_key=api_key)
        from azure.identity import DefaultAzureCredential

        return OpenAIChatClient(
            model=aoai_deployment, azure_endpoint=aoai_endpoint, credential=DefaultAzureCredential()
        )
    return None


def describe_client() -> str:
    """One line for logs and the viewer footer. Never includes secrets."""
    if os.getenv("FOUNDRY_PROJECT_ENDPOINT") and os.getenv("FOUNDRY_MODEL"):
        return f"Microsoft Foundry ({os.getenv('FOUNDRY_MODEL')})"
    if os.getenv("AZURE_OPENAI_ENDPOINT") and os.getenv("AZURE_OPENAI_DEPLOYMENT"):
        return f"Azure OpenAI ({os.getenv('AZURE_OPENAI_DEPLOYMENT')})"
    return "not configured"
