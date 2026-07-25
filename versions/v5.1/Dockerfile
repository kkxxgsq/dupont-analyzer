FROM python:3.12-slim

LABEL version="v5.1" description="杜邦分析 v5.1 - Dupont Analyzer"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/cache

EXPOSE 8765

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/', timeout=5)" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8765", "--workers", "2"]
