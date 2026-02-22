# Multi-stage build: OrcaSlicer CLI + fabprint
#
# Usage:
#   docker build -t fabprint .
#   docker run --rm -v "$PWD:/project" fabprint slice fabprint.toml

# ---------------------------------------------------------------------------
# Stage 1: Extract OrcaSlicer from AppImage
# ---------------------------------------------------------------------------
FROM ubuntu:24.04 AS orca

ARG ORCA_VERSION=2.3.1

WORKDIR /tmp
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fSL -o orca.AppImage \
        "https://github.com/SoftFever/OrcaSlicer/releases/download/v${ORCA_VERSION}/OrcaSlicer_Linux_AppImage_Ubuntu2404_V${ORCA_VERSION}.AppImage" \
    && chmod +x orca.AppImage \
    && ./orca.AppImage --appimage-extract \
    && mv squashfs-root /opt/orca-slicer \
    && rm orca.AppImage

# ---------------------------------------------------------------------------
# Stage 2: Runtime image
# ---------------------------------------------------------------------------
FROM ubuntu:24.04

# OrcaSlicer runtime deps (needed even for CLI — links GTK/GL at startup)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libgl1-mesa-dri libegl1 \
        libgtk-3-0 \
        libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy extracted OrcaSlicer
COPY --from=orca /opt/orca-slicer /opt/orca-slicer
RUN ln -s /opt/orca-slicer/bin/orca-slicer /usr/bin/orca-slicer

# Wire up system profiles so fabprint can discover them
# fabprint looks at ~/.config/OrcaSlicer/system/BBL on Linux
ENV HOME=/home/fabprint
RUN useradd -m -d /home/fabprint fabprint \
    && mkdir -p /home/fabprint/.config/OrcaSlicer/system \
    && ln -s /opt/orca-slicer/resources/profiles/BBL \
             /home/fabprint/.config/OrcaSlicer/system/BBL

# Install uv (fast Python package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install fabprint
WORKDIR /opt/fabprint
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev --no-editable \
    && uv cache clean

ENV PATH="/opt/fabprint/.venv/bin:$PATH"

USER fabprint
WORKDIR /project
ENTRYPOINT ["fabprint"]
CMD ["--help"]
