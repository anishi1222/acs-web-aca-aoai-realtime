FROM node:20-alpine AS build

WORKDIR /app

# Optional: bake API base URL into the production build.
# Example: docker build --build-arg VITE_API_BASE=http://localhost:8000 .
ARG VITE_API_BASE=
ENV VITE_API_BASE=${VITE_API_BASE}

COPY package.json package-lock.json ./
RUN npm ci

COPY . ./
RUN npm run build


FROM nginxinc/nginx-unprivileged:1.27-alpine

# envsubst for nginx config templating
USER root
RUN apk add --no-cache gettext
USER nginx

# Default: for Linux you may need --add-host=host.docker.internal:host-gateway
ENV API_UPSTREAM=http://host.docker.internal:8000

WORKDIR /app

COPY nginx.conf.template /app/nginx.conf.template
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
USER root
RUN chmod +x /app/docker-entrypoint.sh
USER nginx
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080

ENTRYPOINT ["/app/docker-entrypoint.sh"]
