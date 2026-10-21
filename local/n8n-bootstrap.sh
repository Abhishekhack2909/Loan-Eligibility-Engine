#!/bin/sh
set -eu

MARKER="${N8N_PROVISION_MARKER:-/home/node/.n8n/.loan-engine-provisioned-v1}"
WORKFLOW_DIR=/data/workflows

# The marker is stored in the persistent n8n volume. A failed import does not
# create it, while a normal restart never imports duplicate workflows or
# credentials.
if [ -f "$MARKER" ]; then
  exit 0
fi

credential_file=$(mktemp)
trap 'rm -f "$credential_file"' EXIT
node -e 'process.stdout.write(JSON.stringify([{name: process.env.N8N_POSTGRES_CREDENTIAL_NAME, type: "postgres", data: {host: "postgres", port: 5432, database: "loan_engine", user: "loan_engine", password: process.env.POSTGRES_PASSWORD, ssl: "disable"}, nodesAccess: []}]))' >"$credential_file"

n8n import:credentials --input="$credential_file"
n8n import:workflow --input="$WORKFLOW_DIR/01-public-loan-catalog.json"
n8n import:workflow --input="$WORKFLOW_DIR/02-ingestion-match.json"
n8n import:workflow --input="$WORKFLOW_DIR/03-email-delivery.json"
n8n update:workflow --id=public-loan-catalog-ingestion --active=true
n8n update:workflow --id=loan-ingestion-match --active=true
n8n update:workflow --id=loan-eligibility-email-delivery --active=false

touch "$MARKER"
echo "n8n workflows A/B/C and the local Postgres credential are provisioned" >&2
