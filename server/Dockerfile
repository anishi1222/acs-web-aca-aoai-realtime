FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	PIP_NO_CACHE_DIR=1

WORKDIR /app


# Needed for outbound TLS (Azure/OpenAI over https/wss) + installing uv.
RUN apt-get update \
	&& apt-get install -y --no-install-recommends ca-certificates curl \
	&& rm -rf /var/lib/apt/lists/*

# Install uv (avoid pip in container build).
ENV UV_INSTALL_DIR=/usr/local/bin \
	UV_UNMANAGED_INSTALL=1 \
	UV_NO_MODIFY_PATH=1
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# Create an unprivileged user to run the server.
RUN groupadd -g 10001 app && \
	useradd -r -u 10001 -g app -m -d /home/app app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

# Ensure runtime uses the uv-managed virtual environment.
ENV VIRTUAL_ENV=/app/.venv \
	PATH="/app/.venv/bin:$PATH"

COPY . ./
RUN chown -R app:app /app

USER app

EXPOSE 8000

CMD ["python", "app.py"]
