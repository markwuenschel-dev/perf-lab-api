# web/Dockerfile
# Multi-stage build: Node compiles the Vite/React bundle, nginx serves it.
# VITE_API_BASE_URL is a build-time ARG — set it in Railway's service Variables tab
# (under "Build Variables") so it gets baked into the JS bundle at image build time.

# ── Stage 1: build ────────────────────────────────────────────────────────────
FROM node:22-alpine AS build
WORKDIR /app

# Receive the backend URL at build time and expose it to Vite
ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

COPY package*.json ./
RUN npm ci --prefer-offline

COPY . .
RUN npm run build

# ── Stage 2: serve ────────────────────────────────────────────────────────────
FROM nginx:1.27-alpine

# Copy built assets
COPY --from=build /app/dist /usr/share/nginx/html

# nginx config template — uses envsubst at startup to inject $PORT
COPY nginx.conf /etc/nginx/templates/default.conf.template

# Railway injects $PORT; default 80 keeps local docker run working
ENV PORT=80
EXPOSE ${PORT}

# envsubst only replaces ${PORT}, leaving nginx $uri/$args variables untouched
CMD ["sh", "-c", "envsubst '${PORT}' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]
