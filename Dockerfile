FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends libvips-dev build-essential && rm -rf /var/lib/apt/lists/*

RUN pip install uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY . .

FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends libvips && rm -rf /var/lib/apt/lists/*

ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=${GIT_COMMIT}

WORKDIR /app
COPY --from=builder /app /app

CMD [".venv/bin/python", "bot.py"]
