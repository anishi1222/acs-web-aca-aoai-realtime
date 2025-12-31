#!/bin/bash
export TAG="nonroot"

echo "Building server container..."
cd server
docker build -t anishi/acs-aoai-realtime-server:$TAG .
cd ..

echo "Building web container..."
cd web
docker build -t anishi/acs-aoai-realtime-web:$TAG .
cd ..

docker push anishi/acs-aoai-realtime-server:$TAG
docker push anishi/acs-aoai-realtime-web:$TAG