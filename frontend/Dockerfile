# Build stage
FROM node:24-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./

RUN npm ci

COPY . .

RUN npm run build


# Production stage
FROM nginx:1.28-alpine
LABEL org.opencontainers.image.title="My-Instant 3D Quote Frontend" \
    org.opencontainers.image.description="MyMachines 3D printing quotation frontend" \
    org.opencontainers.image.source="https://github.com/EdgarMata/mymachines-3d-quote" \
    org.opencontainers.image.licenses="AGPL-3.0-only" \
    org.opencontainers.image.vendor="MyMachines"

COPY nginx.conf /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
