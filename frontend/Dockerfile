
# Build stage
FROM node:24-alpine AS build

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./

RUN npm ci

COPY frontend/ .

RUN npm run build


# Production stage
FROM nginx:1.28-alpine

LABEL org.opencontainers.image.title="My-Instant 3D Quote Frontend" \
    org.opencontainers.image.description="MyMachines 3D printing quotation frontend" \
    org.opencontainers.image.source="https://github.com/EdgarMata/mymachines-3d-quote" \
    org.opencontainers.image.licenses="AGPL-3.0-only" \
    org.opencontainers.image.vendor="MyMachines"

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY frontend/nginx-trusted-proxies.conf /etc/nginx/trusted-proxies.conf
COPY --chmod=755 frontend/15-validate-trusted-proxies.sh /docker-entrypoint.d/15-validate-trusted-proxies.sh

COPY --from=build /app/dist /usr/share/nginx/html

# Project license and third-party notices
COPY LICENSE /usr/share/nginx/html/LICENSE
COPY THIRD_PARTY_NOTICES.md /usr/share/nginx/html/THIRD_PARTY_NOTICES.md

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
