FROM python:3.12-slim AS base
# Keep the runtime non-root; each later image adds only the dependencies needed by
# that process (API, offline worker, or explicitly networked insight worker).
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN groupadd --gid 10001 suoku && useradd --uid 10001 --gid suoku --create-home suoku
RUN mkdir /data && chown 10001:10001 /data
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

FROM base AS api
# The API image does not include model weights or provider SDKs.
RUN pip install '.[server]'
USER 10001:10001
ENTRYPOINT ["suoku", "serve", "--host", "0.0.0.0", "--data", "/data"]

FROM base AS worker
# FFmpeg is required for local frame extraction; the default compose worker has no network.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install '.[local]'
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 OMP_NUM_THREADS=2
USER 10001:10001
ENTRYPOINT ["suoku", "worker", "--data", "/data", "--model", "/models/siglip"]

FROM worker AS insights-worker
# This target is opt-in because enabling it allows selected frames to leave the host.
USER root
RUN pip install '.[insights]'
USER 10001:10001
