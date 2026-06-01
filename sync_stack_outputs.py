"""Configure local files from CloudFormation stack outputs.

Run after 'aws cloudformation deploy' to populate .bedrock_agentcore.yaml
and Dockerfile with the values that 'agentcore launch' needs.

Usage:
  python sync_stack_outputs.py
  python sync_stack_outputs.py --stack-name MyCustomStackName
"""

import argparse
import os
import re
import shutil
import sys

import boto3
import ruamel.yaml
from dotenv import load_dotenv, set_key

load_dotenv()

STACK_NAME = "celonis-agentcore"


def get_stack_outputs(stack_name, region):
    """Fetch CloudFormation stack outputs as a dict."""
    cfn = boto3.client("cloudformation", region_name=region)
    try:
        resp = cfn.describe_stacks(StackName=stack_name)
    except Exception as e:
        print(f"ERROR: Could not describe stack '{stack_name}' in {region}: {e}")
        print(f"\nDeploy first with:")
        print(f"  aws cloudformation deploy --template-file template.yaml \\")
        print(f"    --stack-name {stack_name} --capabilities CAPABILITY_NAMED_IAM \\")
        print(f"    --parameter-overrides ...")
        sys.exit(1)

    stacks = resp.get("Stacks", [])
    if not stacks:
        print(f"ERROR: Stack '{stack_name}' not found.")
        sys.exit(1)

    return {o["OutputKey"]: o["OutputValue"] for o in stacks[0].get("Outputs", [])}


def get_cognito_client_secret(user_pool_id, client_id, region):
    """Retrieve the Cognito app client secret (not in CFN outputs for security)."""
    cognito = boto3.client("cognito-idp", region_name=region)
    resp = cognito.describe_user_pool_client(UserPoolId=user_pool_id, ClientId=client_id)
    return resp["UserPoolClient"].get("ClientSecret", "")


def update_env(outputs, client_secret, region):
    """Write stack outputs to .env for reference and offline testing."""
    env_path = os.path.join(os.getcwd(), ".env")

    set_key(env_path, "AWS_REGION", region)
    set_key(env_path, "GATEWAY_URL", outputs.get("GatewayUrl", ""))
    set_key(env_path, "BEDROCK_MODEL_ID", outputs.get("BedrockModelIdResolved", ""))
    set_key(env_path, "COGNITO_USER_POOL_ID", outputs.get("CognitoUserPoolId", ""))
    set_key(env_path, "COGNITO_CLIENT_ID", outputs.get("CognitoClientId", ""))
    set_key(env_path, "COGNITO_CLIENT_SECRET", client_secret)
    set_key(env_path, "COGNITO_TOKEN_URL", outputs.get("CognitoTokenUrl", ""))
    set_key(env_path, "COGNITO_SCOPE", outputs.get("CognitoScope", ""))

    print(f"  .env updated")


def update_yaml(outputs, region):
    """Write execution role and JWT authorizer config to .bedrock_agentcore.yaml."""
    yaml_path = ".bedrock_agentcore.yaml"

    if not os.path.exists(yaml_path):
        example = ".bedrock_agentcore.yaml.example"
        if os.path.exists(example):
            shutil.copy(example, yaml_path)
            print(f"  Created {yaml_path} from example template")
        else:
            print(f"  ERROR: {yaml_path} not found and no example to copy from")
            sys.exit(1)

    yaml = ruamel.yaml.YAML()
    yaml.preserve_quotes = True
    with open(yaml_path, encoding="utf-8") as f:
        config = yaml.load(f)

    agent = config["agents"]["celonis_process_agent"]
    agent["aws"]["execution_role"] = outputs.get("RuntimeRoleArn")
    agent["aws"]["execution_role_auto_create"] = False
    agent["aws"]["region"] = region

    # JWT authorizer for inbound auth
    cognito_issuer = outputs.get("CognitoIssuerUrl", "")
    cognito_client_id = outputs.get("CognitoClientId", "")
    if cognito_issuer and cognito_client_id:
        agent["authorizer_configuration"] = {
            "customJWTAuthorizer": {
                "discoveryUrl": f"{cognito_issuer}/.well-known/openid-configuration",
                "allowedClients": [cognito_client_id],
            }
        }

    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f)
    print(f"  {yaml_path} updated")


def update_dockerfile(outputs, region):
    """Patch Dockerfile ENV vars with stack outputs."""
    dockerfiles = [
        "Dockerfile",
        os.path.join(".bedrock_agentcore", "celonis_process_agent", "Dockerfile"),
    ]
    env_vars = {
        "GATEWAY_URL": outputs.get("GatewayUrl", ""),
        "AWS_REGION": region,
        "BEDROCK_MODEL_ID": outputs.get("BedrockModelIdResolved", ""),
    }

    for path in dockerfiles:
        if not os.path.exists(path):
            continue
        text = open(path, encoding="utf-8").read()
        for var_name, var_value in env_vars.items():
            pattern = re.compile(rf"^ENV {var_name}=.*$", re.MULTILINE)
            new_line = f"ENV {var_name}={var_value}"
            if pattern.search(text):
                text = pattern.sub(new_line, text)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"  {path} updated")


def main():
    parser = argparse.ArgumentParser(description="Configure local files from CFN stack outputs")
    parser.add_argument("--stack-name", default=STACK_NAME, help="CloudFormation stack name")
    parser.add_argument("--region", default=None, help="AWS region (default: from AWS CLI config)")
    args = parser.parse_args()

    # Use explicit --region, or fall back to AWS CLI default region
    region = args.region or boto3.session.Session().region_name
    if not region:
        print("ERROR: No region specified. Set a default with 'aws configure set region <region>'")
        print("       or pass --region explicitly.")
        sys.exit(1)

    print(f"Reading stack '{args.stack_name}' in {region}...\n")
    outputs = get_stack_outputs(args.stack_name, region)

    print("Stack outputs:")
    for k, v in outputs.items():
        print(f"  {k}: {v}")
    print()

    update_yaml(outputs, region)
    update_dockerfile(outputs, region)

    client_secret = get_cognito_client_secret(
        outputs["CognitoUserPoolId"], outputs["CognitoClientId"], region
    )
    update_env(outputs, client_secret, region)

    print(f"\nDone! Next: agentcore deploy")


if __name__ == "__main__":
    main()
