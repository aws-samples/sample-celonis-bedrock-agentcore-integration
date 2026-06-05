FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.11-slim-bookworm

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py ./

RUN useradd --create-home appuser
USER appuser

# Populated by sync_stack_outputs.py from CloudFormation stack outputs.
ENV AWS_REGION=us-east-1
ENV GATEWAY_URL=
ENV BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-6

# OpenTelemetry observability configuration
ENV OTEL_RESOURCE_ATTRIBUTES=service.name=celonis-process-agent
ENV OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"]

CMD ["opentelemetry-instrument", "python", "agent.py"]
