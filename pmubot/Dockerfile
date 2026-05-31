FROM python:3.12-slim

ARG TARGETARCH

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*

RUN case "${TARGETARCH}" in \
        amd64) docker_arch='x86_64'; compose_arch='x86_64' ;; \
        arm64) docker_arch='aarch64'; compose_arch='aarch64' ;; \
        *) echo "Unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && curl -fsSL "https://download.docker.com/linux/static/stable/${docker_arch}/docker-28.1.1.tgz" -o /tmp/docker.tgz \
    && tar -xzf /tmp/docker.tgz -C /tmp \
    && mv /tmp/docker/docker /usr/local/bin/docker \
    && mkdir -p /usr/local/lib/docker/cli-plugins \
    && curl -fsSL "https://github.com/docker/compose/releases/download/v2.36.2/docker-compose-linux-${compose_arch}" \
        -o /usr/local/lib/docker/cli-plugins/docker-compose \
    && chmod +x /usr/local/bin/docker /usr/local/lib/docker/cli-plugins/docker-compose \
    && rm -rf /tmp/docker /tmp/docker.tgz

RUN pip install --no-cache-dir fastapi httpx uvicorn

COPY app.py /app/app.py
COPY audit.py /app/audit.py
COPY bot_update.py /app/bot_update.py
COPY napcat_client.py /app/napcat_client.py
COPY static /app/static

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
