# uv + Python base. The Python package layer is reproducible from the committed
# lockfile (uv sync --frozen); the base tag itself tracks upstream uv/Python/
# Debian patch releases.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# System libraries the vision stack needs on a slim image. The wheels install
# fine and then fail at *import* without these, so a green build proves nothing —
# /health self-checks the OCR engine for exactly this reason.
#
# RapidOCR pulls the full opencv-python (not headless), which links X11/GL at
# import even though nothing here ever draws a window; onnxruntime needs OpenMP.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        libgomp1 \
        libgl1 \
        libxcb1 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Dependency layer first (cached across app-code changes).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# App code + rulebook.
COPY . .
RUN uv sync --frozen --no-dev

# Drop privileges for the runtime. The process needs only read access to /app:
# batch uploads spool through tempfile (TMPDIR, /tmp by default), the OCR
# models ship inside the installed wheel, and nothing writes the app tree at
# runtime. --create-home gives library caches (~/.cache) a writable landing
# spot; the chown keeps `uv run` unsurprised by ownership regardless of the
# build user's umask.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080
# --no-sync: use the environment already built above; don't re-resolve at boot
# (that removed installed packages and slowed cold start).
# --no-server-header: don't advertise the server family ("Server: uvicorn") to
# every client; HF's edge proxy passes the banner through unchanged.
CMD ["uv", "run", "--no-sync", "uvicorn", "label_assay.web.app:app", "--host", "0.0.0.0", "--port", "8080", "--no-server-header"]
