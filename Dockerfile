# Official uv + Python base — reproducible installs from the committed lockfile.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# System libraries the vision stack needs on a slim image: opencv links libglib
# (libgthread), and onnxruntime needs OpenMP. Without these the wheels install
# fine and then fail at import, which is a runtime error, not a build one.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Dependency layer first (cached across app-code changes).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# App code + rulebook.
COPY . .
RUN uv sync --frozen --no-dev

EXPOSE 8080
# --no-sync: use the environment already built above; don't re-resolve at boot
# (that removed installed packages and slowed cold start).
CMD ["uv", "run", "--no-sync", "uvicorn", "label_assay.web.app:app", "--host", "0.0.0.0", "--port", "8080"]
