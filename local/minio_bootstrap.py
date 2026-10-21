"""Create the local MinIO bucket and explicitly register its webhook event.

The official ``minio/mc`` image is not required for the local stack.  Using the
S3-compatible API keeps the bootstrap reproducible and makes the important
MinIO -> event-router wiring visible in code rather than relying on an implicit
Docker network connection.
"""

from __future__ import annotations

import os
import time
from typing import Any

import boto3


ENDPOINT = os.getenv("MINIO_ENDPOINT_URL", "http://minio:9000")
BUCKET = os.getenv("S3_BUCKET", "loan-uploads")
REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
ACCESS_KEY = os.getenv("MINIO_ROOT_USER", "minioadmin")
SECRET_KEY = os.getenv("MINIO_ROOT_PASSWORD", "minioadmin")
WEBHOOK_ARN = os.getenv("MINIO_WEBHOOK_ARN", "arn:minio:sqs::PRIMARY:webhook")


def client() -> Any:
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
    )


def main() -> None:
    s3 = client()
    last_error: Exception | None = None
    for _ in range(30):
        try:
            s3.head_bucket(Bucket=BUCKET)
            break
        except Exception as exc:  # MinIO may be healthy before the API is ready.
            last_error = exc
            try:
                s3.create_bucket(Bucket=BUCKET)
                break
            except Exception as create_error:
                last_error = create_error
                time.sleep(2)
    else:
        raise RuntimeError(f"MinIO bucket {BUCKET!r} was not ready: {last_error}")

    s3.put_bucket_notification_configuration(
        Bucket=BUCKET,
        NotificationConfiguration={
            "QueueConfigurations": [
                {
                    "Id": "loan-engine-event-router",
                    "QueueArn": WEBHOOK_ARN,
                    "Events": ["s3:ObjectCreated:*"]
                }
            ]
        },
    )
    print(f"MinIO bucket ready: {BUCKET}; webhook event configured: {WEBHOOK_ARN}", flush=True)


if __name__ == "__main__":
    main()
