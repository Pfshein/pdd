# syntax=docker/dockerfile:1
#
# PDD sandbox image: the execution plane for dangerous stages.
# No credentials are baked into this image. The orchestrator passes only selected
# OPENAI_* variables at `docker run` time, by env name.

FROM node:22-slim AS node

FROM python:3.12-slim

ARG QWEN_NPM_PACKAGE=@qwen-code/qwen-code

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

RUN export DEBIAN_FRONTEND=noninteractive \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        procps \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules

RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

RUN python -m pip install --no-cache-dir pytest jsonschema \
    && npm install -g "${QWEN_NPM_PACKAGE}" \
    && qwen --version

WORKDIR /work

CMD ["sh", "-lc", "python --version && node --version && qwen --version"]
