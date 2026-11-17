# Ballista Spark worker image: Spark 3.5 JVM runtime + Iceberg + Python 3.11.
#
# apache/spark:3.5.x ships Python 3.10, but de-arsenal requires >=3.11, so the
# JVM distribution and Spark's Kubernetes entrypoint are copied onto a
# python:3.11 base. Build context is this directory with the workspace wheels
# for arsenal-core, de-gladius and de-ballista placed in ./wheels/.
#
#   uv build --package arsenal-core --package de-gladius --package de-ballista \
#       --wheel --out-dir packages/ballista/docker/wheels
#   docker build -t ballista-worker:dev packages/ballista/docker
ARG SPARK_VERSION=3.5.3

FROM apache/spark:${SPARK_VERSION} AS spark

FROM python:3.11-slim-bookworm
ARG ICEBERG_VERSION=1.6.1
ARG SCALA_BINARY=2.12
ARG SPARK_MINOR=3.5

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        openjdk-17-jre-headless tini procps ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s "$(dirname "$(dirname "$(readlink -f "$(command -v java)")")")" /opt/java \
    && useradd --uid 185 --gid 0 --home-dir /opt/spark/work-dir --no-create-home spark

COPY --from=spark /opt/spark /opt/spark
COPY --from=spark /opt/entrypoint.sh /opt/decom.sh /opt/

ENV JAVA_HOME=/opt/java \
    SPARK_HOME=/opt/spark \
    PYSPARK_PYTHON=python3 \
    PYTHONPATH=/opt/spark/python:/opt/spark/python/lib/py4j-src.zip

RUN ln -s "$(ls /opt/spark/python/lib/py4j-*-src.zip)" /opt/spark/python/lib/py4j-src.zip \
    && maven=https://repo1.maven.org/maven2/org/apache/iceberg \
    && curl -fsSLo "/opt/spark/jars/iceberg-spark-runtime-${SPARK_MINOR}_${SCALA_BINARY}-${ICEBERG_VERSION}.jar" \
        "${maven}/iceberg-spark-runtime-${SPARK_MINOR}_${SCALA_BINARY}/${ICEBERG_VERSION}/iceberg-spark-runtime-${SPARK_MINOR}_${SCALA_BINARY}-${ICEBERG_VERSION}.jar" \
    && curl -fsSLo "/opt/spark/jars/iceberg-aws-bundle-${ICEBERG_VERSION}.jar" \
        "${maven}/iceberg-aws-bundle/${ICEBERG_VERSION}/iceberg-aws-bundle-${ICEBERG_VERSION}.jar"

COPY wheels/ /tmp/wheels/
RUN pip install --no-cache-dir --find-links /tmp/wheels "de-ballista[object-store]" \
    && rm -rf /tmp/wheels \
    && mkdir -p /opt/de-arsenal /opt/spark/work-dir \
    && printf 'from ballista.worker_main import main\nraise SystemExit(main())\n' \
        > /opt/de-arsenal/ballista-worker.py \
    && chown -R 185:0 /opt/spark/work-dir

WORKDIR /opt/spark/work-dir
USER 185
ENTRYPOINT ["/opt/entrypoint.sh"]
