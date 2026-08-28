FROM python:3.12-slim

# Without this, Python buffers stdout when it isn't a TTY — which is exactly
# the case under Docker — so `docker logs` shows nothing until the buffer
# fills or the process exits. On a crash that means losing the log lines that
# would explain it.
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic.ini .
COPY migrations ./migrations
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# No curl in the slim image, so use Python (already present) to hit /readyz —
# this checks Redis/DB connectivity, not just that the process is alive.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/readyz').status == 200 else 1)"

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
