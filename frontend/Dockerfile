FROM node:20-alpine

WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .

EXPOSE 3000

# Dev server, not a production build -- matches this project's existing
# philosophy (the Streamlit container is also just "run the app directly").
# docker-compose.yml bind-mounts ./frontend:/app over this for live reload
# during development, with an anonymous volume over /app/node_modules so the
# host's (nonexistent) node_modules doesn't shadow what npm install baked in.
CMD ["npm", "run", "dev"]
