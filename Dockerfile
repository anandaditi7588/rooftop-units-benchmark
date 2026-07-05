# RTU Heat Pump Benchmarking — container image for Render/Railway/Fly.io/etc.
#
# Renders/Railway-style platforms inject a $PORT env var the app must bind
# to (not always 8000), so the CMD uses shell-form substitution for that.
FROM python:3.12-slim

WORKDIR /app

# ca-certificates: needed for outbound HTTPS (scraping manufacturer sites).
# truststore falls back to the system trust store on Linux via this package.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Physical_Data.xlsx and any manually-curated source_documents/*.pdf already
# in the build context are baked into the image here, so they survive
# restarts/redeploys even though the rest of /uploads, /output, /logs are
# ephemeral on most platforms' free tiers (see README's deployment section).

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
