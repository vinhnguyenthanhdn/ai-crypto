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

# Dependencies first, so a source change does not reinstall them.
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY scripts ./scripts

# Nothing here needs to write to the image or to run as root. A container that
# cannot write its own code is one fewer thing to reason about when the same
# image is handed to someone else.
RUN useradd --create-home --uid 10001 runner \
 && chown -R runner:runner /app
USER runner

# The default command is the thing that provably works with no network, no
# credentials and no mounted data: the deterministic regression suite.
CMD ["bash", "scripts/run_suite.sh"]
