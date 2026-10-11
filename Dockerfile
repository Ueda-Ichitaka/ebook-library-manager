# About this file: builds the container image for the ebookprep tool.
# The image bundles a pinned calibre release (command-line tools only, run headless)
# together with Python and the Python dependencies, so the pipeline runs the same way
# on every machine. The "test" target adds the test dependencies and the test suite.

FROM python:3.14.8-slim-trixie AS runtime

ARG CALIBRE_VERSION=9.16.0

# System libraries the calibre binary build needs on a server without a display.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates wget xz-utils xdg-utils \
        libegl1 libopengl0 libglx0 libxcb-cursor0 libxkbcommon0 libfontconfig1 libnss3 libasound2t64 \
    && rm -rf /var/lib/apt/lists/*

# Isolated install keeps every calibre file inside /opt/calibre.
RUN wget -nv -O- https://download.calibre-ebook.com/linux-installer.sh \
    | sh /dev/stdin install_dir=/opt isolated=y version=${CALIBRE_VERSION}

ENV PATH="/opt/calibre:${PATH}" \
    QT_QPA_PLATFORM=offscreen \
    HOME=/tmp \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY config.toml ./

FROM runtime AS test
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY pytest.ini ./
COPY tests ./tests
CMD ["pytest"]
