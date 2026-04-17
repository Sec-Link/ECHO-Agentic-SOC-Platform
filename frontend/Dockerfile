# ---------- 1. 依赖安装阶段 ----------
FROM node:24-alpine AS deps
RUN apk add --no-cache libc6-compat
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ---------- 2. 构建阶段 ----------
FROM node:24-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# 确保 next.config.js 中配置了 output: 'standalone'
RUN npm run build

# ---------- 3. 运行阶段 ----------
FROM node:24-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED 1

# 安全加固：创建非 root 用户运行 Node.js 进程
RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

# 仅拷贝 standalone 模式必需的文件
COPY --from=builder /app/public ./public
# 自动生成的 standalone 文件夹包含了 server.js 和 node_modules 最小集
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000
ENV PORT 3000

CMD ["node", "server.js"]
