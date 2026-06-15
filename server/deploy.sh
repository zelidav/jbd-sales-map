#!/usr/bin/env bash
# Deploy the JBD sales bot to Cloud Run.
# Reuses the existing Anthropic key secret (MM_ANTHROPIC_API_KEY) in the printful-manager project.
set -euo pipefail

PROJECT="${PROJECT:-printful-manager}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-jbd-sales-bot}"
BOT_MODEL="${BOT_MODEL:-claude-sonnet-4-6}"
ALLOWED_ORIGIN="${ALLOWED_ORIGIN:-*}"

cd "$(dirname "$0")"

# Keep the bot's dataset in sync with the deployed map before every deploy.
python ../tools/sync_accounts.py

# Make sure the runtime SA can read the shared Anthropic key.
PROJ_NUM=$(gcloud projects describe "$PROJECT" --format="value(projectNumber)")
RUNTIME_SA="${PROJ_NUM}-compute@developer.gserviceaccount.com"
gcloud secrets add-iam-policy-binding MM_ANTHROPIC_API_KEY \
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
  --timeout 120 \
  --concurrency 40 \
  --max-instances 3 \
  --set-env-vars "BOT_MODEL=${BOT_MODEL},ALLOWED_ORIGIN=${ALLOWED_ORIGIN}" \
  --set-secrets "ANTHROPIC_API_KEY=MM_ANTHROPIC_API_KEY:latest"

URL=$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format='value(status.url)')
echo
echo "========================================"
echo "Service URL: $URL"
echo "========================================"
