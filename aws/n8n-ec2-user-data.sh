#!/bin/bash
set -euo pipefail

exec > >(tee -a /var/log/loan-engine-n8n-bootstrap.log | logger -t loan-engine-n8n -s 2>/dev/console) 2>&1

REGION="__REGION__"
BUCKET="__BUCKET__"
DATABASE_SECRET_ARN="__DATABASE_SECRET_ARN__"
N8N_WEBHOOK_SECRET_ARN="__N8N_WEBHOOK_SECRET_ARN__"
SES_CREDENTIAL_SECRET_ARN="__SES_CREDENTIAL_SECRET_ARN__"

dnf install -y docker jq unzip awscli-2 || dnf install -y docker jq unzip awscli
systemctl enable --now docker
if ! docker compose version >/dev/null 2>&1; then
  dnf install -y docker-compose-plugin || true
fi
if ! docker compose version >/dev/null 2>&1; then
  mkdir -p /usr/local/lib/docker/cli-plugins
  curl -fsSL https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-aarch64 -o /usr/local/lib/docker/cli-plugins/docker-compose
  chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
fi
mkdir -p /opt/loan-engine/n8n/fixtures/loans /opt/loan-engine/llm_stub
cd /opt/loan-engine

aws s3 cp "s3://${BUCKET}/aws/n8n-compose.yml" ./docker-compose.yml --region "${REGION}"
aws s3 cp "s3://${BUCKET}/aws/n8n-bootstrap.js" ./n8n-bootstrap.js --region "${REGION}"
aws s3 cp "s3://${BUCKET}/aws/n8n/" ./n8n/ --recursive --region "${REGION}"
aws s3 cp "s3://${BUCKET}/aws/llm_stub/server.py" ./llm_stub/server.py --region "${REGION}"

db_secret_json="$(aws secretsmanager get-secret-value --secret-id "${DATABASE_SECRET_ARN}" --query SecretString --output json --region "${REGION}" | jq -r '.')"
webhook_secret_json="$(aws secretsmanager get-secret-value --secret-id "${N8N_WEBHOOK_SECRET_ARN}" --query SecretString --output json --region "${REGION}" | jq -r '.')"
ses_secret_json="$(aws secretsmanager get-secret-value --secret-id "${SES_CREDENTIAL_SECRET_ARN}" --query SecretString --output json --region "${REGION}" | jq -r '.')"

database_dsn="$(printf '%s' "${db_secret_json}" | jq -r '.DATABASE_DSN // .value // .')"
webhook_secret="$(printf '%s' "${webhook_secret_json}" | jq -r '.N8N_WEBHOOK_SECRET // .value // .')"
ses_access_key="$(printf '%s' "${ses_secret_json}" | jq -r '.accessKeyId')"
ses_secret_key="$(printf '%s' "${ses_secret_json}" | jq -r '.secretAccessKey')"

python3 - "${database_dsn}" > /tmp/loan-engine-rds.env <<'PY'
import shlex
import sys
from urllib.parse import unquote, urlparse

parsed = urlparse(sys.argv[1])
if parsed.scheme not in {"postgresql", "postgres"}:
    raise SystemExit("DATABASE_DSN must be a PostgreSQL URL")
values = {
    "N8N_POSTGRES_HOST": parsed.hostname or "",
    "N8N_POSTGRES_PORT": str(parsed.port or 5432),
    "N8N_POSTGRES_DATABASE": (parsed.path or "/loan_engine").lstrip("/"),
    "N8N_POSTGRES_USER": unquote(parsed.username or "loan_engine"),
    "N8N_POSTGRES_PASSWORD": unquote(parsed.password or ""),
}
for key, value in values.items():
    print(f"{key}={shlex.quote(value)}")
PY
source /tmp/loan-engine-rds.env

cat > .env <<EOF
N8N_ENCRYPTION_KEY=$(openssl rand -hex 32)
N8N_WEBHOOK_SECRET=$(printf '%s' "${webhook_secret}")
N8N_POSTGRES_HOST=${N8N_POSTGRES_HOST}
N8N_POSTGRES_PORT=${N8N_POSTGRES_PORT}
N8N_POSTGRES_DATABASE=${N8N_POSTGRES_DATABASE}
N8N_POSTGRES_USER=${N8N_POSTGRES_USER}
N8N_POSTGRES_PASSWORD=$(printf '%s' "${N8N_POSTGRES_PASSWORD}")
N8N_POSTGRES_SSL=require
N8N_POSTGRES_ALLOW_UNAUTHORIZED_CERTS=true
N8N_POSTGRES_CREDENTIAL_NAME=Postgres local (configure in n8n)
AWS_SES_ACCESS_KEY_ID=$(printf '%s' "${ses_access_key}")
AWS_SES_SECRET_ACCESS_KEY=$(printf '%s' "${ses_secret_key}")
AWS_SES_REGION=${REGION}
AWS_SES_ENABLED=true
AWS_SES_CREDENTIAL_NAME=AWS SES (configure in n8n)
AWS_SES_FROM=complexhat735@gmail.com
MAIL_DELIVERY_MODE=ses
DEMO_RECIPIENTS=complexhat735+one@gmail.com,complexhat735+two@gmail.com
AWS_DEMO_MODE=true
AWS_DEMO_RECIPIENTS=complexhat735+one@gmail.com
DEMO_NOTIFICATION_TOKEN=aws-demo-notification-token
LOAN_SOURCE_MODE=fixture
LLM_MODE=stub
LLM_STUB_URL=http://llm-stub:8090
EOF
chmod 600 .env

docker compose up -d
