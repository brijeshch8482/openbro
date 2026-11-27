# Deploying the OpenBro web demo

`openbro web` serves a public chat page (see the README, "Web demo"). This
guide puts it online. You need a free LLM key — a [Groq](https://console.groq.com/keys)
key works out of the box.

## Option A — Hugging Face Spaces (free, recommended)

A Space gives you a public link like `https://huggingface.co/spaces/<you>/openbro`.
The free CPU tier is enough; it sleeps when nobody visits and wakes on the
next visit (about 30 seconds).

1. Sign in at [huggingface.co](https://huggingface.co) and choose **New Space**.
   - Name: `openbro` · SDK: **Docker** · template: **Blank** · hardware: **CPU basic (free)** · visibility: **Public**.
2. Add the two files from `deploy/huggingface/` to the Space — either upload
   them in the Space's **Files** tab, or with git:

   ```bash
   git clone https://huggingface.co/spaces/<you>/openbro hf-openbro
   cp deploy/huggingface/README.md deploy/huggingface/Dockerfile hf-openbro/
   cd hf-openbro && git add . && git commit -m "OpenBro web demo" && git push
   ```

3. In the Space: **Settings → Variables and secrets → New secret**
   `GROQ_API_KEY` = your key. (Optional variable `OPENBRO_WEB_MODEL` to pick
   another Groq model.)
4. The Space builds (about 5 minutes the first time) and shows the chat page.

**Updating:** the Space clones the GitHub repo when it builds. After merging
changes, open **Settings → Factory rebuild**.

## Option B — any container host

The root `Dockerfile` builds from your checkout:

```bash
docker build -t openbro-web .
docker run -p 7860:7860 -e GROQ_API_KEY=gsk_... openbro-web
# open http://localhost:7860
```

On Render, Railway or Fly.io, point a new web service at the GitHub repo,
choose the Dockerfile, and set `GROQ_API_KEY`. The container listens on
`$PORT` (default 7860). On a VPS, run the container behind Caddy or nginx
for HTTPS.

## What the image contains

- OpenBro with the `web` and `rag` extras.
- The embedding model (`paraphrase-multilingual-MiniLM-L12-v2`, ~220 MB)
  and a ready index of `README.md` + `docs/`, built during `docker build`
  by `python -m openbro.web.prepare`, so the page answers seconds after start.
- It runs as an unprivileged user and keeps no visitor data: each chat lives
  in memory and expires after 30 idle minutes.

## Limits and cost

The demo is built for free tiers. Groq's free tier allows about 8,000
tokens per minute per model; OpenBro rotates across three models
(`gpt-oss-120b`, `qwen3.8-27b`, `gpt-oss-20b`) when one is busy.
Visitors are limited to 20 messages per hour each and 300 per hour in total
(`openbro web --per-ip N` to change the first). If many people use it at
once, answers slow down or ask them to wait a minute — nothing is billed.
