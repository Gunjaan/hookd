# Hookd app image: Python for Django (server rendered templates, no Node toolchain).
# Code is not copied in; deploy.py mounts the repository at run time.
# Build with: python3 deploy.py build
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/home/venv/bin:${PATH}"

RUN DEBIAN_FRONTEND=noninteractive apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        nano \
        postgresql-client \
        procps \
        screen \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /home/venv
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Shortcuts available in the container shell. APP_HOME is set by deploy.py; scripts/hookd.sh does the work.
RUN { \
      echo 'alias run="$APP_HOME/scripts/hookd.sh run"'; \
      echo 'alias runbg="screen -dmS backend bash -ic run && sleep 2 && screen -ls"'; \
      echo 'alias restart-celery="$APP_HOME/scripts/hookd.sh celery"'; \
      echo 'alias stopall="$APP_HOME/scripts/hookd.sh stop"'; \
    } >> /root/.bashrc

EXPOSE 8000

CMD ["bash"]
