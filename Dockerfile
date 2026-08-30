# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.14
ARG NODE_VERSION=20

FROM node:${NODE_VERSION}-slim AS node-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend ./
RUN mkdir -p /app/songhive && npm run build

FROM python:${PYTHON_VERSION}-slim AS songhive

ARG USER_ID=1000
ARG GROUP_ID=1000

ENV PYTHONUNBUFFERED=1
ENV HOME=/home/songhive

# Create a non-root runtime group and user. The UID/GID default to 1000 so
# that, on most Linux desktops, the container user matches the host user and
# files created in bind-mounted volumes are owned by the host user.
RUN groupadd -g ${GROUP_ID} songhive && \
    useradd -u ${USER_ID} -g songhive -d /home/songhive -s /bin/bash songhive

WORKDIR /home/songhive

# Install a static ffmpeg binary. It has no external dependencies and works
# in the Debian-based python-slim image without pulling in the 200+ Debian
# packages that `apt-get install ffmpeg` would install.
COPY --from=mwader/static-ffmpeg:9.0.1 /ffmpeg /usr/local/bin/ffmpeg

# Copy package metadata, install runtime dependencies, then copy source code
# and install the package as root. The installed package is readable by all
# users; the source tree remains in /app so the editable install can resolve it.
COPY requirements.txt pyproject.toml setup.cfg README.md /app/
RUN pip install --no-cache-dir --no-compile -r /app/requirements.txt \
    "setuptools>=61.0" "wheel"
COPY songhive /app/songhive
COPY --from=node-builder /app/songhive/static /app/songhive/static
RUN pip install --no-build-isolation --no-deps --no-cache-dir --no-compile -e /app

# Copy the entrypoint script that bootstraps the container.
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Runtime directories that may be bind-mounted from the host must be writable
# by the container user.
RUN mkdir -p /data /etc/songhive /var/www/songhive /home/songhive/.local && \
    chown -R songhive:songhive /data /etc/songhive /var/www/songhive /home/songhive

VOLUME ["/data", "/etc/songhive", "/var/www/songhive"]
EXPOSE 8000
USER songhive
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["songhive"]
