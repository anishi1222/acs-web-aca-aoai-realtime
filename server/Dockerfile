FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	PIP_NO_CACHE_DIR=1

WORKDIR /app

# Create an unprivileged user to run the server.
RUN groupadd -g 10001 app && \
	useradd -r -u 10001 -g app -m -d /home/app app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./
RUN chown -R app:app /app

USER app

EXPOSE 8000

CMD [
	"uvicorn",
	"app:app",
	"--host",
	"0.0.0.0",
	"--port",
	"8000",
	"--ws",
	"websockets",
	"--ws-max-size",
	"0",
	"--ws-per-message-deflate",
	"false",
	"--ws-ping-interval",
	"20",
	"--ws-ping-timeout",
	"20"
]
