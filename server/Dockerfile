FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	PIP_NO_CACHE_DIR=1

WORKDIR /app

# Needed for outbound TLS (Azure/OpenAI over https/wss).
RUN apt-get update \
	&& apt-get install -y --no-install-recommends ca-certificates \
	&& rm -rf /var/lib/apt/lists/*

# Create an unprivileged user to run the server.
RUN groupadd -g 10001 app && \
	useradd -r -u 10001 -g app -m -d /home/app app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./
RUN chown -R app:app /app

USER app

EXPOSE 8000

CMD ["python", "app.py"]
