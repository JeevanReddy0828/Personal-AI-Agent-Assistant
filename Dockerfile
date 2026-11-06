# A hosted J.A.R.V.I.S: sign-in only, one hostname, no shell and no files (see "Hosting online"
# in the README). Zero runtime dependencies, so there is nothing to install but the package.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LAPTOP_AGENT_HOSTED=1 \
    LAPTOP_AGENT_HOST=0.0.0.0 \
    LAPTOP_AGENT_PORT=8080 \
    LAPTOP_AGENT_DATA_DIR=/data

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && rm -rf /app/src

RUN useradd --uid 10001 --create-home jarvis && mkdir -p /data && chown jarvis:jarvis /data
EXPOSE 8080

# Starts as root only to hand a freshly mounted volume (root-owned) to the app's own user, then
# drops to it for good: the server never runs as root.
CMD ["sh", "-c", "chown jarvis:jarvis \"$LAPTOP_AGENT_DATA_DIR\" && exec setpriv --reuid=jarvis --regid=jarvis --init-groups python -m laptop_agent.webui"]
