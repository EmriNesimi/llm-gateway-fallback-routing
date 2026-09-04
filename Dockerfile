# Pinned by digest as well as tag. The tag moves — python:3.12-slim is
# rebuilt whenever its base or an OS package changes — so two builds of
# the same commit could ship different userland, and "works on main"
# stops being a statement about a commit.
#
# This is the multi-arch index digest, not a per-platform one, so the
# release workflow's arm64 build still resolves. The tag stays for
# readability and is what the seven-way Python version guard reads.
# Dependabot updates digest and tag together, so this does not freeze
# security patches — it makes taking them a commit.
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6

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

# Drop root. Nothing here needs it: the dependencies are already installed,
# and /app is the only path written to (the default SQLite database), so it's
# chowned rather than left root-owned. A container escape is a much smaller
# problem from an unprivileged uid.
RUN useradd --create-home --uid 10001 gateway && chown -R gateway:gateway /app
USER gateway

# No curl in the slim image, so use Python (already present) to hit /readyz —
# this checks Redis/DB connectivity, not just that the process is alive.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/readyz').status == 200 else 1)"

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
