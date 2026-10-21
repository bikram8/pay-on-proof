# One image: React dashboard built with Node, served by the FastAPI backend.
#   docker build -t pay-on-proof .
#   docker run -p 8000:8000 --env-file backend/.env -e PUBLIC_BASE_URL=http://localhost:8000/api \
#     -e FRONTEND_URL=http://localhost:8000 pay-on-proof
# Then open http://localhost:8000

FROM node:22.11.0-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12.8-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    FRONTEND_DIST=/app/frontend/dist \
    DATABASE_URL=sqlite:////data/payonproof.db \
    UPLOAD_DIR=/data/uploads
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=frontend /frontend/dist /app/frontend/dist
RUN mkdir -p /data/uploads
EXPOSE 8000
# Render sets $PORT; default 8000 for local docker run.
CMD ["sh", "-c", "uvicorn app.server:root --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
