FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
RUN useradd --create-home --uid 10001 codatau
COPY --chown=codatau:codatau . .
RUN mkdir -p /data/uploads /data/artifacts /data/instance /app/instance && chown -R codatau:codatau /data /app/instance
USER codatau
ENV UPLOAD_FOLDER=/data/uploads ANALYTICS_FOLDER=/data/artifacts
EXPOSE 5000
CMD ["sh", "scripts/start-web.sh"]

FROM runtime AS test
USER root
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN pip install pytest==9.1.1
COPY --chown=codatau:codatau tests /app/tests
USER codatau
CMD ["sh", "scripts/test-container.sh"]

FROM runtime AS production
