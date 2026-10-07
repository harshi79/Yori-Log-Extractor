# Yori Log Extractor — the bot, in a container.
#
#   docker build -t yori-log-extractor .
#   docker run --rm --env-file .env --name yori-bot yori-log-extractor
#
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first: this layer is cached until requirements.txt changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The bot itself.
COPY bot/ ./bot/
COPY run.py pyproject.toml README.md ./

# Never run as root, and keep the secrets in the environment, not the image.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin bot
USER bot

# Long polling needs no inbound port, so no EXPOSE here.
CMD ["python", "run.py", "--quiet"]
