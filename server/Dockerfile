FROM node:20-slim
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --omit=dev --no-audit --no-fund
# Every data file the server reads must be listed here — a missing one boots fine and
# then degrades silently (brand_intel.json caused exactly that once). index.js loads:
# accounts.json, orders_summary.json, brand_intel.json, brand_profiles.json.
COPY index.js accounts.json orders_summary.json brand_intel.json brand_profiles.json ./
ENV PORT=8080
EXPOSE 8080
CMD ["node", "index.js"]
