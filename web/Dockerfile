FROM docker.io/library/node:22-slim AS build
RUN npm install --global pnpm@12.15.0
WORKDIR /web
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
# Scripts are skipped here: the postinstall runs `nuxt prepare`, which needs the sources.
RUN pnpm install --frozen-lockfile --ignore-scripts
COPY . .
RUN pnpm build

FROM docker.io/library/node:22-slim AS app
WORKDIR /web
COPY --from=build /web/.output ./.output
ENV HOST=0.0.0.0 PORT=3000 NODE_ENV=production
# The API base is read at runtime: NUXT_PUBLIC_API_BASE (the browser calls the API directly).
ENV NUXT_PUBLIC_API_BASE=http://127.0.0.1:8000
EXPOSE 3000
USER node
CMD ["node", ".output/server/index.mjs"]
