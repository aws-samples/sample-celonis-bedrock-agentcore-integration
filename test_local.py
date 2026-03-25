"""Quick local smoke test – runs the agent outside of Amazon Bedrock AgentCore Runtime.

For local dev, this connects directly to Celonis MCP (not via Gateway).
Requires CELONIS_* env vars in .env.
"""

import os
from dotenv import load_dotenv
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

from celonis_oauth import CelonisOAuthProvider

load_dotenv()

oauth = CelonisOAuthProvider(
    token_url=os.environ["CELONIS_TOKEN_URL"],
    client_id=os.environ["CELONIS_CLIENT_ID"],
    client_secret=os.environ["CELONIS_CLIENT_SECRET"],
    scope=os.environ.get("CELONIS_OAUTH_SCOPE", "mcp-asset.tools:execute"),
)

mcp_client = MCPClient(
    lambda: streamablehttp_client(
        url=os.environ["CELONIS_MCP_SERVER_URL"],
        headers={"Authorization": f"Bearer {oauth.get_token()}"},
    )
)

bedrock_model = BedrockModel(
    model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
    region_name="us-east-1",
    streaming=True,
)

with mcp_client:
    tools = mcp_client.list_tools_sync()
    print(f"Discovered {len(tools)} Celonis tools:")
    for t in tools:
        print(f"  - {t.tool_name}")

    agent = Agent(
        model=bedrock_model,
        tools=tools,
        system_prompt="You are a Celonis process mining assistant. Use the available tools.",
    )

    while True:
        prompt = input("\nYou: ")
        if prompt.lower() in ("exit", "quit", "bye"):
            break
        response = agent(prompt)
        print(f"\nAgent: {response.message}")
