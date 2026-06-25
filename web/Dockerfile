# CottonMouth web dashboard — Next.js (standalone output).
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# NEXT_PUBLIC_API_URL is inlined into client code at build time.
#   "self"            -> same-origin, proxied to BACKEND_ORIGIN (recommended)
#   "<url>"           -> browser calls the backend directly (CORS is open)
#   unset/""          -> demo mode (hardcoded sample data, no backend)
ARG NEXT_PUBLIC_API_URL=self
ARG BACKEND_ORIGIN=http://cottonmouth-backend:8150
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    BACKEND_ORIGIN=$BACKEND_ORIGIN \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
