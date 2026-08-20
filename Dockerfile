# A reproducible environment for the strategy core and its regression suite.
#
# What this image is for: pinning the interpreter and the dependency set so the
# suite runs the same way on a laptop, in CI and on a host that has no Python
# toolchain. The engine's live mode needs network access to an exchange and a
# configuration file, neither of which is baked in — see README, "Run it in a
# container", for what is and is not exercised here.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# lightgbm links against the OpenMP runtime, which python:*-slim does not carry.
# The CI matrix never noticed: the GitHub runner image happens to ship libgomp1,
# so `import lightgbm` works there and fails in any minimal image. That is the
# kind of defect only a container finds.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

# Dependencies first, so a source change does not reinstall them.
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY scripts ./scripts

# Nothing here needs to write to the image or to run as root. A container that
# cannot write its own code is one fewer thing to reason about when the same
# image is handed to someone else.
# An empty, writable config/. The image ships no configuration — the dashboard
# generates its own session secret on first import, and it needs somewhere to
# put it. Empty and writable is a different claim from absent, and CI checks the
# one that is true.
RUN mkdir -p /app/config \
 && useradd --create-home --uid 10001 runner \
 && chown -R runner:runner /app
USER runner

# The default command is the thing that provably works with no network, no
# credentials and no mounted data: the deterministic regression suite.
CMD ["bash", "scripts/run_suite.sh"]
