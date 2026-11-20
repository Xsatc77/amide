# The base image is pinned by digest (the multi-arch index of python:3.13-slim). Dependabot proposes new digests weekly; to refresh by hand,
# read the Docker-Content-Digest of the python:3.13-slim manifest list (docker buildx imagetools inspect python:3.13-slim) and paste it here.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    AMIDE_DATA_DIR=/data

# OCR for scanned price lists (RapidOCR -> OpenCV, the headless build) needs this one system library on the slim image.
# zlib1g is named so the image takes the Debian security update of it even when the pinned base image is older.
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 zlib1g && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Every package comes from the hash-pinned lock (requirements.lock, made by tools/make_lock.py): pip refuses anything whose hash differs.
COPY requirements.lock .
# pip, setuptools and wheel are removed afterwards: Amide never needs them at run time, and the copies of urllib3, msgpack and setuptools they
# bundle are what image scanners flag.
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.lock && pip uninstall -y pip setuptools wheel

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app
COPY public ./public

# The release this image was built from, shown in the footer and in Settings (the build passes it in).
ARG AMIDE_VERSION=development
ENV AMIDE_VERSION=$AMIDE_VERSION

RUN useradd --create-home --uid 1000 amide && mkdir -p /data && chown amide:amide /data
USER amide
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
