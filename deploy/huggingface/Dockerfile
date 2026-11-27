# Hugging Face Space for the OpenBro web demo.
#
# The Space repo holds only this file and README.md; the code is cloned
# from GitHub at build time. Rebuild the Space (Settings → Factory rebuild)
# to pick up a new release. Set GROQ_API_KEY under Settings → Secrets.
# See docs/DEPLOY.md.

FROM python:3.12-slim

ARG OPENBRO_REPO=https://github.com/brijeshch8482/openbro.git
ARG OPENBRO_REF=main

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 user
RUN git clone --depth 1 --branch "$OPENBRO_REF" "$OPENBRO_REPO" /app \
    && chown -R user /app
WORKDIR /app
RUN pip install ".[web,rag]"

USER user
ENV HOME=/home/user \
    FASTEMBED_CACHE_PATH=/home/user/.cache/fastembed \
    OPENBRO_WEB_DATA=/home/user/.openbro/web \
    PORT=7860

RUN python -m openbro.web.prepare --data-dir "$OPENBRO_WEB_DATA" /app/README.md /app/docs

EXPOSE 7860
CMD ["sh", "-c", "openbro web --host 0.0.0.0 --port ${PORT} --trust-proxy --data-dir ${OPENBRO_WEB_DATA} --docs /app/README.md --docs /app/docs"]
