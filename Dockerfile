FROM node:24-bookworm AS node

FROM python:3.12-bookworm

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && npm install --global pnpm@11.25.0

ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1
WORKDIR /workspace
COPY . .
RUN python manage.py setup \
    && pnpm --dir .cache/web exec playwright install --with-deps chromium

CMD ["python", "manage.py", "test-browser-live"]
