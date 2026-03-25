"""Test the deployed Amazon Bedrock AgentCore agent with Cognito JWT auth.

Usage:
  python test_remote.py
  python test_remote.py "custom prompt"
"""

import os
import sys
import json
import requests
from urllib.parse import quote
from dotenv import load_dotenv

load_dotenv()

REGION = os.environ.get("AWS_REGION", "us-east-1")
ACCOUNT_ID = os.environ["AWS_ACCOUNT_ID"]

# Resolve agent ID: .env first, then .bedrock_agentcore.yaml
AGENT_ID = os.environ.get("AGENTCORE_AGENT_ID", "").strip("'\"")
if not AGENT_ID:
    try:
        import ruamel.yaml
        yaml = ruamel.yaml.YAML()
        with open(".bedrock_agentcore.yaml", encoding="utf-8") as f:
            config = yaml.load(f)
        AGENT_ID = config["agents"]["celonis_process_agent"]["bedrock_agentcore"]["agent_id"]
        if AGENT_ID:
            print(f"  Using agent ID from .bedrock_agentcore.yaml: {AGENT_ID}")
    except Exception as e:
        print(f"  Warning: could not resolve agent ID from .bedrock_agentcore.yaml: {e}")
    if not AGENT_ID:
        print("ERROR: AGENTCORE_AGENT_ID not set in .env and not found in .bedrock_agentcore.yaml.")
        print("Run 'agentcore launch' first, then set AGENTCORE_AGENT_ID in .env.")
        sys.exit(1)

AGENT_ARN = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:runtime/{AGENT_ID}"

# Cognito credentials
COGNITO_TOKEN_URL = os.environ["COGNITO_TOKEN_URL"]
COGNITO_CLIENT_ID = os.environ["COGNITO_CLIENT_ID"]
COGNITO_CLIENT_SECRET = os.environ["COGNITO_CLIENT_SECRET"]
COGNITO_SCOPE = os.environ["COGNITO_SCOPE"]

# AgentCore invocation endpoint
INVOKE_URL = f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{quote(AGENT_ARN, safe='')}/invocations?qualifier=DEFAULT"


def get_cognito_token():
    """Get a JWT access token from Cognito using client_credentials grant."""
    resp = requests.post(
        COGNITO_TOKEN_URL,
        data={"grant_type": "client_credentials", "scope": COGNITO_SCOPE},
        auth=(COGNITO_CLIENT_ID, COGNITO_CLIENT_SECRET),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def invoke(prompt):
    print(f"Invoking: {prompt}")
    print(f"  Agent: {AGENT_ARN}")
    print(f"  URL: {INVOKE_URL}")

    token = get_cognito_token()
    print(f"  Got Cognito token")

    resp = requests.post(
        INVOKE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"prompt": prompt},
        timeout=120,
    )

    resp.raise_for_status()
    print(f"Status: {resp.status_code}")
    try:
        body = resp.json()
        print(f"Response:\n{json.dumps(body, indent=2)}")
    except json.JSONDecodeError:
        print(f"Response:\n{resp.text}")


if __name__ == "__main__":
    prompt = sys.argv[1] if len(sys.argv) > 1 else "Give me info on PO approvals"
    invoke(prompt)
