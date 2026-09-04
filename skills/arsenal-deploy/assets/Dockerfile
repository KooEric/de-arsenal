# Container for running a DE Arsenal project on Lambda (container image), Fargate,
# Cloud Run jobs, or Cloudflare Containers. Build from the directory that holds
# arsenal.yaml. Secrets arrive as environment variables at runtime, never baked in.
FROM python:3.12-slim

ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
RUN pip install "de-arsenal"            # or "de-arsenal[postgres]"

WORKDIR /app
COPY arsenal.yaml collect.yaml transform.yaml ./
# State and data must live on a mounted/persisted path or be synced around the run.
VOLUME ["/app/.arsenal", "/app/data"]

ENTRYPOINT ["arsenal"]
CMD ["run"]
