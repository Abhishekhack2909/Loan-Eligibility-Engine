#!/bin/sh
set -eu

QUEUE_NAME="${EVENT_QUEUE_NAME:-loan-eligibility-events}"
awslocal sqs create-queue --queue-name "$QUEUE_NAME" >/dev/null
echo "LocalStack SQS queue ready: $QUEUE_NAME"
