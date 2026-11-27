# OpenBro web demo — `openbro web` in a container.
#
#   docker build -t openbro-web .
#   docker run -p 7860:7860 -e GROQ_API_KEY=gsk_... openbro-web
#
# Works on any container host (Render, Railway, Fly.io, a VPS). For
# Hugging Face Spaces use deploy/huggingface/ — see docs/DEPLOY.md.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Run as an unprivileged user (uid 1000, which Hugging Face Spaces expects).
RUN useradd --create-home --uid 1000 user
WORKDIR /app
COPY --chown=user . /app
RUN pip install ".[web,rag]"

USER user
ENV HOME=/home/user \
    FASTEMBED_CACHE_PATH=/home/user/.cache/fastembed \
    OPENBRO_WEB_DATA=/home/user/.openbro/web \
    PORT=7860

# Bake the embedding model and the demo index into the image so the
# container answers seconds after it starts.
RUN python -m openbro.web.prepare --data-dir "$OPENBRO_WEB_DATA" /app/README.md /app/docs

EXPOSE 7860
CMD ["sh", "-c", "openbro web --host 0.0.0.0 --port ${PORT} --trust-proxy --data-dir ${OPENBRO_WEB_DATA} --docs /app/README.md --docs /app/docs"]
