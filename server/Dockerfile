FROM node:20-slim
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --omit=dev --no-audit --no-fund
# Every source and data file the server reads must be listed here. A missing DATA file
# boots fine and then degrades silently (brand_intel.json caused exactly that once); a
# missing SOURCE file fails the container outright (profiles.js caused exactly that).
# index.js imports profiles.js and loads accounts.json, orders_summary.json,
# brand_intel.json, brand_profiles.json.
COPY index.js profiles.js accounts.json orders_summary.json brand_intel.json brand_profiles.json ./
ENV PORT=8080
EXPOSE 8080
CMD ["node", "index.js"]
