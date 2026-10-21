#!/bin/sh
set -eu

mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
mc mb --ignore-existing local/loan-uploads

# MinIO sends object notifications to the HTTP event-router. The router owns
# the second hop to LocalStack SQS, so this wiring is explicit and inspectable.
mc event remove local/loan-uploads --force >/dev/null 2>&1 || true
mc event add local/loan-uploads --event put --arn "arn:minio:sqs::primary:webhook"
echo "MinIO webhook notification configured for local/loan-uploads"
