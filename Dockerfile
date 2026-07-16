# Thomas Agent MVP — operator control-channel service (R4.5).
#
# Runs the poll -> verify -> handle -> reply loop as a long-lived service. The image carries
# ONLY committed, non-secret source. Per-machine governance state (Core pointer, operator
# registration, safety-flag activation, control state, ledger) and secrets (bot token, API key)
# are provided at runtime via a mounted volume and environment variables — never baked in.
# The Safety-Flag Gate still governs every network capability: with no mounted activation the
# real Telegram/provider paths fail closed, so a bare image cannot open a network socket.
FROM python:3.12-slim

# Match CI (Python 3.12). Unbuffered logs, UTF-8 I/O for non-ASCII requests, no .pyc writes.
ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install runtime deps first so the dependency layer caches across source changes.
COPY requirements-runtime.txt ./
RUN pip install --no-cache-dir -r requirements-runtime.txt

# Copy only the committed source the runtime actually reads. /app is the repo root the code
# resolves via parents[2]. The .dockerignore keeps local THOMAS_CORE state (approvals/
# activations/CURRENT_CORE_RELEASE) and .runtime_governance_state out of the build context.
COPY runtime/ ./runtime/
COPY scripts/ ./scripts/
COPY schemas/ ./schemas/
COPY governance/ ./governance/
COPY 03_ROLE_CONTRACTS/ ./03_ROLE_CONTRACTS/
COPY 05_REGISTRIES/ ./05_REGISTRIES/
COPY THOMAS_CORE/ ./THOMAS_CORE/

# Per-machine state + the durable ledger live here and are MOUNTED at runtime, never baked in.
VOLUME ["/app/.runtime_governance_state"]

# Run as a non-root user. The mounted state volume must be writable by this uid (10001).
RUN useradd --create-home --uid 10001 thomas \
    && mkdir -p /app/.runtime_governance_state \
    && chown -R thomas:thomas /app
USER thomas

# Default: the continuous long-poll operator loop. To process real requests the operator must
# mount provisioned state (registration + Core activation) and, for the real Telegram transport,
# set MVP_OPERATOR_CHANNEL=telegram + TELEGRAM_BOT_TOKEN with a mounted network_access activation.
# Emergency stop from the host: `docker exec <container> python -m runtime.mvp_runtime.console_cli kill`.
CMD ["python", "-m", "runtime.mvp_runtime.operator_cli", "--max-batches", "0", "--long-poll-seconds", "25"]
