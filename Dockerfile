FROM node:22-bookworm@sha256:0e5f906573693feaa1e21057ebdcfdb5bd5021f050b2dc7c9deceb629c7da2a8 AS node

FROM python:3.13-bookworm@sha256:073ffebb96ae4d0ed73ccad59f08c47bd84af8a79f5258130f8458ac50ad9ff4

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && npm install --global pnpm@11.25.0

ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1
WORKDIR /workspace
COPY . .
RUN python manage.py setup \
    && pnpm --dir apps/web exec playwright install --with-deps chromium

CMD ["python", "manage.py", "test-browser-live"]
