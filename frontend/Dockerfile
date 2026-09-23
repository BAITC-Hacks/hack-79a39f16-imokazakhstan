FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.28-alpine AS runtime
RUN apk add --no-cache jq ca-certificates
COPY nginx.conf /etc/nginx/nginx.conf
COPY docker-entrypoint/start.sh /usr/local/bin/dashboard-start
RUN sed -i 's/\r$//' /usr/local/bin/dashboard-start \
    && chmod 755 /usr/local/bin/dashboard-start
COPY --from=build /app/dist /usr/share/nginx/html
USER nginx
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
ENTRYPOINT ["/usr/local/bin/dashboard-start"]
CMD ["nginx", "-g", "daemon off;"]
