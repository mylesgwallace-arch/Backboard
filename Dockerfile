# Backboard API + web front end in one container (stdlib HTTP server).
#
# The database and model inputs are NOT baked into the image: data/ is about
# 1 GB, untracked, and changes daily. Mount it at /app/data:
#
#   docker build -t backboard .
#   docker run -p 8000:8000 -v "$PWD/data:/app/data" backboard
#
# Behind a reverse proxy (TLS, a public host), set BACKBOARD_TRUST_PROXY=1 so
# rate limits key on the client address in X-Forwarded-For. The daily refresh
# runs outside the server process: scripts/refresh_live_data.sh (cron) against
# the same data/ directory.
FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY web/ web/
COPY models/ models/
COPY templates/ templates/

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["python", "src/api.py", "--host", "0.0.0.0", "--port", "8000"]
