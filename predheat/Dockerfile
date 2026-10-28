# Heat Pump Pilot Home Assistant add-on image.
# Stage 1 builds the React dashboard; stage 2 installs the Python service and
# serves the built dashboard from /app/static.

FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml DOCS.md ./
COPY src ./src
RUN pip install --no-cache-dir . && mkdir -p /data
COPY --from=frontend /build/dist ./static
ENV PREDHEAT_FRONTEND_DIR=/app/static \
    PREDHEAT_DATA_DIR=/data \
    PREDHEAT_API_HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1
EXPOSE 8750
CMD ["python", "-m", "predheat.addon"]
