"""Test the deployed Amazon Bedrock AgentCore agent with Cognito JWT auth.

Reads all configuration from the CloudFormation stack outputs and
.bedrock_agentcore.yaml — no manual .env setup needed for testing.

Usage:
  python test_remote.py
  python test_remote.py "custom prompt"
  python test_remote.py --stack-name my-stack "custom prompt"
"""

import argparse
import json
import os
import sys

import boto3
import requests
import ruamel.yaml
from urllib.parse import quote
from dotenv import load_dotenv

load_dotenv()

STACK_NAME = "celonis-agentcore"
REGION = os.environ.get("AWS_REGION") or boto3.session.Session().region_name or "us-east-1"


def get_stack_outputs(stack_name, region):
    """Fetch CloudFormation stack outputs as a dict. Returns None if stack unavailable."""
    cfn = boto3.client("cloudformation", region_name=region)
    try:
        resp = cfn.describe_stacks(StackName=stack_name)
    except Exception:
        return None

    stacks = resp.get("Stacks", [])
    if not stacks:
        return None

    return {o["OutputKey"]: o["OutputValue"] for o in stacks[0].get("Outputs", [])}


def get_cognito_client_secret(user_pool_id, client_id, region):
    """Retrieve the Cognito app client secret (not in CFN outputs for security)."""
    cognito = boto3.client("cognito-idp", region_name=region)
    resp = cognito.describe_user_pool_client(UserPoolId=user_pool_id, ClientId=client_id)
    return resp["UserPoolClient"].get("ClientSecret", "")


def get_agent_id():
    """Resolve agent ID from .bedrock_agentcore.yaml (populated by agentcore launch)."""
    yaml_path = ".bedrock_agentcore.yaml"
    if not os.path.exists(yaml_path):
        return None
    try:
        yaml = ruamel.yaml.YAML()
        with open(yaml_path, encoding="utf-8") as f:
            config = yaml.load(f)
        return config["agents"]["celonis_process_agent"]["bedrock_agentcore"]["agent_id"]
    except Exception:
        return None


def get_cognito_token(token_url, client_id, client_secret, scope):
    """Get a JWT access token from Cognito using client_credentials grant."""
    resp = requests.post(
        token_url,
        data={"grant_type": "client_credentials", "scope": scope},
        auth=(client_id, client_secret),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def invoke(prompt, stack_name):
    """Invoke the deployed agent using config from CFN stack or .env fallback."""
    outputs = get_stack_outputs(stack_name, REGION)

    if outputs:
        cognito_token_url = outputs["CognitoTokenUrl"]
        cognito_client_id = outputs["CognitoClientId"]
        cognito_scope = outputs["CognitoScope"]
        cognito_client_secret = get_cognito_client_secret(
            outputs["CognitoUserPoolId"], cognito_client_id, REGION
        )
        account_id = outputs.get("RuntimeRoleArn", "").split(":")[4]
    else:
        # Fall back to .env values (populated by sync_stack_outputs.py)
        cognito_token_url = os.environ.get("COGNITO_TOKEN_URL", "")
        cognito_client_id = os.environ.get("COGNITO_CLIENT_ID", "")
        cognito_client_secret = os.environ.get("COGNITO_CLIENT_SECRET", "")
        cognito_scope = os.environ.get("COGNITO_SCOPE", "celonis-agent/invoke")
        account_id = os.environ.get("AWS_ACCOUNT_ID", "")
        if not cognito_token_url:
            print("ERROR: Could not read stack and no Cognito values in .env.")
            print("Run 'python sync_stack_outputs.py' first.")
            sys.exit(1)

    # Resolve agent ID
    agent_id = os.environ.get("AGENTCORE_AGENT_ID", "").strip("'\"") or get_agent_id()
    if not agent_id:
        print("ERROR: Agent ID not found.")
        print("Run 'agentcore launch' first. The agent ID is written to .bedrock_agentcore.yaml.")
        sys.exit(1)

    agent_arn = f"arn:aws:bedrock-agentcore:{REGION}:{account_id}:runtime/{agent_id}"
    invoke_url = f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{quote(agent_arn, safe='')}/invocations?qualifier=DEFAULT"

    print(f"Invoking: {prompt}")
    print(f"  Agent: {agent_arn}")
    print(f"  URL: {invoke_url}")

    token = get_cognito_token(cognito_token_url, cognito_client_id, cognito_client_secret, cognito_scope)
    print(f"  Got Cognito token")

    resp = requests.post(
        invoke_url,
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
    parser = argparse.ArgumentParser(description="Test the deployed AgentCore agent")
    parser.add_argument("prompt", nargs="?", default="Give me info on PO approvals",
                        help="Prompt to send to the agent")
    parser.add_argument("--stack-name", default=STACK_NAME,
                        help="CloudFormation stack name (default: celonis-agentcore)")
    parser.add_argument("--region", default=None,
                        help="AWS region (default: from AWS CLI config or AWS_REGION env var)")
    args = parser.parse_args()

    if args.region:
        REGION = args.region
    invoke(args.prompt, args.stack_name)
