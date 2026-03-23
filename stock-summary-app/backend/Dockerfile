# 클라우드(Render/Fly/Railway 등)에 올려 맥이 꺼져 있어도 접속 가능하게 함
FROM python:3.12-slim-bookworm

WORKDIR /app

# 일부 네이티브 휠 실패 시 빌드용 (필요 없으면 제거 가능)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY web/ ./web/

# Render/Fly/Railway 등이 주입하는 PORT 사용
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
