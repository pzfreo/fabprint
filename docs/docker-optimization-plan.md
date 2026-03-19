# Docker Optimization Plan

Goal: speed up Docker usage for both the OrcaSlicer (fabprint/fabprint) and
cloud-bridge (fabprint/cloud-bridge) images without sacrificing robustness.

---

## 1. Skip redundant `docker pull` in cloud-bridge

**Problem:** Every call to `_run_bridge()` in `src/fabprint/cloud/bridge.py:90`
runs `docker pull fabprint/cloud-bridge`, adding 5-15 seconds even when the
image is already up to date. This happens on every print, status check, cancel,
and task list.

**Fix:** Add a staleness check so we only pull when the local image is older
than a configurable threshold (default 24 hours).

### Steps

1. Add a module-level constant `_PULL_INTERVAL_SECONDS = 86400` (24h) in
   `src/fabprint/cloud/bridge.py`.
2. Add a helper `_should_pull_image() -> bool` that:
   - Reads a timestamp file at `~/.cache/fabprint/cloud-bridge-pull-ts`.
   - Returns `True` if the file is missing or older than `_PULL_INTERVAL_SECONDS`.
   - Returns `False` otherwise.
3. Add a helper `_record_pull()` that writes `time.time()` to the timestamp
   file (creating parent dirs as needed).
4. In `_run_bridge()`, replace the unconditional `docker pull` with:
   ```python
   if _should_pull_image():
       pull = subprocess.run(["docker", "pull", DOCKER_IMAGE], ...)
       if pull.returncode == 0:
           _record_pull()
   ```
5. Add an env-var override `FABPRINT_DOCKER_PULL=always|never|auto` for
   debugging and CI (default `auto`).

### Tests (already written)

- `TestRunBridgeDockerPull::test_pulls_image_when_using_docker` — pull happens
- `TestRunBridgeDockerPull::test_pull_failure_still_runs_container` — graceful degradation

### New tests to add

- `test_skips_pull_when_recent` — mock timestamp file < 24h old, verify no pull
- `test_pulls_when_stale` — mock timestamp file > 24h old, verify pull happens
- `test_pull_always_override` — env var `FABPRINT_DOCKER_PULL=always` forces pull
- `test_pull_never_override` — env var `FABPRINT_DOCKER_PULL=never` skips pull

### Estimated savings

5-15 seconds per cloud-bridge invocation (status, print, cancel, tasks).

---

## 2. Split dependency layer from source in OrcaSlicer Dockerfile

**Problem:** In `Dockerfile:60-64`, `pyproject.toml`, `uv.lock`, and `src/` are
all copied before `uv sync`. Any change to Python source code busts the
dependency install cache, causing a full reinstall (~30-60s).

**Fix:** Copy lockfiles first, install deps, then copy source.

### Steps

1. In `Dockerfile`, replace lines 58-64:
   ```dockerfile
   # Install dependencies (cached unless lockfile changes)
   WORKDIR /opt/fabprint
   COPY pyproject.toml uv.lock README.md LICENSE ./
   RUN uv python install 3.12 \
       && uv sync --frozen --no-dev --no-editable --python 3.12

   # Copy source and re-link (fast — deps already installed)
   COPY src/ ./src/
   RUN uv sync --frozen --no-dev --no-editable --python 3.12 \
       && uv cache clean
   ```
2. Verify with `docker build .` that a source-only change reuses the dep layer.

### Tests

No new Python tests needed. Existing CI integration tests
(`test_slice_docker` in `tests/test_cli.py`) validate the built image.

### Verification

```bash
# Change a .py file, rebuild — should see "CACHED" on the uv sync layer
docker build --progress=plain . 2>&1 | grep -i cached
```

### Estimated savings

30-60 seconds on source-only rebuilds.

---

## 3. Add BuildKit cache mounts for apt and uv

**Problem:** Both Dockerfiles re-download apt packages and Python wheels on
every build, even when the package set hasn't changed.

**Fix:** Use `--mount=type=cache` for apt and uv caches.

### Steps

1. In `Dockerfile` (stage 2, line 34), replace the apt-get RUN:
   ```dockerfile
   RUN --mount=type=cache,target=/var/cache/apt \
       --mount=type=cache,target=/var/lib/apt/lists \
       apt-get update && apt-get install -y --no-install-recommends \
           libgl1 libgl1-mesa-dri libegl1 \
           libgtk-3-0 \
           libwebkit2gtk-4.1-0 \
           libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 \
           xvfb \
           ca-certificates
   ```
   (Remove `&& rm -rf /var/lib/apt/lists/*` since the cache mount handles it.)

2. In `Dockerfile` (stage 1, line 15), same pattern for the curl/ca-certificates
   install.

3. In `Dockerfile`, replace the uv sync RUN with a cache mount:
   ```dockerfile
   RUN --mount=type=cache,target=/root/.cache/uv \
       uv sync --frozen --no-dev --no-editable --python 3.12
   ```
   Remove `uv cache clean` since the cache is external to the image.

4. In `Dockerfile.cloud-bridge` (line 20), same pattern for apt-get.

5. Ensure CI workflow uses `DOCKER_BUILDKIT=1` (already the case with
   `docker/build-push-action`).

### Tests

No new Python tests needed. Dockerfile-only change.

### Verification

```bash
# Second build should show cache hits
DOCKER_BUILDKIT=1 docker build --progress=plain . 2>&1 | grep "cache"
```

### Estimated savings

10-30 seconds per rebuild (avoids re-downloading ~200MB of apt packages).

---

## 4. Multi-stage cloud-bridge to drop g++

**Problem:** `Dockerfile.cloud-bridge` keeps `g++` (~100MB+) in the final image
even though it's only used to compile one binary. This inflates image size and
pull times.

**Fix:** Use a builder stage for compilation.

### Steps

1. Restructure `Dockerfile.cloud-bridge` into two stages:

   ```dockerfile
   # Stage 1: compile the bridge binary
   FROM --platform=linux/amd64 ubuntu:24.04 AS builder
   RUN apt-get update && apt-get install -y --no-install-recommends g++
   COPY scripts/bambu_cloud_bridge.cpp /opt/bridge/
   RUN g++ -std=c++17 -O2 \
       -o /usr/local/bin/bambu_cloud_bridge \
       /opt/bridge/bambu_cloud_bridge.cpp \
       -ldl -lpthread

   # Stage 2: runtime (no compiler)
   FROM --platform=linux/amd64 ubuntu:24.04
   RUN apt-get update && apt-get install -y --no-install-recommends \
       ca-certificates curl libcurl4 python3 unzip \
       && rm -rf /var/lib/apt/lists/*

   # ... (fetch libbambu_networking.so and cert — unchanged) ...

   COPY --from=builder /usr/local/bin/bambu_cloud_bridge /usr/local/bin/
   ```

2. Build and verify: `docker run --rm fabprint/cloud-bridge:test --help`

3. Compare image sizes:
   ```bash
   docker images fabprint/cloud-bridge --format '{{.Size}}'
   ```

### Tests

- Existing `TestRunBridgeDockerPull` tests validate the bridge runs correctly.
- Add a CI smoke test step to `publish-cloud-bridge.yml`:
  ```yaml
  - name: Smoke test cloud-bridge image
    run: docker run --rm fabprint/cloud-bridge:${{ env.VERSION }} --help
  ```

### Estimated savings

~100MB smaller image, faster pulls on first use.

---

## 5. Publish an OrcaSlicer base image

**Problem:** The OrcaSlicer AppImage extraction + system deps (~800MB) take
minutes to build but rarely change. Every fabprint code change triggers a full
rebuild of these layers (unless local Docker cache is warm).

**Fix:** Publish a pre-built base image with OrcaSlicer + runtime deps.

### Steps

1. Create `Dockerfile.orca-base`:
   ```dockerfile
   # Base image: OrcaSlicer + runtime deps (rebuild only on Orca version bump)
   FROM --platform=linux/amd64 ubuntu:24.04 AS orca
   ARG ORCA_VERSION=2.3.1
   WORKDIR /tmp
   RUN apt-get update && apt-get install -y --no-install-recommends \
       curl ca-certificates && rm -rf /var/lib/apt/lists/*
   RUN curl -fSL -o orca.AppImage \
       "https://github.com/SoftFever/OrcaSlicer/releases/download/v${ORCA_VERSION}/OrcaSlicer_Linux_AppImage_Ubuntu2404_V${ORCA_VERSION}.AppImage" \
       && chmod +x orca.AppImage \
       && ./orca.AppImage --appimage-extract \
       && mv squashfs-root /opt/orca-slicer \
       && rm orca.AppImage

   FROM --platform=linux/amd64 ubuntu:24.04
   ARG ORCA_VERSION=2.3.1
   LABEL fabprint.orca-version="${ORCA_VERSION}"
   RUN apt-get update && apt-get install -y --no-install-recommends \
       libgl1 libgl1-mesa-dri libegl1 libgtk-3-0 libwebkit2gtk-4.1-0 \
       libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 xvfb ca-certificates \
       && rm -rf /var/lib/apt/lists/*
   COPY --from=orca /opt/orca-slicer /opt/orca-slicer
   RUN ln -s /opt/orca-slicer/bin/orca-slicer /usr/bin/orca-slicer
   ENV HOME=/home/fabprint
   RUN useradd -m -d /home/fabprint fabprint \
       && mkdir -p /home/fabprint/.config/OrcaSlicer/system \
       && ln -s /opt/orca-slicer/resources/profiles/BBL \
                /home/fabprint/.config/OrcaSlicer/system/BBL
   ```

2. Simplify `Dockerfile` to use the base:
   ```dockerfile
   ARG ORCA_VERSION=2.3.1
   FROM fabprint/orca-base:${ORCA_VERSION}
   COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
   WORKDIR /opt/fabprint
   COPY pyproject.toml uv.lock README.md LICENSE ./
   RUN uv python install 3.12 \
       && uv sync --frozen --no-dev --no-editable --python 3.12
   COPY src/ ./src/
   RUN uv sync --frozen --no-dev --no-editable --python 3.12 \
       && uv cache clean
   ENV PATH="/opt/fabprint/.venv/bin:$PATH"
   USER fabprint
   WORKDIR /project
   ENTRYPOINT ["fabprint"]
   CMD ["--help"]
   ```

3. Add a separate CI workflow `publish-orca-base.yml`:
   - Triggered manually or when `Dockerfile.orca-base` changes.
   - Publishes `fabprint/orca-base:2.3.1` to Docker Hub + GHCR.

4. Update `publish-cloud-bridge.yml` to use the base image for the orca build.

5. Update `scripts/build-docker.sh` to support `build-docker.sh orca-base 2.3.1`.

### Tests

No new Python tests. Existing `test_slice_docker*` integration tests validate
the final image works correctly.

### Estimated savings

Minutes on code-only rebuilds (base image pull ~30s vs rebuild ~3-5 min).

---

## ~~6. Expand PersistentBridge usage for multi-step workflows~~ — SKIPPED

Decided not to pursue. The 2-3s container startup savings per call isn't worth
the added complexity for typical usage patterns.

---

## Implementation order

| Phase | Change | Risk | Effort | Status |
|-------|--------|------|--------|--------|
| 1 | Skip redundant pull (#1) | Low | Small | Done (PR #159) |
| 2 | Split dep layer (#2) + cache mounts (#3) | Low | Small | Done (PR #160) |
| 3 | Multi-stage cloud-bridge (#4) | Low | Small | Done (PR #160) |
| 4 | OrcaSlicer base image (#5) | Medium | Medium | Done (PRs #161, #162, #163) |
| 5 | PersistentBridge expansion (#6) | Medium | Medium | Skipped — not worth the complexity |

---

## Test coverage summary

| Area | Tests | Status |
|------|-------|--------|
| Pull staleness (#1) | 6 (pull, graceful fail, stale, recent, env overrides) | Done |
| Dep layer split (#2) | CI integration | Done |
| Cache mounts (#3) | CI integration | Done |
| Multi-stage bridge (#4) | 7 (_run_bridge) | Done |
| Base image (#5) | CI integration | Done |
| PersistentBridge (#6) | 8 (enter/exit/status) | Skipped |
