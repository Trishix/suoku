FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN groupadd --gid 10001 suoku && useradd --uid 10001 --gid suoku --create-home suoku
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

FROM base AS api
RUN pip install '.[server]'
USER 10001:10001
ENTRYPOINT ["suoku", "serve", "--host", "0.0.0.0", "--data", "/data"]

FROM base AS worker
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install '.[local]'
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 OMP_NUM_THREADS=2
USER 10001:10001
ENTRYPOINT ["suoku", "worker", "--data", "/data", "--model", "/models/siglip"]
