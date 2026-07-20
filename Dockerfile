# Stage 1: Build the frontend React/Vite app
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Build the backend FastAPI app
FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy built frontend static files to be served by FastAPI
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# Copy backend application
COPY backend/ /app/backend/

EXPOSE 8000

ENV PYTHONPATH=/app
ENV APP_ENV=production

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
