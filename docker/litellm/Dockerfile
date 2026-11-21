# LiteLLM with the strata-context-manager hook built in: dockerfan4life/strata-context-manager-litellm
# Build from the repository root:  docker build -f docker/litellm/Dockerfile -t strata-context-manager-litellm .
ARG LITELLM_IMAGE=docker.litellm.ai/berriai/litellm:v1.110.2
FROM ${LITELLM_IMAGE}
LABEL org.opencontainers.image.title="strata-context-manager-litellm" \
      org.opencontainers.image.description="LiteLLM proxy with the strata-context-manager hook" \
      org.opencontainers.image.source="https://github.com/themimi974/strata-context-manager" \
      org.opencontainers.image.licenses="Apache-2.0"
COPY hook/strata_context_hook.py /app/strata_context_hook.py
