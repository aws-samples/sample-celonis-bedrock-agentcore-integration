"""Strands agent connecting to Celonis via AgentCore Gateway (IAM auth),
deployable on Amazon Bedrock AgentCore Runtime."""

import os
from dotenv import load_dotenv
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
from bedrock_agentcore.runtime import BedrockAgentCoreApp

load_dotenv()

GATEWAY_URL = os.environ["GATEWAY_URL"]
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")

# ---------------------------------------------------------------------------
# MCP client via AgentCore Gateway (SigV4 auth — no tokens to manage)
# ---------------------------------------------------------------------------
mcp_client = MCPClient(
    lambda: aws_iam_streamablehttp_client(
        endpoint=GATEWAY_URL,
        aws_region=AWS_REGION,
        aws_service="bedrock-agentcore",
    )
)

# ---------------------------------------------------------------------------
# Bedrock model
# ---------------------------------------------------------------------------
bedrock_model = BedrockModel(
    model_id=MODEL_ID,
    region_name=AWS_REGION,
    streaming=True,
)

SYSTEM_PROMPT = """You are a helpful process mining assistant powered by Celonis.
You have access to Celonis tools via MCP that let you search for process insights,
retrieve real-time process context, trigger actions, and write decisions back to Celonis.
Always use the available tools to answer questions about processes, KPIs, and operational data."""

# ---------------------------------------------------------------------------
# AgentCore Runtime wrapper
# ---------------------------------------------------------------------------
app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict):
    """Handle an invocation from AgentCore Runtime."""
    prompt = payload.get("prompt", "Hello")

    with mcp_client:
        tools = mcp_client.list_tools_sync()
        agent = Agent(
            model=bedrock_model,
            tools=tools,
            system_prompt=SYSTEM_PROMPT,
        )
        result = agent(prompt)

    return {"response": result.message}



if __name__ == "__main__":
    app.run()
