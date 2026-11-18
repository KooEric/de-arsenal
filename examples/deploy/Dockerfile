# Run an Arsenal project in a container (Kubernetes CronJob, ECS scheduled task, ...).
#
#   docker build -t my-pipeline -f Dockerfile /path/to/project    # the dir with arsenal.yaml
#   docker run --rm -e PG_DSN=... -v pipeline-state:/project/.arsenal my-pipeline
#
# Mount a persistent volume at /project/.arsenal. Without it every run starts from
# nothing and fetches everything again.
FROM python:3.12-slim

RUN pip install --no-cache-dir "de-arsenal[postgres]==0.6.*" \
    && useradd --create-home --uid 1000 arsenal

WORKDIR /project
COPY --chown=arsenal . /project
RUN mkdir -p /project/.arsenal && chown arsenal /project/.arsenal
USER arsenal
VOLUME /project/.arsenal

ENTRYPOINT ["arsenal"]
CMD ["run"]
