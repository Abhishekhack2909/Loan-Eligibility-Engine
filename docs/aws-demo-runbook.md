# AWS demonstration runbook

This is the evidence path for the completed sandbox deployment and the one
controlled SES test. It is designed for a screen recording or live review.

## Current environment

```text
AWS account: 726990466215
AWS region: ap-south-1
CloudFormation stack: loan-eligibility-engine-sandbox
API routes: enabled with the demo API key
AWS n8n host: EC2-backed Docker Compose instance
SES mode: sandbox
SES sender: complexhat735@gmail.com
SES test recipient: complexhat735+one@gmail.com
```

Do not use an address from `users.csv` as a delivery recipient. The approved
test aliases are `complexhat735+one@gmail.com` and
`complexhat735+two@gmail.com`.

## 1. Verify the CLI identity

```powershell
aws sts get-caller-identity --profile loan-engine
```

Show that the account is `726990466215`. Do not show credentials, access keys,
or secret values.

## 2. Show the CloudFormation stack

```powershell
aws cloudformation describe-stacks `
  --profile loan-engine `
  --region ap-south-1 `
  --stack-name loan-eligibility-engine-sandbox `
  --query "Stacks[0].{Status:StackStatus,Updated:LastUpdatedTime}" `
  --output table
```

Expected status:

```text
UPDATE_COMPLETE
```

Console:

```text
https://ap-south-1.console.aws.amazon.com/cloudformation/home?region=ap-south-1#/stacks
```

Open the stack and show the **Resources** and **Outputs** tabs. Important
resources are the upload bucket, EventBridge rule, ingestion queue, DLQ, API
Lambda, and ingestion Lambda.

## 3. Show the Lambda functions

```powershell
aws lambda list-functions `
  --profile loan-engine `
  --region ap-south-1 `
  --query "Functions[?starts_with(FunctionName, 'loan-eligibility-engine-sandbox')].{Name:FunctionName,Runtime:Runtime,Architecture:Architectures[0]}" `
  --output table
```

Expected functions:

```text
loan-eligibility-engine-sandbox-api
loan-eligibility-engine-sandbox-ingestion
```

Show the ingestion Lambda configuration in the Lambda console. Confirm:

- Runtime: Python 3.12
- Architecture: arm64
- VPC subnets/security group configured
- API Gateway trigger is present because the approved demo enables API routes
  with the Secrets Manager-backed API key
- The ingestion function has an SQS trigger

## 4. Show the S3 to EventBridge wiring

```powershell
aws s3api get-bucket-notification-configuration `
  --profile loan-engine `
  --region ap-south-1 `
  --bucket loan-engine-sandbox-726990466215-uploads

aws events describe-rule `
  --profile loan-engine `
  --region ap-south-1 `
  --name loan-eligibility-engine-sandbox-S3ObjectCreatedRule-O2FR3DhccDev `
  --query "{State:State,Pattern:EventPattern,Arn:Arn}" `
  --output json

aws events list-targets-by-rule `
  --profile loan-engine `
  --region ap-south-1 `
  --name loan-eligibility-engine-sandbox-S3ObjectCreatedRule-O2FR3DhccDev `
  --output table
```

Expected evidence:

- S3 has `EventBridgeConfiguration` enabled.
- The EventBridge rule is `ENABLED`.
- The rule target is the ingestion SQS queue.

Console pages:

```text
https://ap-south-1.console.aws.amazon.com/s3/buckets/loan-engine-sandbox-726990466215-uploads
https://ap-south-1.console.aws.amazon.com/events/home?region=ap-south-1#/rules
```

## 5. Show SQS and the DLQ

Use the queue URLs shown in the CloudFormation stack outputs. The successful
test consumed its message, so an empty main queue is expected.

```powershell
aws sqs get-queue-attributes `
  --profile loan-engine `
  --region ap-south-1 `
  --queue-url <INGESTION_QUEUE_URL> `
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible `
  --output table

aws sqs get-queue-attributes `
  --profile loan-engine `
  --region ap-south-1 `
  --queue-url <DLQ_URL> `
  --attribute-names ApproximateNumberOfMessages `
  --output table
```

Expected result after the successful run:

```text
Main queue visible messages: 0
Main queue in-flight messages: 0
DLQ messages: 0
```

## 6. Show the ingestion Lambda log

In PowerShell, use the CloudWatch console or run:

```powershell
aws logs tail "/aws/lambda/loan-eligibility-engine-sandbox-ingestion" `
  --profile loan-engine `
  --region ap-south-1 `
  --since 1h
```

Show the successful invocation and the `REPORT` line. Do not show secret
values or database connection strings.

## 7. Show the completed upload result

The verified AWS smoke upload was:

```text
upload_id: e20d8cf5-2580-4a1c-aade-f59ba57a079e
status: completed
row_count: 1
error_count: 0
```

The upload used the sanitized fixture:

```text
tests/fixtures/smoke-users.csv
```

This demonstrates authenticated API Gateway upload metadata, a presigned S3
upload, EventBridge routing, SQS consumption, Lambda execution, and PostgreSQL
persistence. The browser UI can target the AWS API with the `UI_URL` value
written to the ignored `aws-build/aws-demo-ui.env` file. Do not expose that
file or its API key.

## 8. Show SES identity verification

```powershell
aws sesv2 list-email-identities `
  --profile loan-engine `
  --region ap-south-1 `
  --query "EmailIdentities[].IdentityName" `
  --output table

aws sesv2 get-email-identity `
  --profile loan-engine `
  --region ap-south-1 `
  --email-identity complexhat735@gmail.com `
  --query "VerifiedForSendingStatus" `
  --output text
```

Repeat the last command for both controlled aliases. All three must print
`True`.

Console:

```text
https://ap-south-1.console.aws.amazon.com/ses/home?region=ap-south-1#/verified-identities
```

## 9. Show the SES sandbox account status

```powershell
aws sesv2 get-account `
  --profile loan-engine `
  --region ap-south-1 `
  --query "{ProductionAccessEnabled:ProductionAccessEnabled,MaxSendRate:SendQuota.MaxSendRate,SentLast24Hours:SendQuota.SentLast24Hours}" `
  --output table
```

Production access is intentionally still `false`. The test is valid because
both sender and recipient identities are verified in the SES sandbox.

## 10. Show the least-privilege n8n SES credential

```powershell
aws iam get-user `
  --profile loan-engine `
  --user-name loan-engine-n8n-ses `
  --query "User.Arn" `
  --output text

aws iam get-user-policy `
  --profile loan-engine `
  --user-name loan-engine-n8n-ses `
  --policy-name loan-engine-ses-send `
  --query "PolicyDocument.Statement[].Action" `
  --output json
```

The only actions should be `ses:SendEmail` and `ses:SendRawEmail`. Never show
the access key or secret key. n8n stores the credential encrypted in its local
credential store.

## 11. Run the AWS end-to-end demo

The browser UI remains local, but it can target the deployed AWS API. The
deployment script writes the endpoint and short-lived demo API key to the
ignored local file `aws-build/aws-demo-ui.env`. Use the `UI_URL` value from
that file, or open the local UI with the equivalent query parameters:

```text
http://localhost:8080/?api=<API_ENDPOINT>&token=<DEMO_API_KEY>
```

Use only the sanitized one-row fixture:

```text
tests/fixtures/smoke-users.csv
```

The current UI target performs:

```text
browser UI
  -> authenticated API Gateway upload metadata request
  -> presigned AWS S3 PUT
  -> S3 EventBridge
  -> SQS
  -> ingestion Lambda
  -> private RDS
  -> signed private callback to AWS n8n
  -> Workflow B
  -> Workflow C
  -> SES controlled alias
```

The verified AWS end-to-end upload was:

```text
upload_id: e20d8cf5-2580-4a1c-aade-f59ba57a079e
status: completed
row_count: 1
error_count: 0
```

The corresponding RDS delivery record was:

```text
recipient: complexhat735+one@gmail.com
status: sent
```

## 12. Show the n8n execution

Open:

```text
http://<AWS_N8N_PUBLIC_IP>:5678
```

Open **Executions** and select the successful `Loan Eligibility - C Grouped
Email Delivery` execution created by the SES test. Show these nodes:

1. `Demo Notification Webhook`
2. `Verify Match Event`
3. `Group Matches and Apply DEMO_RECIPIENTS Allowlist`
4. `AWS SES Credential Gate`
5. `AWS SES Delivery (credential-gated)`
6. `Mark SES Sent`
7. `Record SES Delivery`
8. `Return Delivery Summary`

The returned webhook summary was:

```json
{"status":"completed","deliveries_recorded":1,"no_matches":false}
```

The one-message test used:

```text
From: complexhat735@gmail.com
To: complexhat735+one@gmail.com
Subject: Your loan eligibility results
```

The SES response included a provider message ID in the execution data.

## 13. Show the recipient safety boundary

The one-message payload is checked in at:

```text
tests/fixtures/demo-notification-one-user-ses.json
```

It contains only the controlled alias recipient. The workflow retains the
source CSV email as user data and does not use it as the delivery address.

The existing two-user local Mailpit demo remains separate and should not be
replayed during the SES presentation.

## 14. Final security proof

```powershell
aws rds describe-db-instances `
  --profile loan-engine `
  --region ap-south-1 `
  --db-instance-identifier loan-engine-sandbox-db `
  --query "DBInstances[0].{Status:DBInstanceStatus,Public:PubliclyAccessible,Class:DBInstanceClass}" `
  --output table
```

Expected:

```text
Status: available
Public: false
Class: db.t4g.micro
```

The API route is protected by the Secrets Manager-backed demo API key. Do not
share the key or the generated presigned URL. The AWS n8n host uses a private
VPC address for the Lambda callback and is publicly reachable only on the demo
n8n port. Restrict that security-group rule after recording the video.

SES remains sandboxed and the IAM policy allows only the verified sender and
the two controlled alias recipients. Never send to a source CSV address.
