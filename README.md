# Loan Eligibility Engine

The Loan Eligibility Engine is an end-to-end implementation of the Engineering
Intern assignment. It accepts a customer CSV, persists validated rows in
PostgreSQL, discovers personal-loan products, matches users to products, and
sends a controlled notification through n8n and Amazon SES.

The reproducible default path is **local-only for build and test**. An approved
assignment sandbox was also deployed in `ap-south-1`; its live evidence and
screen-recording steps are in [docs/aws-demo-runbook.md](docs/aws-demo-runbook.md).
Do not repeat or expand the AWS deployment without reviewing cost, data, and
recipient scope.

## What is included

- `serverless.yml` — AWS S3, EventBridge, SQS, Lambda, IAM, VPC, API Gateway,
  and CloudWatch resources;
- `docker-compose.yml` — local PostgreSQL, MinIO, LocalStack, n8n, Mailpit,
  backend, worker, web UI, and offline LLM stub;
- `aws/` and `scripts/infra/` — the AWS n8n host and sandbox deployment helper;
- `n8n/01-public-loan-catalog.json`, `n8n/02-ingestion-match.json`, and
  `n8n/03-email-delivery.json` — the three required n8n workflows;
- `backend/`, `migrations/`, `tests/`, and `local/` — application logic, schema,
  tests, fixtures, and reproducible local event wiring;
- `docs/architecture.html`, `docs/aws-demo-runbook.md`,
  `docs/local-runbook.md`, and `docs/workflows.md` — diagrams, setup, evidence,
  and workflow contracts.

## Architecture

Open [the interactive architecture diagram](docs/architecture.html). Its
topology shows the AWS target path and the local equivalents side by side:

```text
browser UI -> API Gateway -> presigned S3 upload -> EventBridge -> SQS
            -> ingestion Lambda -> private RDS PostgreSQL
            -> signed private n8n callback -> Workflow B -> Workflow C -> SES

local equivalent:
browser UI -> FastAPI -> MinIO -> event-router -> LocalStack SQS
            -> ingestion worker -> PostgreSQL -> n8n Workflows A/B/C -> Mailpit
```

For the completed AWS sandbox walkthrough, use
[docs/aws-demo-runbook.md](docs/aws-demo-runbook.md).

The AWS portion is parameterized for an existing private PostgreSQL/RDS
deployment. S3 is private and encrypted, EventBridge sends object-created
events to SQS, SQS has a DLQ, Lambda processing is VPC-attached, Secrets Manager
values are referenced by ARN, and CloudWatch log retention is 14 days. API
Gateway routes remain optional and can be disabled through the
`EnableApiRoutes` CloudFormation parameter; the approved demonstration
sandbox enables them with an API key.

When the approved AWS environment supplies the n8n callback URL and its
Secrets Manager-backed webhook secret, the ingestion Lambda sends the same
signed completion event as the local worker. A callback failure is retryable;
database idempotency makes the retried CSV safe. The demonstration sandbox
uses a private VPC callback to the EC2-hosted n8n instance.

## Assignment requirement map

| Assignment requirement | Implementation and evidence |
| --- | --- |
| Scalable CSV upload | Browser requests upload metadata, then uploads directly to S3 with a presigned URL. |
| Event-driven ingestion | S3 object event -> EventBridge -> SQS -> ingestion Lambda. |
| RDS persistence | Lambda streams and validates rows into `users` in PostgreSQL/RDS. |
| Self-hosted n8n | Docker Compose locally and an EC2-hosted Docker Compose instance in the AWS demo. |
| Workflow A | Scheduled public-source discovery with Discover, NerdWallet, Prosper, LLM extraction, validation, and `loan_products` upsert. |
| Workflow B | Signed webhook, SQL coarse filter, n8n deterministic matching, idempotent `matches` persistence, and metrics. |
| Optimization challenge | SQL removes impossible pairs before n8n evaluation; the LLM is not called once per user/product pair. |
| Workflow C | Grouped personalized notification, controlled-recipient allowlist, Mailpit local branch, and AWS SES branch. |
| Minimal UI | `web/` upload page with asynchronous status polling. |
| Documentation | Architecture diagram, local runbook, AWS runbook, workflow contract, and this README. |

## Runtime and dependency pins

- Python: `3.12` via `backend/.python-version`.
- Node tooling: `22.14.0` via `.nvmrc`.
- Python dependencies: exact pins in `backend/pyproject.toml`, with the
  repository lockfile at `backend/uv.lock`.
- The local workflow uses placeholder values from `.env.example`; copy it to
  `.env` and never commit `.env`.

## Sample CSV findings

The reviewed sample contains **10,000 data rows**, **7 columns**, repeated email
values, and exactly these fields:

| Field | Meaning and validation |
| --- | --- |
| `user_id` | Required non-empty text identifier. |
| `name` | Required non-empty display name. |
| `email` | Required email; normalized to lowercase for validation and recipient handling. Repeated values are retained. |
| `monthly_income` | Non-negative decimal amount. |
| `credit_score` | Integer from 0 through 850. |
| `employment_status` | Required non-empty text used by supported criteria. |
| `age` | Integer from 0 through 150. |

There are no extra columns in this finding. The parser does permit extra
columns and preserves them as `raw_data`, but product matching uses only the
known fields above. A repeated email does not mean duplicate users: the stable
ingestion identity is `(upload_batch_id, source_row_number)` and the database
keeps those rows separately.

## Local-only setup

### Prerequisites

Install or make available locally, without global project changes:

- Python 3.12 and `uv`;
- Node 22.14.0 if Serverless syntax inspection is needed (do not deploy);
- PostgreSQL 15+ (the Compose database is exposed to host tools on port `15432`
  to avoid colliding with an existing local PostgreSQL on `5432`);
- Docker Desktop for the checked-in PostgreSQL, MinIO, event-router, LocalStack,
  ingestion worker, Mailpit, n8n, LLM stub, backend, and web services.

The local stack files are outside this ownership slice and must be treated as
collaborator-owned when changing them.

PowerShell:

```powershell
Copy-Item .env.example .env
uv sync --project backend --group test
```

Start the checked-in local stack without AWS:

```bash
docker compose up --build
```

Bash:

```bash
cp .env.example .env
uv sync --project backend --group test
```

Apply the schema to the local database:

```bash
psql "$DATABASE_DSN" -f migrations/001_initial.sql
```

See [docs/local-runbook.md](docs/local-runbook.md) for the full local wiring,
demo sequence, and troubleshooting.

## Run commands

Start the FastAPI app from the repository root:

```bash
uv run --project backend uvicorn backend.app.main:app --reload
```

Check health without external services:

```bash
curl -sS http://localhost:8000/health
```

Expected response:

```json
{"status":"ok"}
```

The upload endpoints require local PostgreSQL and MinIO. The local event path
is MinIO webhook -> event-router -> LocalStack SQS
`loan-eligibility-events` -> ingestion worker. The local router sends a
normalized `{"source":"minio","records":[{"bucket":"loan-uploads","key":"uploads/{upload_id}/{filename}"}]}`
body. The AWS EventBridge detail shape is a separate contract used by the AWS
Lambda adapter in `scripts/infra/`.

## Test command

Use this one offline backend test command:

```bash
uv run --project backend pytest tests/backend
```

The suite uses injected boundaries and does not call AWS, PostgreSQL, MinIO,
n8n, Mailpit, SES, public web pages, or an LLM service. Do not replace the
offline LLM stub with a network model in this check.

## n8n setup and the three workflows

Compose provisions the three exports and one PostgreSQL credential before the
first n8n start through `local/n8n-bootstrap.js`. The persistent marker
`/home/node/.n8n/.loan-engine-provisioned-v1` makes the operation idempotent on
restart. The credential is named `Postgres local (configure in n8n)` and uses
`postgres:5432`, database `loan_engine`, user `loan_engine`, the
`POSTGRES_PASSWORD` value, and SSL off. Workflow C calls Mailpit's local HTTP
API; it does not need an SMTP credential. The optional local model API uses
`LLM_API_BASE_URL` and `LLM_MODEL`; the default `LLM_MODE=stub` path uses only
`http://llm-stub:8090`. The incoming completion webhook uses the shared secret
and HMAC headers, not a committed credential, and rejects non-completed
ingestion events.

For the AWS n8n host, the bootstrap script reads the RDS DSN, webhook secret,
and SES credential from Secrets Manager. It creates the encrypted n8n
PostgreSQL credential with SSL enabled and the `AWS SES (configure in n8n)`
credential with region `ap-south-1`. The AWS host enables SES only for the
controlled aliases and keeps the source CSV email addresses unchanged in RDS.
Credential values are never exported to GitHub.

### Workflow A — public product discovery

Workflow A fetches three personal-loan pages when `LOAN_SOURCE_MODE=public`,
captures retrieval metadata and a content hash, and parses criteria. The compose
default is `fixture` for offline repeatability; its three fixture nodes connect
directly to extraction and do not wait for the public branch. Every product
retains a public provenance URL plus `source_mode=fixture` or `public`. Offline
tests use the LLM stub; `LLM_MODE=openai-compatible` uses
`LLM_API_BASE_URL/chat/completions` with `LLM_MODEL`. The LLM receives public
product text only and cannot invent an unrecognized matcher criterion silently.

### Workflow B — optimized eligibility and ranking

After the signed ingestion callback, load the accepted users and active
products. The `SQL Coarse Filter` applies the cheap structured rules for income,
credit score, age, and employment status. Only the returned candidate pairs
reach the n8n Code node. The Code node performs the second-stage deterministic
check, preserves unknown criteria as `may_qualify`, and stores every evaluated
candidate. Ranking is a presentation operation over the persisted result:
`TOP_K` changes which rows are shown, not which candidates are evaluated or
stored.

The metrics make the optimization visible:

```text
naive      = accepted users × active products
candidates = rows returned by SQL Coarse Filter
evaluated  = candidates
stored     = evaluated
```

Therefore `candidates <= naive`, and an LLM is never called once for every
possible user/product pair. Workflow A may use the LLM for product-criteria
extraction, but Workflow B uses the cheaper structured filter first.

### Workflow C — notification

Normalize recipients, group by user ID plus recipient, include eligible,
may-qualify, and unknown matches, create delivery records, and send locally
through the Mailpit HTTP API or through the controlled AWS SES branch. A
no-match run returns one summary without waiting on a provider branch. The AWS
demo branch fails closed unless the recipient is an explicitly configured
controlled alias. Mailpit demonstrates only a local delivery handoff; it is
not proof of SES configuration, identity verification, sandbox exit,
deliverability, or production quotas. Completion is returned to Workflow B as
one summary; the ingestion callback into B is signed.

The full node-level contract, credential table, and error handling are in
[docs/workflows.md](docs/workflows.md).

## Optimization semantics and actual metrics

The optimization boundary is deliberately explicit:

1. SQL applies cheap structured criteria and removes impossible pairs;
2. n8n evaluates and stores every remaining candidate;
3. optional LLM work is limited to product-criteria extraction or ambiguity,
   never one call per possible user/product pair;
4. top-K is applied only after candidate persistence, for ranking or
   notification display.

Workflow B enforces this invariant:

```text
candidates <= naive
evaluated = candidates
stored = evaluated
```

For ingestion, the invariant is:

```text
total_rows = inserted_rows + duplicate_rows + invalid_rows + failed_rows
accepted_rows = inserted_rows + duplicate_rows
```

Replaying an upload may increase `duplicate_rows`, but it cannot create a second
row for the same `(upload_batch_id, source_row_number)`. A top-K setting must
never be used to report a smaller actual match count.

## Demo script

1. Copy `.env.example` to `.env` and run `docker compose up --build` for the
   checked-in local services.
2. Wait for n8n health; its bootstrap imports A/B/C and creates the Postgres
   credential automatically. No UI import or credential secret is required.
3. Start the backend with the run command above, or use the Compose backend.
4. `POST /uploads` with `{"filename":"sample.csv"}` and save `upload_id` and
   `upload_url`.
5. `PUT` a CSV with the exact seven-column header to the presigned URL.
6. Follow the message through MinIO webhook, event-router, LocalStack SQS, and
   the ingestion worker.
7. Confirm upload status, then let Workflow A use a public page or its marked
   fixture fallback.
8. Run Workflow B and inspect the `naive`, `candidates`, `evaluated`, and
   `stored` metrics. Confirm the SQL stage reduces candidates before matching.
9. Run Workflow C, inspect Mailpit for local mode, or inspect the AWS n8n
   execution and controlled SES delivery for the AWS demo.

### Verified local smoke run

The checked local smoke run used `tests/fixtures/smoke-users.csv` and verified:

```text
presigned upload -> MinIO -> event-router -> LocalStack SQS
-> ingestion worker -> PostgreSQL -> n8n B -> n8n C -> Mailpit
```

Observed result: one user row was persisted, three public-source products were
present in `loan_products`, and one `sent` Mailpit delivery was recorded for the
local fixture recipient `demo@example.test`. The current Workflow B export also
reports the SQL candidate reduction; rerun the smoke flow after importing that
export if you need refreshed `naive/candidates/evaluated/stored` evidence.
Workflow A was also executed against the real Discover, NerdWallet, and Prosper
pages; any failed fetch is explicitly marked and uses a fixture fallback. The
local LLM stub was used for deterministic offline tests. This does **not** prove
AWS SES delivery.

### Two-user notification-only demo

For a safe notification demonstration using the existing 10,000-row batch, use
the checked fixture `tests/fixtures/demo-notification-two-users.json`. It targets
only two users whose matches are already stored and maps delivery to:

```text
complexhat735+one@gmail.com
complexhat735+two@gmail.com
```

The users' original `users.email` values remain unchanged in PostgreSQL. The
demo webhook accepts only one or two explicitly named targets, requires the
local demo token, checks both aliases against `DEMO_RECIPIENTS`, and refuses to
use the source CSV address as a recipient. It does not re-ingest the CSV or
re-run matching. `AWS_SES_ENABLED=false`; delivery is Mailpit only.

Run it locally with:

```powershell
curl.exe -X POST http://localhost:5678/webhook/loan-demo-notification `
  -H "Content-Type: application/json" `
  --data-binary "@tests/fixtures/demo-notification-two-users.json"
```

Then verify two new messages at [Mailpit](http://localhost:8025), and verify
the two `eligible-loans-demo-v1` rows in `email_deliveries`. Do not edit or
delete the existing blocked-delivery rows.

For copy/paste commands and the intentionally invalid-row demonstration, see
[docs/local-runbook.md](docs/local-runbook.md).

## AWS sandbox deployment

The approved sandbox was deployed in `ap-south-1` as
`loan-eligibility-engine-sandbox`. The exact evidence path is in
[docs/aws-demo-runbook.md](docs/aws-demo-runbook.md). The following commands
must not be repeated casually because they can create or update billable
resources:

```bash
# Repeat only after explicit approval and a fresh change-set review:
npx serverless deploy --stage sandbox --region ap-south-1

# Repeat only after explicit approval and a fresh change-set review:
aws cloudformation deploy --template-file .serverless/cloudformation-template-update-stack.json --stack-name loan-eligibility-engine
```

The sandbox now enables the authenticated upload API, uses a private RDS
instance, keeps SES in sandbox mode, and runs the AWS n8n Docker Compose host
inside the same VPC. The AWS ingestion Lambda calls the private n8n webhook;
the AWS n8n workflows use the same RDS and the controlled SES aliases.

The repeatable demo deployment helper is:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\infra\deploy_aws_demo.ps1 `
  -Profile loan-engine -Region ap-south-1 -StackName loan-eligibility-engine-sandbox
```

It provisions the demo n8n EC2 host, private database/security-group access,
Secrets Manager references, the authenticated API route, and the AWS n8n
workflow bundle. It writes the API endpoint, controlled demo key, and n8n URL
to the ignored local file `aws-build/aws-demo-ui.env`; never commit or share
that file. If a workflow JSON changes after the host was created, sync the
workflow to the AWS n8n instance and verify a fresh execution before recording;
the existing n8n data volume is intentionally persistent and is not replaced
automatically on a normal restart.
Before any expansion, review the existing-RDS network path, dependency layer
ARN, secret ARNs, API authentication/WAF plan, log/retention cost, S3 lifecycle,
and data retention.

### AWS n8n credential setup

The AWS demonstration runs n8n in Docker Compose on an EC2 host inside the
application VPC. The bootstrap process loads these values from Secrets Manager;
do not paste them into workflow exports:

| n8n credential | AWS configuration |
| --- | --- |
| PostgreSQL | Private RDS endpoint, database `loan_engine`, port `5432`, SSL enabled, certificate verification relaxed only for the sandbox demonstration. |
| AWS SES | IAM user `loan-engine-n8n-ses`, region `ap-south-1`, policy limited to `ses:SendEmail` and `ses:SendRawEmail`. |
| Completion webhook | Shared HMAC secret used by Lambda and Workflow B. |

SES is sandboxed. The sender and controlled recipient aliases are deployment
configuration loaded from Secrets Manager or the local `.env`; they are not
required in the workflow exports. The source CSV addresses remain unchanged in
the `users` table and are never used as AWS demo recipients.

## Demonstration evidence

The assignment video can use the following evidence path:

1. Open `docs/architecture.html` and explain the event-driven architecture.
2. Upload `tests/fixtures/smoke-users.csv` through the minimal web UI.
3. Show the AWS stack or S3/EventBridge/SQS resources.
4. Show Workflow A's schedule, three sources, fallback, extraction, validation,
   and `loan_products` upsert.
5. Show Workflow B's signed webhook, `SQL Coarse Filter`, matching code, and
   `naive/candidates/evaluated/stored` metrics.
6. Show Workflow C's controlled recipient mapping, SES gate, send, and delivery
   record.
7. Show the received message in the controlled test inbox.

The exact console pages and verified upload evidence are in
[docs/aws-demo-runbook.md](docs/aws-demo-runbook.md). Never expose API tokens,
presigned URLs, access keys, database passwords, or Secrets Manager values in a
recording.

The repository contains the assignment's source-code deliverables. The
demonstration video, collaborator invitations, and submission form are external
submission steps rather than application configuration.

After submission, stop or delete billable AWS resources and remove temporary
demo ingress and credentials that are no longer needed.

## Troubleshooting and limitations

Common failures and the local remedies are documented in
[docs/local-runbook.md](docs/local-runbook.md). The most important boundaries
are:

- public page content is mutable; fixture fallback is necessary for repeatable
  local demos and must be labeled;
- the LLM stub and an actual local model API are optional extraction aids, not
  deterministic eligibility authorities;
- SES is configured and has a controlled AWS test; Mailpit still cannot prove
  SES behavior;
- the AWS sandbox has been deployed and the one-row end-to-end path has been
  verified. Review its dependency layer, private network, existing RDS, secret
  ARNs, demo API key, and EC2 n8n security-group rule before repeating it;
- local service definitions are outside this owned slice, so their health,
  credentials, and network aliases must be checked with their owners.
