FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.11-slim-bookworm

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py ./

RUN useradd --create-home appuser
USER appuser

ENV AWS_REGION=us-east-1
ENV GATEWAY_URL=

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"]

CMD ["python", "agent.py"]
