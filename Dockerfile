FROM node:22-alpine AS frontend
WORKDIR /build
RUN npm install -g pnpm@11.25.0
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SMS_DATA_DIR=/data SMS_STATIC_DIR=/app/static
WORKDIR /app
COPY backend/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock && groupadd -g 10001 xinbox && useradd -u 10001 -g xinbox -M xinbox && mkdir /data && chown xinbox:xinbox /data
COPY backend/*.py ./
COPY --from=frontend /build/dist ./static
USER 10001:10001
EXPOSE 8787
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8787", "--workers", "1", "--no-proxy-headers", "--no-access-log", "--limit-concurrency", "64", "--timeout-keep-alive", "5"]
