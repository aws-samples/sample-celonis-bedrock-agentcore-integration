# Security

This is educational sample code intended to demonstrate an integration pattern between Celonis and Amazon Bedrock AgentCore. It is not production-ready. Review and harden it before any production use. This document describes the security model, your responsibilities, and the risks to evaluate.

## Reporting Security Issues

If you discover a potential security issue in this sample, please open an issue in this repository's issue tracker, or follow the disclosure process defined by the repository owner. Do not include sensitive details (credentials, tokens, customer data) in a public report.

## Shared Responsibility Model

Security and compliance are shared between AWS and you, the customer. See the [AWS Shared Responsibility Model](https://aws.amazon.com/compliance/shared-responsibility-model/).

- **AWS manages:** Infrastructure security, service availability, AWS IAM service security, Amazon Cognito service availability, and encryption of data at rest within AWS services (AWS Secrets Manager, Amazon Cognito, Amazon CloudWatch Logs).
- **You are responsible for:** Managing IAM policies and least privilege access, rotating Amazon Cognito client secrets, securing the Celonis OAuth2 credentials you pass as parameters, monitoring CloudWatch logs for security events, and configuring network access controls for production deployments.

## Threat Model and Trust Boundaries

This system spans four trust boundaries: the calling client (Celonis Action Flow), AgentCore Runtime, AgentCore Gateway, and the Celonis MCP Server. Key threats and mitigations:

| Threat | Vector | Mitigation |
|---|---|---|
| Token theft | Cognito JWT or Celonis OAuth2 token intercepted or leaked | TLS for all transport; short-lived tokens; secrets stored in AWS Secrets Manager, never in code or logs |
| IAM role escalation | Overly broad runtime/gateway permissions | Scoped resource ARNs in `template.yaml`; review and tighten before production |
| MCP server compromise | Malicious or compromised Celonis endpoint returns hostile tool output | Treat MCP tool results as untrusted input; the agent only invokes the configured Celonis endpoint |
| Credential exposure | OAuth2 client secret committed to source control or printed during troubleshooting | `NoEcho` on CloudFormation parameters; `.env` and `.bedrock_agentcore.yaml` gitignored; redact secrets from logs |
| Unauthorized invocation | Caller without valid JWT reaches the runtime | AgentCore Runtime JWT authorizer validates Amazon Cognito `client_credentials` tokens |

**Data flow:** Celonis → (Cognito JWT) → AgentCore Runtime → Amazon Bedrock model + → (IAM SigV4) → AgentCore Gateway → (OAuth2) → Celonis MCP Server. Authentication is enforced at each inbound boundary; credentials for the outbound Celonis call are brokered by AgentCore Identity and never handled in agent code.

## Per-Service Security Guidelines

- **AWS Identity and Access Management (AWS IAM):** Apply least privilege. The roles in `template.yaml` scope permissions to specific resource ARNs where the service supports it. Review the policies and remove any actions your use case does not need. Periodically review role policies.
- **Amazon Cognito:** Tokens use the `client_credentials` grant. Configure token expiration appropriately and rotate the app client secret on a schedule that matches your organization's policy.
- **AWS Secrets Manager:** The Celonis OAuth2 credentials are stored encrypted. For production, enable automatic rotation:

  ```bash
  # Requires a Lambda rotation function — see https://docs.aws.amazon.com/secretsmanager/latest/userguide/rotating-secrets.html
  aws secretsmanager rotate-secret \
    --secret-id <secret-arn> \
    --rotation-lambda-arn <lambda-arn> \
    --rotation-rules AutomaticallyAfterDays=30
  ```
- **Amazon Bedrock:** Restrict model access via Model Access in your account. Note the data residency implications of geographic cross-region inference (see Compliance below).
- **Amazon ECR:** Enable image scanning and address findings as part of vulnerability management.
- **CloudWatch:** Set log retention policies appropriate to your compliance requirements (logs do not expire by default) and restrict access to log groups.

## Encryption and Key Management

- **In transit:** All traffic is encrypted with TLS.
- **At rest:** AWS Secrets Manager encrypts the OAuth2 credentials using the AWS managed key (`aws/secretsmanager`). Amazon Cognito User Pool data is encrypted with AWS owned keys. CloudWatch Logs are encrypted by default.
- **Key management:** This sample uses AWS managed and AWS owned keys for simplicity. For production, consider customer managed AWS KMS keys for AWS Secrets Manager and CloudWatch Logs to gain control over key rotation policies and cross-account access. See [AWS KMS key rotation](https://docs.aws.amazon.com/kms/latest/developerguide/rotate-keys.html).

  ```bash
  # Create a customer managed KMS key
  aws kms create-key --description "Key for Celonis agent Secrets Manager encryption"

  # Associate a KMS key with a CloudWatch Logs log group
  aws logs associate-kms-key \
    --log-group-name /aws/bedrock-agentcore/runtimes/<agent-id>-DEFAULT/runtime-logs \
    --kms-key-id <key-arn>
  ```

  Then reference the key in your CloudFormation template using the `KmsKeyId` property on `AWS::SecretsManager::Secret`.

## Data Classification and Handling

| Data | Classification | Handling |
|---|---|---|
| OAuth2 credentials (Celonis client ID/secret, Cognito client secret) | HIGH | Stored in AWS Secrets Manager; passed via `NoEcho` parameters; never logged or committed |
| Tokens (Cognito JWT, Celonis OAuth2) | HIGH | Short-lived; transmitted over HTTPS only; never logged |
| Process mining data from Celonis | Depends on your data — may contain PII or business-sensitive information | Handle per your organization's data governance policies |

Handling procedures: Never commit credentials to version control. Use parameter overrides for CloudFormation deployment. Avoid logging tokens or credentials, and redact sensitive values from logs and error messages when troubleshooting. The CLI examples in the README use `<...>` placeholders — substitute real values only at the command line or via a secrets source, not by editing committed files.

## Access Logging and Audit

- **AWS CloudTrail:** Enable CloudTrail to log API-level access to AWS Secrets Manager (credential retrievals), Amazon Cognito (authentication events), AWS IAM (permission changes), and Amazon Bedrock AgentCore (gateway invocations). CloudTrail is essential for security auditing and compliance and is not configured by this template.

  ```bash
  # Create and start a CloudTrail trail
  aws cloudtrail create-trail \
    --name celonis-agent-audit \
    --s3-bucket-name <your-audit-bucket> \
    --is-multi-region-trail

  aws cloudtrail start-logging --name celonis-agent-audit
  ```

  See [Creating a trail](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/cloudtrail-create-a-trail-using-the-console-first-time.html) for full setup options including CloudWatch Logs integration.
- **Application logs:** Runtime logs in the CloudWatch log group `/aws/bedrock-agentcore/runtimes/<agent-id>-DEFAULT/runtime-logs` capture application events. Configure retention per your compliance requirements.
- **Security events to monitor:** Authentication attempts (Amazon Cognito), credential retrievals (AWS Secrets Manager, via CloudTrail), agent invocations (AgentCore), and MCP tool calls (application logs). Consider CloudWatch alarms for authentication failures and unusual API patterns.

## Compliance Considerations

- **Data residency:** This sample uses geographic cross-region inference (US or EU), which routes requests across regions within a geography. Confirm this meets your data residency requirements.
- **Third-party processing:** Process data is exchanged with Celonis, a third-party service. Review Celonis data handling and your data sharing obligations.
- **Log retention:** Configure CloudWatch and CloudTrail retention to match your compliance policies.

## Operational Risks

- **Cost:** Amazon Bedrock model invocations incur charges. Monitor usage and set AWS Budgets alerts.
- **Rate limits:** Review Amazon Bedrock service quotas for your use case.
- **Error handling:** This is sample code. Add production-grade error handling, retry logic, and monitoring before deploying.
- **Network mode:** The agent runs in PUBLIC network mode by default. For production, consider VPC mode with private subnets and security groups to restrict network access.

## Third-Party Integration Responsibilities

This sample does not bundle, redistribute, or proxy any Celonis software or data — it connects to a Celonis MCP Server endpoint that you supply using credentials you own. Before using this integration:

- **Right to use:** Confirm your Celonis license and OAuth2 application grant you the right to access the MCP Server for your intended use.
- **Security review:** Review the Celonis MCP Server connection against your organization's third-party integration requirements. The connection uses OAuth2 `client_credentials` brokered by Amazon Bedrock AgentCore Identity.
- **Data sharing:** Data exchanged with Celonis is governed by the [Celonis Terms of Service](https://www.celonis.com/terms-of-service/). You are responsible for ensuring this data sharing complies with your organization's policies and any applicable regulations.
- **Approval:** Obtain any internal legal, security, and procurement approvals required by your organization before connecting production data.

> **⚠️ Important:** This is educational sample code. It does NOT constitute legal, security, or compliance approval for the Celonis integration. You MUST complete your own verification before any production deployment.

### Pre-Deployment Verification Checklist

Use this checklist to track required approvals before connecting production data:

- [ ] **Terms of Service reviewed** — Celonis Terms of Service read and accepted for your intended use
- [ ] **Right-to-use verified** — Celonis license confirms MCP Server access is permitted
- [ ] **Security review completed** — OAuth2 connection reviewed against your organization's third-party integration policy
- [ ] **Data classification assessed** — Data exchanged with Celonis classified per your governance policies
- [ ] **Data sharing agreement** — Confirmed compliance with data residency and processing requirements
- [ ] **Procurement approval** — Internal procurement process completed (if applicable)
- [ ] **Legal sign-off** — Legal team has approved the integration for your use case
