# Pinning the interpreter here is the point: the host's Python version stops
# mattering, which is what actually bit v2 when Python 3.6 fell out of distros.
FROM python:3.12-slim

# Nothing in MoMMI needs a compiler; matplotlib and the rest ship wheels.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# git is needed by the changelog generator, which shells out to it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[charts]"

RUN useradd --create-home --uid 1000 mommi \
 && mkdir -p /app/config /app/data \
 && chown -R mommi:mommi /app
USER mommi

VOLUME ["/app/config", "/app/data"]

# Commloop (game server) and the HTTP front end (webhooks, nudges).
EXPOSE 1679 40000

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:40000/twohundred', timeout=3).status==200 else 1)"

ENTRYPOINT ["mommi"]
CMD ["--config-dir", "/app/config", "--data-dir", "/app/data"]
