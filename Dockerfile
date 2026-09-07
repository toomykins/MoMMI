# Pinning the interpreter here is the point: the host's Python version stops
# mattering, which is what actually bit v2 when Python 3.6 fell out of distros.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# git: for the changelog generator. bubblewrap: the DM code sandbox.
# BYOND is 32-bit, so pull the i386 runtime it links against.
RUN dpkg --add-architecture i386 \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      git ca-certificates bubblewrap unzip curl \
      libc6:i386 libstdc++6:i386 libcurl4:i386 \
 && rm -rf /var/lib/apt/lists/*

# BYOND toolchain for DM code execution (`runcode` dm). Pinned; off unless a
# server enables codehandling. Lands on PATH so the cog auto-discovers it.
ARG BYOND_MAJOR=516
ARG BYOND_VERSION=516.1684
RUN curl -fsSL "https://www.byond.com/download/build/${BYOND_MAJOR}/${BYOND_VERSION}_byond_linux.zip" -o /tmp/byond.zip \
 && unzip -q /tmp/byond.zip -d /opt \
 && rm /tmp/byond.zip
ENV BYOND_SYSTEM=/opt/byond \
    LD_LIBRARY_PATH=/opt/byond/bin \
    PATH=/opt/byond/bin:$PATH

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[charts]"

RUN useradd --create-home --uid 1000 mommi \
 && mkdir -p /app/config /app/data \
 && chown -R mommi:mommi /app
USER mommi

VOLUME ["/app/config", "/app/data"]
EXPOSE 1679 40000

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:40000/twohundred', timeout=3).status==200 else 1)"

ENTRYPOINT ["mommi"]
CMD ["--config-dir", "/app/config", "--data-dir", "/app/data"]
