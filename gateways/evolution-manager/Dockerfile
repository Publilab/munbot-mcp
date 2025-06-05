FROM node:20-slim

WORKDIR /usr/src/app

COPY . .

# Install curl
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

RUN npm install
RUN npm install -g evolution-manager@0.4.13
RUN npm install -g fs-extra
RUN npm install -g serve

ARG VITE_API_URL
ENV VITE_API_URL=${VITE_API_URL}
RUN npm run build

EXPOSE 9615

CMD ["serve", "-s", "dist", "-l", "9615"]