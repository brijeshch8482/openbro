# Deploying the OpenBro web demo

`openbro web` serves a public chat page (see the README, "Web demo"). This
guide puts it online.

**What runs where:** the LLM is **not** deployed — it stays with the API
provider (Groq's free tier by default) and the server calls it with your
key. The server itself only runs OpenBro: the agent loop, the chat page and
document search over README + `docs/`, with a small embedding model.

You need a free [Groq API key](https://console.groq.com/keys).

## Option A — Render (free, recommended)

Render's free web service runs our `Dockerfile` straight from GitHub:
0.1 CPU and 512 MB RAM, enough for the demo (it uses about 240 MB). It
sleeps after 15 minutes without visitors; the next visit wakes it in about
a minute (Render shows a loading page meanwhile).

1. Sign in at [render.com](https://render.com) with GitHub.
2. **New → Blueprint**, connect the `openbro` repository, branch `main`.
   Render reads `render.yaml` from the repo root.
3. Enter `GROQ_API_KEY` when asked, then **Deploy Blueprint**.
4. The first build takes a few minutes. Your link is
   `https://openbro-<something>.onrender.com`.

Every push to `main` redeploys automatically.

## Option B — Hugging Face Spaces (needs PRO)

Hugging Face now requires a PRO plan for Docker Spaces (Static Spaces stay
free but cannot run a server). With PRO, create a Space with SDK **Docker**,
add the two files from `deploy/huggingface/`, and set `GROQ_API_KEY` under
**Settings → Variables and secrets**. The Space clones this repo when it
builds; use **Settings → Factory rebuild** to update.

## Option C — any container host

```bash
docker build -t openbro-web .
docker run -p 7860:7860 -e GROQ_API_KEY=gsk_... openbro-web
# open http://localhost:7860
```

The container listens on `$PORT` (default 7860). On a VPS, put Caddy or
nginx in front for HTTPS.

## Settings

| Variable | Default | What it does |
|---|---|---|
| `GROQ_API_KEY` | — | the LLM key (required) |
| `OPENBRO_WEB_MODEL` | `openai/gpt-oss-120b` | Groq model; two fallbacks are tried when it is busy |
| `OPENBRO_WEB_EMBED_MODEL` | `BAAI/bge-small-en-v1.5` (build arg) | document-search embedding model; `none` = keywords only (~80 MB RAM); empty = the multilingual default (~640 MB RAM) |

The embedding model and the document index are built into the image by
`python -m openbro.web.prepare`, so the page answers seconds after the
container starts. The container runs as an unprivileged user and keeps no
visitor data: each chat lives in memory and expires after 30 idle minutes.

## Limits and cost

Built for free tiers. Groq's free tier allows about 8,000 tokens per minute
per model; OpenBro rotates across three models (`gpt-oss-120b`,
`qwen3.8-27b`, `gpt-oss-20b`) when one is busy. Visitors are limited to 20
messages per hour each and 300 per hour in total (`openbro web --per-ip N`
changes the first). If many people use it at once, answers slow down or ask
them to wait a minute — nothing is billed.
