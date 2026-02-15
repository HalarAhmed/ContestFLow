# CP Assistant: FastAPI + MongoDB + Playwright (contest registration).
# Build: docker build -t cp-assistant .
# Run:   docker run -p 8000:8000 -e MONGODB_URI=mongodb://... cp-assistant
FROM python:3.11-slim-bookworm

WORKDIR /app

# Chromium deps for Playwright (install before playwright install)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libnss3 libxss1 libasound2 libxtst6 libgtk-3-0 libgbm1 \
        fonts-noto-color-emoji wget ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install chromium

COPY . .
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "python run_api.py"]
