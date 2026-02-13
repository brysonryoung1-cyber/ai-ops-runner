# Dockerfile for OpenClaw Console (Next.js)
# Multi-stage build for minimal production image.
# Binds to 127.0.0.1 when used directly; Docker publish controls host bind.

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --production=false

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000

# Copy only what's needed
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

# Create data directory for audit log
RUN mkdir -p /app/data && chown -R node:node /app/data

USER node
EXPOSE 3000

CMD ["node", "server.js"]
