FROM node:20-alpine AS build

WORKDIR /app

COPY package*.json /app/
RUN npm ci

COPY . /app

ARG VITE_API_BASE_URL=/api
ARG VITE_SERVICE_API_KEY=
ARG VITE_DEFAULT_TOP_K=5

ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_SERVICE_API_KEY=${VITE_SERVICE_API_KEY}
ENV VITE_DEFAULT_TOP_K=${VITE_DEFAULT_TOP_K}

RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
