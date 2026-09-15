# Built from the wardriving repo root, so the review tool can be copied in alongside the app:
#   build:
#     context: ../wardriving
#     dockerfile: analyzer/Dockerfile
# Dockerfile.dockerignore (next to this file) keeps the context down to what's needed.

FROM node:24-slim AS builder
WORKDIR /app
COPY analyzer/package*.json ./
RUN npm ci
COPY analyzer/ ./
RUN npm run build

# Node 24+ for the built-in node:sqlite (reads the Kismet databases); python3 runs
# tools/wardrive_review.py, which is standard library only.
FROM node:24-slim
ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=3000 \
    WARDRIVE_DATA=/data/wardrive \
    WARDRIVE_PYTHON=python3 \
    WARDRIVE_REVIEW_TOOL=/app/tools/wardrive_review.py \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends ca-certificates python3 && \
    rm -rf /var/lib/apt/lists/* && \
    chown node:node /app

USER node
COPY --from=builder --chown=node:node /app/package*.json ./
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force
COPY --from=builder --chown=node:node /app/build ./build
COPY --chown=node:node tools/wardrive_review.py ./tools/wardrive_review.py

EXPOSE 3000
CMD ["node", "--disable-warning=ExperimentalWarning", "build"]
