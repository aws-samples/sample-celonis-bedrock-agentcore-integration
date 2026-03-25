# Celonis Process Agent on Amazon Bedrock AgentCore

A Strands agent that connects to Celonis MCP Server via Amazon Bedrock AgentCore Gateway, deployed on Amazon Bedrock AgentCore Runtime. Includes Amazon Cognito JWT authentication so the agent can be invoked directly from Celonis Action Flows.

## Architecture

![Architecture](architecture.png)

```
Celonis Action Flow / Client
  │                          ▲
  │  HTTP POST               │  JSON response
  │  (Cognito JWT token)     │
  ▼                          │
AgentCore Runtime (JWT authorizer)
  │                          ▲
  │  Strands Agent +         │  Tool results
  │  Claude Sonnet 4         │
  ▼                          │
AgentCore Gateway (IAM SigV4)
  │                          ▲
  │  MCP protocol            │  MCP responses
  │  (OAuth2 via Identity)   │
  ▼                          │
Celonis MCP Server
```


The agent works bidirectionally with Celonis:

Celonis calls the agent via POST request (e.g., from an Action Flow) to get AI-powered answers about process data
The agent calls back into Celonis via MCP tools to query data or trigger Action Flows

Inbound: clients authenticate with a Cognito JWT (client_credentials grant).
Outbound: the Gateway handles Celonis OAuth2 tokens via Amazon Bedrock AgentCore Identity — no credentials in agent code.

## Prerequisites

- Python 3.11+
- AWS CLI configured
- Amazon Bedrock AgentCore Starter Toolkit (`pip install bedrock-agentcore-starter-toolkit`)
- A [Celonis](https://www.celonis.com/) account with MCP Server access and OAuth2 app credentials (see [Third-Party Services](#third-party-services))

## Quick Start

### 1. Configure

```bash
cp .env.example .env
```

Fill in your Celonis and AWS values in `.env`:

> **Security note:** This educational sample stores secrets (Celonis OAuth credentials, Cognito client secret) in `.env` in plaintext for simplicity. In a production environment, store secrets in AWS Secrets Manager and reference them by ARN. The `.env` file is gitignored and should never be committed to source control.

- `CELONIS_MCP_SERVER_URL` — your Celonis MCP server endpoint
- `CELONIS_TOKEN_URL` — Celonis OAuth2 token endpoint
- `CELONIS_CLIENT_ID` / `CELONIS_CLIENT_SECRET` — Celonis OAuth2 app credentials
- `AWS_ACCOUNT_ID` — target AWS account
- `AWS_REGION` — target region (default: `us-east-1`)


### 2. Install

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

### 3. Provision AWS resources

```bash
.venv\Scripts\python setupAWSbackend.py
```

This creates:
- IAM execution role with Bedrock, ECR, and Gateway permissions
- Amazon Bedrock AgentCore Identity credential provider (stores Celonis OAuth2 creds for the Gateway)
- Amazon Bedrock AgentCore Gateway + MCP target pointing to Celonis
- Cognito User Pool with OAuth2 `client_credentials` flow for inbound JWT auth
- Updates `.bedrock_agentcore.yaml` with the role ARN and JWT authorizer config
- Updates `.env` with Gateway URL, Cognito client credentials

### 4. Deploy

```bash
agentcore launch
```

> **Note:** `.bedrock_agentcore.yaml` is gitignored. A clean template (`.bedrock_agentcore.yaml.example`) is committed instead with all account values nulled out. `setupAWSbackend.py` automatically copies it to `.bedrock_agentcore.yaml` on first run, then both the setup script and `agentcore launch` populate it with your account-specific values.

### 5. Set agent ID

Grab the agent ID from `.bedrock_agentcore.yaml` and set it in `.env`:

```
AGENTCORE_AGENT_ID=celonis_process_agent-XXXXXXXXXX
```

### 6. Test

```bash
python test_remote.py
python test_remote.py "What are the top bottlenecks in PO processing?"
```

## Invoking the Agent

The agent accepts POST requests with a JSON body:

```json
{"prompt": "What processes are available?"}
```

The invocation endpoint URL follows this pattern:

```
https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{url_encoded_agent_arn}/invocations?qualifier=DEFAULT
```

To build it, take the `agent_arn` from `.bedrock_agentcore.yaml` (e.g. `arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/celonis_process_agent-XXXXXXXXXX`), URL-encode it, and insert it into the path. For example:

```
https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A123456789012%3Aruntime%2Fcelonis_process_agent-XXXXXXXXXX/invocations?qualifier=DEFAULT
```

### From Celonis Action Flow

Use the [HTTP2 (Action Flow)](https://docs.celonis.com/en/http2--action-flow-.html) module, which supports OAuth 2.0 `client_credentials` flow natively.

1. Create an HTTP2 OAuth 2.0 connection:
   - Flow type: `Client Credentials`
   - Token URI: value of `COGNITO_TOKEN_URL` from `.env`
   - Scope: `celonis-agent/invoke`
   - Client ID: value of `COGNITO_CLIENT_ID` from `.env`
   - Client Secret: value of `COGNITO_CLIENT_SECRET` from `.env`
   - Token placement: `Header` (default)
   - Header token name: `Bearer` (default)

2. Configure the HTTP2 request:
   - URL: `https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{url_encoded_agent_arn}/invocations?qualifier=DEFAULT`
   - Method: `POST`
   - Body type: `Raw`
   - Content type: `application/json`
   - Body: `{"prompt": "your question"}`

The HTTP2 module handles token fetching and refresh automatically.

### Getting a Cognito token

Use the `client_credentials` grant:

```
POST <COGNITO_TOKEN_URL>
Content-Type: application/x-www-form-urlencoded
Authorization: Basic base64(CLIENT_ID:CLIENT_SECRET)

grant_type=client_credentials&scope=celonis-agent/invoke
```

The values for `COGNITO_TOKEN_URL`, `COGNITO_CLIENT_ID`, and `COGNITO_CLIENT_SECRET` are in `.env` after running `setupAWSbackend.py`.

## Local Development

Test directly against Celonis MCP (no Gateway):

```bash
python test_local.py
```
This verifies the Celonis MCP connection works with your credentials.

## Cleanup

Delete all AWS resources:

```bash
python setupAWSbackend.py --cleanup
```

## Files

| File | Purpose |
|---|---|
| `agent.py` | Strands agent connecting to Celonis via Gateway |
| `setupAWSbackend.py` | Provisions all AWS resources (idempotent) |
| `test_remote.py` | Tests deployed agent with Cognito JWT |
| `test_local.py` | Tests direct Celonis MCP connection locally |
| `celonis_oauth.py` | OAuth2 token provider for local dev |

## AWS Resources

| Resource | Name |
|---|---|
| Cognito User Pool | `CelonisAgentCorePool` |
| IAM Role (Runtime) | `CelonisAgentCoreRuntime-{region}` |
| IAM Role (Gateway) | `CelonisAgentCoreGateway-{region}` |
| AgentCore Identity | `celonis-oauth-provider` |
| AgentCore Gateway | `celonis-mcp-gateway` |
| Gateway Target | `cel` |

## Third-Party Services

This sample integrates with [Celonis](https://www.celonis.com/), a third-party process mining platform. To use this sample, you must have your own Celonis account with access to the Celonis MCP Server and valid OAuth2 application credentials. Your use of Celonis is governed by the [Celonis Terms of Service](https://www.celonis.com/terms-of-service/) and is separate from your use of AWS services. AWS is not responsible for Celonis services, and Celonis is not responsible for AWS services.

## License

This sample code is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.
