# Context Manager image: dockerfan4life/strata-context-manager
FROM python:3.12-slim
LABEL org.opencontainers.image.title="strata-context-manager" \
      org.opencontainers.image.description="Persistent context cache for the Strata inference engine behind LiteLLM" \
      org.opencontainers.image.source="https://github.com/themimi974/strata-context-manager" \
      org.opencontainers.image.licenses="Apache-2.0"
WORKDIR /srv
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd -r -u 10001 scm
COPY alembic.ini ./
COPY migrations ./migrations
COPY strata_context_manager ./strata_context_manager
USER scm
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=4)"
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn strata_context_manager.app:app --host 0.0.0.0 --port 8090"]
