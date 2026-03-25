"""Set up all AWS prerequisites for the Celonis AgentCore agent.

Creates:
  1. IAM execution role with Amazon Bedrock + ECR + Gateway access
  2. Amazon Bedrock AgentCore Identity credential provider (stores Celonis OAuth creds)
  3. Amazon Bedrock AgentCore Gateway with MCP target pointing to Celonis
  4. Cognito User Pool + app client (JWT auth for inbound calls from Celonis)
  5. Updates .bedrock_agentcore.yaml and .env

Idempotent — safe to run multiple times.

Usage:
  python setupAWSbackend.py
  python setupAWSbackend.py --cleanup
"""

import json
import os
import time
import boto3
import ruamel.yaml
from dotenv import load_dotenv, set_key

load_dotenv()

REGION = os.environ.get("AWS_REGION", "us-east-1")
ACCOUNT_ID = os.environ["AWS_ACCOUNT_ID"]

# IAM
ROLE_NAME = f"CelonisAgentCoreRuntime-{REGION}"
GATEWAY_ROLE_NAME = f"CelonisAgentCoreGateway-{REGION}"

# AgentCore Identity + Gateway
CREDENTIAL_PROVIDER_NAME = "celonis-oauth-provider"
GATEWAY_NAME = "celonis-mcp-gateway"
GATEWAY_TARGET_NAME = "cel"

# Cognito (inbound JWT auth for Celonis → Agent)
COGNITO_POOL_NAME = "CelonisAgentCorePool"
COGNITO_RESOURCE_SERVER_ID = "celonis-agent"
COGNITO_APP_CLIENT_NAME = "celonis-caller"

iam = boto3.client("iam", region_name=REGION)
agentcore = boto3.client("bedrock-agentcore-control", region_name=REGION)
cognito = boto3.client("cognito-idp", region_name=REGION)


# ---- Step 1: IAM Roles ------------------------------------------------------
def setup_gateway_role():
    """Create IAM role for the AgentCore Gateway to assume."""
    print("  Setting up Gateway IAM role...")
    trust_policy = json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    })

    try:
        resp = iam.create_role(
            RoleName=GATEWAY_ROLE_NAME, AssumeRolePolicyDocument=trust_policy,
            Description="IAM role for Celonis AgentCore Gateway",
        )
        role_arn = resp["Role"]["Arn"]
        print(f"    Created: {role_arn}")
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = iam.get_role(RoleName=GATEWAY_ROLE_NAME)["Role"]["Arn"]
        iam.update_assume_role_policy(RoleName=GATEWAY_ROLE_NAME, PolicyDocument=trust_policy)
        print(f"    Exists: {role_arn}")

    # Gateway needs access to Identity (token vault) to fetch OAuth tokens
    iam.put_role_policy(RoleName=GATEWAY_ROLE_NAME, PolicyName="AgentCoreIdentityAccess", PolicyDocument=json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetCredentialProvider",
                    "bedrock-agentcore:GetTokenVault",
                    "bedrock-agentcore:IssueToken",
                    "bedrock-agentcore:CreateWorkloadIdentity",
                    "bedrock-agentcore:GetWorkloadAccessToken",
                    "bedrock-agentcore:GetResourceOauth2Token",
                ],
                "Resource": [
                    f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:token-vault/default",
                    f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:token-vault/default/*",
                    f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:workload-identity-directory/default",
                    f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:workload-identity-directory/default/*",
                ],
            },
            {
                "Effect": "Allow",
                "Action": "secretsmanager:GetSecretValue",
                "Resource": f"arn:aws:secretsmanager:{REGION}:{ACCOUNT_ID}:secret:bedrock-agentcore-identity!default/oauth2/celonis-oauth-provider*",
            },
        ],
    }))
    print("    Policies: AgentCoreIdentityAccess (with OAuth token + secrets access)")
    return role_arn


def setup_role(gateway_arn):
    print("[1/5] Setting up IAM execution role...")
    trust_policy = json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    })

    try:
        resp = iam.create_role(
            RoleName=ROLE_NAME, AssumeRolePolicyDocument=trust_policy,
            Description="Execution role for Celonis AgentCore agent",
        )
        role_arn = resp["Role"]["Arn"]
        print(f"  Created: {role_arn}")
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
        iam.update_assume_role_policy(RoleName=ROLE_NAME, PolicyDocument=trust_policy)
        print(f"  Exists: {role_arn}")

    # Bedrock invoke-only policy scoped to the specific model
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="BedrockInvokeModel", PolicyDocument=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            "Resource": [
                f"arn:aws:bedrock:{REGION}::foundation-model/us.anthropic.claude-sonnet-4-20250514-v1:0",
                f"arn:aws:bedrock:us-*::foundation-model/anthropic.claude-sonnet-4-20250514-v1:0",
                f"arn:aws:bedrock:{REGION}:{ACCOUNT_ID}:inference-profile/us.anthropic.claude-sonnet-4-20250514-v1:0",
            ],
        }],
    }))

    # ECR pull access
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="ECRPullAccess", PolicyDocument=json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
            {"Effect": "Allow", "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
             "Resource": f"arn:aws:ecr:{REGION}:{ACCOUNT_ID}:repository/bedrock-agentcore-*"},
        ],
    }))

    # CloudWatch Logs
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="CloudWatchLogs", PolicyDocument=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            "Resource": f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/*",
        }],
    }))

    # Gateway invocation access
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="GatewayInvokeAccess", PolicyDocument=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": "bedrock-agentcore:InvokeGateway",
            "Resource": gateway_arn,
        }],
    }))

    print("  Policies: BedrockInvokeModel + ECRPullAccess + CloudWatchLogs + GatewayInvokeAccess")

    # Update YAML with role
    yaml = ruamel.yaml.YAML()
    yaml.preserve_quotes = True
    with open(".bedrock_agentcore.yaml", encoding="utf-8") as f:
        config = yaml.load(f)
    aws_cfg = config["agents"]["celonis_process_agent"]["aws"]
    aws_cfg["execution_role"] = role_arn
    aws_cfg["execution_role_auto_create"] = False
    with open(".bedrock_agentcore.yaml", "w", encoding="utf-8") as f:
        yaml.dump(config, f)
    print(f"  Updated .bedrock_agentcore.yaml with role ARN")
    return role_arn


# ---- Step 3: AgentCore Identity (OAuth2 credential provider) ----------------
def find_existing_credential_provider():
    """Check if the credential provider already exists."""
    try:
        paginator = agentcore.get_paginator("list_oauth2_credential_providers")
        for page in paginator.paginate():
            for provider in page.get("credentialProviders", []):
                if provider.get("name") == CREDENTIAL_PROVIDER_NAME:
                    return provider.get("credentialProviderArn")
    except Exception as e:
        print(f"  Warning listing credential providers: {e}")
    return None


def setup_identity():
    print("[2/5] Setting up AgentCore Identity credential provider...")

    client_id = os.environ["CELONIS_CLIENT_ID"]
    client_secret = os.environ["CELONIS_CLIENT_SECRET"]

    # Derive the discovery URL from the Celonis environment base URL.
    # CELONIS_TOKEN_URL is like https://TEAM.REALM.celonis.cloud/oauth2/token
    # Discovery URL is    like https://TEAM.REALM.celonis.cloud/.well-known/openid-configuration
    celonis_base = os.environ["CELONIS_TOKEN_URL"].split("/oauth2/")[0]
    discovery_url = f"{celonis_base}/.well-known/openid-configuration"

    provider_config = {
        "customOauth2ProviderConfig": {
            "oauthDiscovery": {
                "discoveryUrl": discovery_url,
            },
            "clientId": client_id,
            "clientSecret": client_secret,
        }
    }

    provider_arn = find_existing_credential_provider()
    if provider_arn:
        print(f"  Exists: {provider_arn}")
        try:
            agentcore.update_oauth2_credential_provider(
                name=CREDENTIAL_PROVIDER_NAME,
                credentialProviderVendor="CustomOauth2",
                oauth2ProviderConfigInput=provider_config,
            )
            print(f"  Updated credentials")
        except Exception as e:
            print(f"  Note: Could not update provider ({e}), using existing")
    else:
        resp = agentcore.create_oauth2_credential_provider(
            name=CREDENTIAL_PROVIDER_NAME,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput=provider_config,
        )
        provider_arn = resp["credentialProviderArn"]
        print(f"  Created: {provider_arn}")

    return provider_arn


# ---- Step 4: AgentCore Gateway -----------------------------------------------
def find_existing_gateway():
    """Check if the gateway already exists."""
    try:
        paginator = agentcore.get_paginator("list_gateways")
        for page in paginator.paginate():
            for gw in page.get("items", []):
                if gw.get("name") == GATEWAY_NAME:
                    return gw.get("gatewayId"), gw.get("gatewayArn")
    except Exception as e:
        print(f"  Warning listing gateways: {e}")
    return None, None


def find_existing_target(gateway_id):
    """Check if the gateway target already exists."""
    try:
        paginator = agentcore.get_paginator("list_gateway_targets")
        for page in paginator.paginate(gatewayIdentifier=gateway_id):
            for target in page.get("items", []):
                if target.get("name") == GATEWAY_TARGET_NAME:
                    return target.get("targetId")
    except Exception as e:
        print(f"  Warning listing gateway targets: {e}")
    return None


def setup_gateway(credential_provider_arn, gateway_role_arn):
    print("[3/5] Setting up AgentCore Gateway...")

    celonis_mcp_url = os.environ["CELONIS_MCP_SERVER_URL"]
    oauth_scope = os.environ.get("CELONIS_OAUTH_SCOPE", "mcp-asset.tools:execute")

    # Create or find gateway
    gateway_id, gateway_arn = find_existing_gateway()
    if gateway_id:
        print(f"  Gateway exists: {gateway_id}")
        gw_resp = agentcore.get_gateway(gatewayIdentifier=gateway_id)
        gateway_url = gw_resp.get("gatewayUrl", "")
        gateway_arn = gw_resp.get("gatewayArn", f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:gateway/{gateway_id}")
    else:
        gw_resp = agentcore.create_gateway(
            name=GATEWAY_NAME,
            description="MCP Gateway proxying to Celonis MCP Server with OAuth2",
            protocolType="MCP",
            roleArn=gateway_role_arn,
            authorizerType="AWS_IAM",
        )
        gateway_id = gw_resp["gatewayId"]
        gateway_arn = gw_resp["gatewayArn"]
        gateway_url = gw_resp.get("gatewayUrl", "")
        print(f"  Created gateway: {gateway_id}")

        # Wait for gateway to become READY
        print("  Waiting for gateway to become READY...")
        for _ in range(30):
            time.sleep(10)  # nosemgrep: arbitrary-sleep
            status_resp = agentcore.get_gateway(gatewayIdentifier=gateway_id)
            status = status_resp.get("status", "")
            gateway_url = status_resp.get("gatewayUrl", gateway_url)
            if status == "READY":
                print(f"  Gateway READY: {gateway_url}")
                break
            elif status == "FAILED":
                reasons = status_resp.get("statusReasons", [])
                raise RuntimeError(f"Gateway creation failed: {reasons}")
            print(f"    Status: {status}...")
        else:
            print("  Warning: Gateway not READY after 5 minutes, continuing anyway...")

    # Create or find target
    target_id = find_existing_target(gateway_id)
    if target_id:
        print(f"  Target exists: {target_id}")
    else:
        target_resp = agentcore.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=GATEWAY_TARGET_NAME,
            targetConfiguration={
                "mcp": {
                    "mcpServer": {
                        "endpoint": celonis_mcp_url,
                    }
                }
            },
            credentialProviderConfigurations=[{
                "credentialProviderType": "OAUTH",
                "credentialProvider": {
                    "oauthCredentialProvider": {
                        "providerArn": credential_provider_arn,
                        "scopes": [oauth_scope],
                        "grantType": "CLIENT_CREDENTIALS",
                    }
                },
            }],
        )
        target_id = target_resp["targetId"]
        print(f"  Created target: {target_id}")

    # Update .env with gateway URL
    env_path = os.path.join(os.getcwd(), ".env")
    set_key(env_path, "GATEWAY_URL", gateway_url)
    set_key(env_path, "GATEWAY_ID", gateway_id)
    print(f"  .env updated with GATEWAY_URL={gateway_url}")

    # Update Dockerfiles with gateway URL so fresh deploys use the right value
    _update_dockerfiles_gateway_url(gateway_url)

    return gateway_id, gateway_arn, gateway_url


def _update_dockerfiles_gateway_url(gateway_url):
    """Patch the GATEWAY_URL ENV line in both Dockerfiles."""
    import re
    dockerfiles = [
        "Dockerfile",
        os.path.join(".bedrock_agentcore", "celonis_process_agent", "Dockerfile"),
    ]
    new_line = f"ENV GATEWAY_URL={gateway_url}"
    pattern = re.compile(r"^ENV GATEWAY_URL=.*$", re.MULTILINE)
    for path in dockerfiles:
        if not os.path.exists(path):
            continue
        text = open(path, encoding="utf-8").read()
        if pattern.search(text):
            text = pattern.sub(new_line, text)
        else:
            # No existing ENV line — append before CMD
            text = text.replace("CMD ", f"{new_line}\n\nCMD ")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"  Updated {path} with GATEWAY_URL")


# ---- Step 5: Cognito User Pool (inbound JWT auth) ----------------------------
def find_existing_pool():
    """Find the Cognito User Pool by name."""
    paginator = cognito.get_paginator("list_user_pools")
    for page in paginator.paginate(MaxResults=60):
        for pool in page.get("UserPools", []):
            if pool["Name"] == COGNITO_POOL_NAME:
                return pool["Id"]
    return None


def setup_cognito():
    print("[4/5] Setting up Cognito User Pool (inbound JWT auth)...")

    # Create or find pool
    pool_id = find_existing_pool()
    if pool_id:
        print(f"  Pool exists: {pool_id}")
    else:
        resp = cognito.create_user_pool(
            PoolName=COGNITO_POOL_NAME,
            AdminCreateUserConfig={"AllowAdminCreateUserOnly": True},
        )
        pool_id = resp["UserPool"]["Id"]
        print(f"  Created pool: {pool_id}")

    # Create or find resource server (defines custom scopes)
    scope_name = "invoke"
    full_scope = f"{COGNITO_RESOURCE_SERVER_ID}/{scope_name}"
    try:
        cognito.create_resource_server(
            UserPoolId=pool_id,
            Identifier=COGNITO_RESOURCE_SERVER_ID,
            Name="Celonis Agent Invocation",
            Scopes=[{"ScopeName": scope_name, "ScopeDescription": "Invoke the agent"}],
        )
        print(f"  Created resource server: {COGNITO_RESOURCE_SERVER_ID}")
    except cognito.exceptions.InvalidParameterException:
        print(f"  Resource server exists: {COGNITO_RESOURCE_SERVER_ID}")

    # Create or find app client (client_credentials grant for M2M)
    existing_client_id = None
    existing_client_secret = None
    paginator = cognito.get_paginator("list_user_pool_clients")
    for page in paginator.paginate(UserPoolId=pool_id):
        for client in page.get("UserPoolClients", []):
            if client["ClientName"] == COGNITO_APP_CLIENT_NAME:
                existing_client_id = client["ClientId"]
                break

    if existing_client_id:
        desc = cognito.describe_user_pool_client(UserPoolId=pool_id, ClientId=existing_client_id)
        existing_client_secret = desc["UserPoolClient"].get("ClientSecret")
        print(f"  App client exists: {existing_client_id}")
    else:
        resp = cognito.create_user_pool_client(
            UserPoolId=pool_id,
            ClientName=COGNITO_APP_CLIENT_NAME,
            GenerateSecret=True,
            AllowedOAuthFlows=["client_credentials"],
            AllowedOAuthScopes=[full_scope],
            AllowedOAuthFlowsUserPoolClient=True,
        )
        existing_client_id = resp["UserPoolClient"]["ClientId"]
        existing_client_secret = resp["UserPoolClient"].get("ClientSecret")
        print(f"  Created app client: {existing_client_id}")

    # Enable domain for token endpoint (if not already set)
    try:
        cognito.describe_user_pool(UserPoolId=pool_id)
        domain_prefix = f"celonis-agent-{ACCOUNT_ID}"
        try:
            cognito.create_user_pool_domain(UserPoolId=pool_id, Domain=domain_prefix)
            print(f"  Created domain: {domain_prefix}")
        except cognito.exceptions.InvalidParameterException:
            print(f"  Domain already exists")
    except Exception as e:
        print(f"  Warning setting domain: {e}")

    issuer_url = f"https://cognito-idp.{REGION}.amazonaws.com/{pool_id}"
    token_url = f"https://{domain_prefix}.auth.{REGION}.amazoncognito.com/oauth2/token"

    # Update .env
    # SECURITY NOTE: This educational sample stores the Cognito client secret in .env for
    # simplicity. In a production environment, store secrets in AWS Secrets
    # Manager and reference them by ARN instead. Ensure .env is in .gitignore
    # and never committed to source control.
    env_path = os.path.join(os.getcwd(), ".env")
    set_key(env_path, "COGNITO_USER_POOL_ID", pool_id)
    set_key(env_path, "COGNITO_CLIENT_ID", existing_client_id)
    if existing_client_secret:
        set_key(env_path, "COGNITO_CLIENT_SECRET", existing_client_secret)
    set_key(env_path, "COGNITO_TOKEN_URL", token_url)
    set_key(env_path, "COGNITO_SCOPE", full_scope)

    print(f"  Issuer: {issuer_url}")
    print(f"  Token URL: {token_url}")
    print(f"  Scope: {full_scope}")

    return pool_id, issuer_url, full_scope, existing_client_id


# ---- Step 6: Update YAML config ----------------------------------------------
def update_yaml_config(gateway_url, cognito_issuer_url=None, cognito_scope=None, cognito_client_id=None):
    print("[5/5] Updating .bedrock_agentcore.yaml...")
    yaml = ruamel.yaml.YAML()
    yaml.preserve_quotes = True
    with open(".bedrock_agentcore.yaml", encoding="utf-8") as f:
        config = yaml.load(f)

    agent_cfg = config["agents"]["celonis_process_agent"]
    # Configure Cognito JWT authorizer for inbound auth
    if cognito_issuer_url and cognito_scope and cognito_client_id:
        agent_cfg["authorizer_configuration"] = {
            "customJWTAuthorizer": {
                "discoveryUrl": f"{cognito_issuer_url}/.well-known/openid-configuration",
                "allowedClients": [cognito_client_id],
            }
        }
        print(f"  Set JWT authorizer: {cognito_issuer_url}")
    else:
        agent_cfg["authorizer_configuration"] = None
        print("  Cleared authorizer_configuration")

    with open(".bedrock_agentcore.yaml", "w", encoding="utf-8") as f:
        yaml.dump(config, f)
    print("  Updated .bedrock_agentcore.yaml")


# ---- Reset local config for new account --------------------------------------
def reset():
    """Reset account-specific values in .env and .bedrock_agentcore.yaml."""
    print("Resetting local config for new account deployment...")

    env_path = os.path.join(os.getcwd(), ".env")
    set_key(env_path, "AGENTCORE_AGENT_ID", "")
    set_key(env_path, "GATEWAY_URL", "")
    set_key(env_path, "GATEWAY_ID", "")
    set_key(env_path, "COGNITO_USER_POOL_ID", "")
    set_key(env_path, "COGNITO_CLIENT_ID", "")
    set_key(env_path, "COGNITO_CLIENT_SECRET", "")
    set_key(env_path, "COGNITO_TOKEN_URL", "")
    set_key(env_path, "COGNITO_SCOPE", "")
    print("  .env: cleared all generated values.")

    if os.path.exists(".bedrock_agentcore.yaml"):
        yaml = ruamel.yaml.YAML()
        yaml.preserve_quotes = True
        with open(".bedrock_agentcore.yaml", encoding="utf-8") as f:
            config = yaml.load(f)

        agent = config["agents"]["celonis_process_agent"]
        agent["aws"]["execution_role"] = None
        agent["aws"]["execution_role_auto_create"] = True
        agent["aws"]["account"] = None
        agent["aws"]["ecr_repository"] = None
        agent["aws"]["ecr_auto_create"] = True
        agent["bedrock_agentcore"]["agent_id"] = None
        agent["bedrock_agentcore"]["agent_arn"] = None
        agent["codebuild"]["execution_role"] = None
        agent["codebuild"]["project_name"] = None
        agent["codebuild"]["source_bucket"] = None
        agent["authorizer_configuration"] = None

        with open(".bedrock_agentcore.yaml", "w", encoding="utf-8") as f:
            yaml.dump(config, f)
        print("  .bedrock_agentcore.yaml: reset all account-specific values.")
    print("\nNow update AWS_ACCOUNT_ID in .env, then run: python setupAWSbackend.py")


# ---- Cleanup: delete all AWS resources ---------------------------------------
def cleanup():
    """Delete all AWS resources created by this script."""
    print("=" * 60)
    print("Celonis AgentCore - Cleanup")
    print(f"Account: {ACCOUNT_ID}  Region: {REGION}")
    print("=" * 60)

    # 1. Delete gateway targets + gateway
    print("\n[1/5] Deleting AgentCore Gateway...")
    try:
        paginator = agentcore.get_paginator("list_gateways")
        for page in paginator.paginate():
            for gw in page.get("items", []):
                if gw.get("name") == GATEWAY_NAME:
                    gw_id = gw["gatewayId"]
                    # Delete targets first
                    try:
                        tp = agentcore.get_paginator("list_gateway_targets")
                        for tpage in tp.paginate(gatewayIdentifier=gw_id):
                            for target in tpage.get("items", []):
                                tid = target["targetId"]
                                print(f"  Deleting target: {tid}")
                                agentcore.delete_gateway_target(gatewayIdentifier=gw_id, targetId=tid)
                    except Exception as e:
                        print(f"  Warning deleting targets: {e}")
                    print(f"  Waiting for target deletion to propagate...")
                    time.sleep(5)  # nosemgrep: arbitrary-sleep
                    print(f"  Deleting gateway: {gw_id}")
                    agentcore.delete_gateway(gatewayIdentifier=gw_id)
                    print(f"  Deleted.")
    except Exception as e:
        print(f"  Skipped: {e}")

    # 2. Delete credential provider
    print("\n[2/5] Deleting AgentCore Identity credential provider...")
    try:
        paginator = agentcore.get_paginator("list_oauth2_credential_providers")
        for page in paginator.paginate():
            for provider in page.get("credentialProviders", []):
                if provider.get("name") == CREDENTIAL_PROVIDER_NAME:
                    print(f"  Deleting: {provider.get('credentialProviderArn')}")
                    agentcore.delete_oauth2_credential_provider(name=CREDENTIAL_PROVIDER_NAME)
                    print(f"  Deleted.")
    except Exception as e:
        print(f"  Skipped: {e}")

    # 3. Delete IAM roles (inline policies + managed policies + role)
    print("\n[3/5] Deleting IAM roles...")
    for role_name in [ROLE_NAME, GATEWAY_ROLE_NAME]:
        try:
            # Remove inline policies
            policies = iam.list_role_policies(RoleName=role_name)["PolicyNames"]
            for p in policies:
                iam.delete_role_policy(RoleName=role_name, PolicyName=p)
                print(f"  Removed inline policy: {role_name}/{p}")
            # Detach managed policies
            attached = iam.list_attached_role_policies(RoleName=role_name)["AttachedPolicies"]
            for p in attached:
                iam.detach_role_policy(RoleName=role_name, PolicyArn=p["PolicyArn"])
                print(f"  Detached managed policy: {role_name}/{p['PolicyName']}")
            # Delete role
            iam.delete_role(RoleName=role_name)
            print(f"  Deleted role: {role_name}")
        except iam.exceptions.NoSuchEntityException:
            print(f"  Role not found: {role_name}")
        except Exception as e:
            print(f"  Warning for {role_name}: {e}")

    # 4. Delete AgentCore runtime (if exists)
    print("\n[4/5] Deleting AgentCore runtime...")
    try:
        resp = agentcore.list_agent_runtimes()
        for rt in resp.get("agentRuntimes", []):
            rt_name = rt.get("agentRuntimeName", "")
            if "celonis_process_agent" in rt_name:
                rt_id = rt["agentRuntimeId"]
                print(f"  Deleting runtime: {rt_id}")
                try:
                    agentcore.delete_agent_runtime(agentRuntimeId=rt_id)
                    print(f"  Deleted.")
                except Exception as e:
                    print(f"  Warning: {e}")
    except Exception as e:
        print(f"  Skipped: {e}")

    # 5. Delete Cognito User Pool
    print("\n[5/5] Deleting Cognito User Pool...")
    try:
        pool_id = find_existing_pool()
        if pool_id:
            # Delete domain first (required before pool deletion)
            domain_prefix = f"celonis-agent-{ACCOUNT_ID}"
            try:
                cognito.delete_user_pool_domain(UserPoolId=pool_id, Domain=domain_prefix)
                print(f"  Deleted domain: {domain_prefix}")
            except Exception as e:
                print(f"  Warning deleting domain: {e}")
            cognito.delete_user_pool(UserPoolId=pool_id)
            print(f"  Deleted pool: {pool_id}")
        else:
            print(f"  Not found, skipping.")
    except Exception as e:
        print(f"  Skipped: {e}")

    # 6. Reset local config
    print("\n[6] Resetting local config...")
    reset()

    print("\n" + "=" * 60)
    print("Cleanup complete!")
    print("=" * 60)
    print("\nTo redeploy from scratch:")
    print("  1. python setupAWSbackend.py")
    print("  2. agentcore launch")
    print("  3. Update AGENTCORE_AGENT_ID in .env")
    print("  4. python test_remote.py")


# ---- Main -------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    if "--cleanup" in sys.argv:
        cleanup()
    else:
        # Auto-create .bedrock_agentcore.yaml from example if missing
        import shutil
        if not os.path.exists(".bedrock_agentcore.yaml"):
            if os.path.exists(".bedrock_agentcore.yaml.example"):
                shutil.copy(".bedrock_agentcore.yaml.example", ".bedrock_agentcore.yaml")
                print("Created .bedrock_agentcore.yaml from example template.")
            else:
                print("ERROR: .bedrock_agentcore.yaml.example not found. Cannot continue.")
                sys.exit(1)

        print("=" * 60)
        print("Celonis AgentCore - Gateway Setup")
        print(f"Account: {ACCOUNT_ID}  Region: {REGION}")
        print("=" * 60)

        credential_provider_arn = setup_identity()
        gateway_role_arn = setup_gateway_role()
        gateway_id, gateway_arn, gateway_url = setup_gateway(credential_provider_arn, gateway_role_arn)
        role_arn = setup_role(gateway_arn)
        pool_id, cognito_issuer_url, cognito_scope, cognito_client_id = setup_cognito()
        update_yaml_config(gateway_url, cognito_issuer_url, cognito_scope, cognito_client_id)

        print("\n" + "=" * 60)
        print("Setup complete!")
        print("=" * 60)
        print(f"\nGateway URL: {gateway_url}")
        print(f"\nNext: .venv\\Scripts\\agentcore launch")
        print(f"Then fill in AGENTCORE_AGENT_ID in .env")
