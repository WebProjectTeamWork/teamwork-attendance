FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .
# RapidOCR downloads its models on first use; fetch them now so a fresh container never waits on the network.
RUN python -c "from rapidocr import RapidOCR; RapidOCR()"

COPY app ./app
COPY docs ./docs

EXPOSE 8000
# Hosts may set PORT. --proxy-headers makes the real client IP and https scheme visible
# behind the host's proxy, which the per-IP rate limits and the Secure cookie rely on.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --proxy-headers --forwarded-allow-ips="*"
