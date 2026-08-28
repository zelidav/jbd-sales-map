#!/usr/bin/env bash
# Deploy the JBD sales bot to Cloud Run.
# Uses the bot's OWN Anthropic key secret in the printful-manager project. Do NOT point this
# back at the shared MM_ANTHROPIC_API_KEY: that key is dead, and the sales bot was split onto
# its own info@-owned key on 2026-08-11. A deploy that resets the secret takes the bot down.
set -euo pipefail

PROJECT="${PROJECT:-printful-manager}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-jbd-sales-bot}"
BOT_MODEL="${BOT_MODEL:-claude-sonnet-4-6}"
ALLOWED_ORIGIN="${ALLOWED_ORIGIN:-*}"
KEY_SECRET="${KEY_SECRET:-SALESBOT_ANTHROPIC_API_KEY}"

cd "$(dirname "$0")"

# Keep the bot's datasets in sync with the deployed map before every deploy.
python ../tools/sync_accounts.py
# Refresh product-mix aggregates if the raw order export is present locally.
if [ -f ../data/dragonfly_orders.csv ]; then python ../tools/build_orders.py; fi

# Make sure the runtime SA can read the bot's Anthropic key.
PROJ_NUM=$(gcloud projects describe "$PROJECT" --format="value(projectNumber)")
RUNTIME_SA="${PROJ_NUM}-compute@developer.gserviceaccount.com"
gcloud secrets add-iam-policy-binding "$KEY_SECRET" \
  --project "$PROJECT" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/secretmanager.secretAccessor" \
  --condition=None >/dev/null 2>&1 || true

gcloud run deploy "$SERVICE" \
  --project "$PROJECT" \
  --region "$REGION" \
  --source . \
  --allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --timeout 300 \
  --concurrency 40 \
  --max-instances 3 \
  --set-env-vars "BOT_MODEL=${BOT_MODEL},ALLOWED_ORIGIN=${ALLOWED_ORIGIN}" \
  --set-secrets "ANTHROPIC_API_KEY=${KEY_SECRET}:latest"

URL=$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format='value(status.url)')
echo
echo "========================================"
echo "Service URL: $URL"
echo "========================================"
