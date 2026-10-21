/* Provision local n8n without assuming the slim image contains a shell.
 * The official n8n image has Node and the n8n CLI, so a tiny Node launcher is
 * more portable than `sh -c` and keeps provisioning idempotent. */
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const marker = process.env.N8N_PROVISION_MARKER || '/home/node/.n8n/.loan-engine-provisioned-v1';
const workflowDir = '/data/workflows';

function run(command, args) {
  const result = spawnSync(command, args, { stdio: 'inherit', env: process.env });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} ${args.join(' ')} exited with ${result.status}`);
}

if (!fs.existsSync(marker)) {
  const credentialFile = path.join(os.tmpdir(), 'loan-engine-postgres-credential.json');
  const credentials = [{
    id: 'loan-engine-postgres-local',
    name: process.env.N8N_POSTGRES_CREDENTIAL_NAME || 'Postgres local (configure in n8n)',
    type: 'postgres',
    data: {
      host: process.env.N8N_POSTGRES_HOST || 'postgres',
      port: Number(process.env.N8N_POSTGRES_PORT || 5432),
      database: process.env.N8N_POSTGRES_DATABASE || 'loan_engine',
      user: process.env.N8N_POSTGRES_USER || 'loan_engine',
      password: process.env.N8N_POSTGRES_PASSWORD || process.env.POSTGRES_PASSWORD || 'loan_engine_local',
      ssl: process.env.N8N_POSTGRES_SSL || 'disable',
      allowUnauthorizedCerts: process.env.N8N_POSTGRES_ALLOW_UNAUTHORIZED_CERTS === 'true'
    },
    nodesAccess: []
  }];
  if (process.env.AWS_SES_ACCESS_KEY_ID && process.env.AWS_SES_SECRET_ACCESS_KEY) {
    credentials.push({
      id: 'loan-engine-aws-ses',
      name: process.env.AWS_SES_CREDENTIAL_NAME || 'AWS SES (configure in n8n)',
      type: 'aws',
      data: {
        accessKeyId: process.env.AWS_SES_ACCESS_KEY_ID,
        secretAccessKey: process.env.AWS_SES_SECRET_ACCESS_KEY,
        region: process.env.AWS_SES_REGION || 'ap-south-1'
      },
      nodesAccess: []
    });
  }
  fs.writeFileSync(credentialFile, JSON.stringify(credentials));
  run('n8n', ['import:credentials', `--input=${credentialFile}`]);
  for (const filename of ['01-public-loan-catalog.json', '02-ingestion-match.json', '03-email-delivery.json']) {
    // n8n's CLI import format is an array even when the checked-in export is
    // a single workflow object, which is the friendlier format for reviewers.
    const workflow = JSON.parse(fs.readFileSync(path.join(workflowDir, filename), 'utf8'));
    const workflowFile = path.join(os.tmpdir(), `loan-engine-${filename}`);
    fs.writeFileSync(workflowFile, JSON.stringify([workflow]));
    run('n8n', ['import:workflow', `--input=${workflowFile}`]);
    fs.rmSync(workflowFile, { force: true });
  }
  run('n8n', ['update:workflow', '--id=public-loan-catalog-ingestion', '--active=true']);
  run('n8n', ['update:workflow', '--id=loan-ingestion-match', '--active=true']);
  run('n8n', ['update:workflow', '--id=loan-eligibility-email-delivery', '--active=true']);
  fs.writeFileSync(marker, new Date().toISOString());
  fs.rmSync(credentialFile, { force: true });
}

const child = require('child_process').spawn('n8n', ['start'], { stdio: 'inherit', env: process.env });
child.on('exit', code => process.exit(code === null ? 1 : code));
