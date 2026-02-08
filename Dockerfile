# =============================================================================
# File Organizer v2 - Multi-stage Docker Build
# =============================================================================
# Stage 1: Builder - install dependencies and build the package
# Stage 2: Runtime - minimal image with only runtime dependencies
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Builder
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy only dependency specification first (for layer caching)
COPY file_organizer_v2/pyproject.toml ./file_organizer_v2/
COPY file_organizer_v2/src/ ./file_organizer_v2/src/

# Install the package and dependencies into a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir ./file_organizer_v2

# ---------------------------------------------------------------------------
# Stage 2: Runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# Set labels for the container image
LABEL org.opencontainers.image.title="File Organizer v2" \
      org.opencontainers.image.description="AI-powered local file management system" \
      org.opencontainers.image.source="https://github.com/curdriceaurora/Local-File-Organizer" \
      org.opencontainers.image.licenses="MIT OR Apache-2.0"

# Install runtime system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        ffmpeg \
        libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Install Ollama
RUN curl -fsSL https://ollama.com/install.sh | sh

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create non-root user for security
RUN groupadd --gid 1000 organizer && \
    useradd --uid 1000 --gid organizer --shell /bin/bash --create-home organizer

# Create data directory for user file mounts
RUN mkdir -p /data/input /data/output /data/models && \
    chown -R organizer:organizer /data

# Set working directory
WORKDIR /app

# Set ownership
RUN chown -R organizer:organizer /app

# Switch to non-root user
USER organizer

# Environment configuration
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FILE_ORGANIZER_DATA_DIR=/data \
    FILE_ORGANIZER_INPUT_DIR=/data/input \
    FILE_ORGANIZER_OUTPUT_DIR=/data/output \
    OLLAMA_HOST=http://localhost:11434

# Volume for user data
VOLUME ["/data"]

# Expose port for future web API
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default entrypoint: start Ollama in background, then run the file organizer
ENTRYPOINT ["/bin/bash", "-c", "ollama serve & sleep 5 && exec \"$@\"", "--"]
CMD ["file-organizer", "--help"]
