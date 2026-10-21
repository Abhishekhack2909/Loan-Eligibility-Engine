# n8n workflow contract

The workflows are intentionally described as contracts here so local n8n
authors can build and review them without coupling the backend to a particular
n8n export format. The backend owns upload metadata, streaming CSV ingestion,
deterministic matching, metrics, and signed completion callbacks. n8n owns
public-page orchestration, product extraction, ranking presentation, and email
delivery.

## Workflow A — product discovery and criteria preparation

1. Start from a manual trigger or the checked-in daily schedule. Set
   `LOAN_SOURCE_MODE=public` to fetch live pages; the compose default is
   `fixture` so offline tests do not call the public internet.
2. Fetch the real public product pages with an HTTP Request node. Store the URL,
   retrieval timestamp, HTTP status, and content hash in the run log.
3. If a page is unavailable, rate-limited, or changes shape, switch to the
   checked-in fixture branch. Mark the source as `fixture` so a demo cannot be
   mistaken for a public-page result.
4. Normalize product names and criteria into the `loan_products` shape. The
   criteria keys supported by the deterministic matcher are:
   `min_monthly_income`, `max_monthly_income`, `min_credit_score`,
   `max_credit_score`, `employment_status`, `min_age`, and `max_age`.
5. Use the offline LLM stub at `LLM_STUB_URL` (`http://llm-stub:8090` inside
   compose) for repeatable tests. For an actual local model API, replace the
   extraction node's base URL/body with the API from `LLM_API_BASE_URL` and
   `LLM_MODEL`, then preserve the normalized product JSON contract. This is an
   optional local-model path and is not used by the offline test command. The
   LLM may extract or explain criteria; it must not silently introduce a
   criterion that the deterministic matcher cannot evaluate.
6. Persist the normalized product criteria and hand the upload/run identifier
   to Workflow B.

**Fallback rule:** real public pages are the default for Workflow A, but every
fixture result must carry an explicit source marker. A fixture fallback keeps a
local demo useful without claiming that the public site was fetched.

## Workflow B — deterministic eligibility and ranking

1. Receive the completed-ingestion callback from the backend and verify the
   `X-N8N-Timestamp` and `X-N8N-Signature` HMAC headers before doing work.
2. Load every accepted user row for the upload and every active product.
3. Run the `SQL Coarse Filter` with the known income, credit-score, age, and
   employment criteria. This is the optimization boundary: impossible
   user/product pairs are removed before n8n code evaluates them.
4. Evaluate every SQL candidate with n8n code. A failed known criterion produces
   `ineligible`; an unknown criterion produces `may_qualify`. The result records
   evaluated criteria, unknown criteria, and reasons.
5. Store every evaluated candidate in `matches`. The unique pair constraint
   makes a replay safe. Known-rejected pairs do not need a full n8n/LLM pass
   because the SQL stage already rejected them.
6. Calculate actual metrics from persisted candidate outcomes. Ranking is a
   later view over those stored matches: `TOP_K` limits displayed
   recommendations only; it never limits candidate evaluation or persistence.
7. Pass the selected presentation rows to Workflow C without deleting or
   rewriting the stored candidate match set.

The AWS ingestion Lambda sends the signed `users.ingestion.completed` callback
after database persistence when both `N8N_COMPLETION_WEBHOOK_URL` and the
Secrets Manager-backed `N8N_WEBHOOK_SECRET` are configured. A callback failure
fails the SQS record, so the event is retried; row-level idempotency prevents a
retry from adding a second user row.

### Metrics invariant

For every ingestion attempt, the counters must satisfy:

```text
total_rows = inserted_rows + duplicate_rows + invalid_rows + failed_rows
accepted_rows = inserted_rows + duplicate_rows
```

For Workflow B, the optimization metrics have a separate invariant:

```text
candidates <= naive
evaluated = candidates
stored = evaluated
```

Here `naive` is `accepted_users * active_products`. `candidates` is the number
of rows returned by the SQL coarse filter. This makes the reduction visible in
the n8n execution data and prevents an LLM call for every possible pair.

Repeated emails are not a reason to drop rows. User identity remains the upload
batch plus source row, while normalized recipient handling protects the email
boundary.

## Workflow C — recipient notification and completion

1. Read the ranked presentation rows and normalize each recipient with the
   backend recipient policy. Reject display names, lists, and CR/LF input.
2. Create one `email_deliveries` record per upload/user/recipient/template
   attempt before sending.
3. In local mode, send through the Mailpit HTTP API
   (`http://mailpit:8025/api/v1/send`) and inspect the Mailpit UI
   (`http://localhost:8025`). Mailpit proves only that the local delivery
   handoff worked; it is not proof that SES is configured or deliverable.
4. The SES branch is configured as an explicit provider branch. Keep it disabled
   for local work (`AWS_SES_ENABLED=false`). The AWS demonstration enables it
   only on the VPC-connected n8n host, with verified identities, SES sandbox
   limits, and an explicit controlled-recipient allowlist.
5. Update delivery status and send the signed completion callback with the
   upload identifier, actual metrics, and workflow status.

## n8n credential setup

Create credentials in the n8n UI; do not put their values in an exported
workflow or this repository. The checked-in exports use one PostgreSQL
credential name and HTTP nodes for local services.

| Credential | Local values / purpose |
| --- | --- |
| PostgreSQL | Credential name `Postgres local (configure in n8n)`; host `postgres`, port `5432`, database `loan_engine`, user `loan_engine`, password from `POSTGRES_PASSWORD`, SSL off. Used for products, users, matches, runs, and deliveries. |
| MinIO / S3 | Workflow exports do not currently use an S3 credential; the local ingestion worker reads MinIO. If a future n8n node reads objects, use endpoint `http://minio:9000`, region `us-east-1`, path-style addressing, and local keys from the stack. |
| Mailpit HTTP | Workflow C calls `http://mailpit:8025/api/v1/send` without SMTP credentials; Mailpit UI is `http://localhost:8025` from the host. |
| HTTP Request / local model | The offline node uses `LLM_STUB_URL=http://llm-stub:8090`; an actual local model API may be configured in the node using `LLM_API_BASE_URL` and `LLM_MODEL`, with any token kept in n8n's credential store. |
| SES (optional) | AWS credential name from `AWS_SES_CREDENTIAL_NAME`; disabled in local mode and enabled only on the controlled AWS demo n8n host. |

For the incoming backend callback, use an n8n Webhook node and perform
cryptographic HMAC verification before trusting a non-local request. The
current export validates the event shape and header presence; the shared value
is the local `N8N_WEBHOOK_SECRET` or the approved secret reference in a
deployment and must never be hard-coded in a workflow export.
