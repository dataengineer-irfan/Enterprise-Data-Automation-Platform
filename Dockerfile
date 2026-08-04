FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

WORKDIR /app/.ui_verify
COPY .ui_verify/package.json .ui_verify/package-lock.json* ./
RUN npm install --legacy-peer-deps --no-audit --no-fund

WORKDIR /app
COPY . .

EXPOSE 8000 8007

CMD ["sh", "-c", "python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000 & python -m http.server 8007"]
