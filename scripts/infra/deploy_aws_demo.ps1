param(
    [string]$Profile = "loan-engine",
    [string]$Region = "ap-south-1",
    [string]$StackName = "loan-eligibility-engine-sandbox",
    [string]$InstanceType = "t4g.small"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$credentialLines = & aws configure export-credentials --profile $Profile --format powershell
if ($LASTEXITCODE -ne 0) { throw "Unable to export the active AWS SSO credentials for Serverless." }
Invoke-Expression ($credentialLines -join [Environment]::NewLine)

function Invoke-AwsJson {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $raw = & aws @Arguments --output json
    if ($LASTEXITCODE -ne 0) { throw "aws command failed: aws $($Arguments -join ' ')" }
    return ($raw | ConvertFrom-Json)
}

function Invoke-AwsText {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $raw = & aws @Arguments --output text
    if ($LASTEXITCODE -ne 0) { throw "aws command failed: aws $($Arguments -join ' ')" }
    return [string]$raw
}

function Get-OrCreateSecret {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$SecretString
    )
    try {
        $secret = Invoke-AwsJson @("secretsmanager", "describe-secret", "--profile", $Profile, "--region", $Region, "--secret-id", $Name)
    } catch {
        $secret = Invoke-AwsJson @("secretsmanager", "create-secret", "--profile", $Profile, "--region", $Region, "--name", $Name, "--secret-string", $SecretString, "--description", "Loan Eligibility Engine AWS demo secret")
    }
    $value = Invoke-AwsText @("secretsmanager", "get-secret-value", "--profile", $Profile, "--region", $Region, "--secret-id", $Name, "--query", "SecretString")
    [pscustomobject]@{ Arn = [string]$secret.ARN; Value = $value }
}

function Get-JsonSecretValue {
    param([Parameter(Mandatory = $true)][string]$SecretString)
    try {
        return ($SecretString | ConvertFrom-Json)
    } catch {
        return [pscustomobject]@{ API_AUTH_TOKEN = $SecretString }
    }
}

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$buildDir = Join-Path $root "aws-build"
if (-not (Test-Path -LiteralPath $buildDir)) {
    New-Item -ItemType Directory -Path $buildDir | Out-Null
}

$stack = Invoke-AwsJson @("cloudformation", "describe-stacks", "--profile", $Profile, "--region", $Region, "--stack-name", $StackName)
$stackData = $stack.Stacks[0]
$parameters = @{}
foreach ($parameter in $stackData.Parameters) { $parameters[[string]$parameter.ParameterKey] = [string]$parameter.ParameterValue }
$outputs = @{}
foreach ($output in $stackData.Outputs) { $outputs[[string]$output.OutputKey] = [string]$output.OutputValue }

$account = Invoke-AwsText @("sts", "get-caller-identity", "--profile", $Profile, "--query", "Account")
$bucket = $outputs["UploadsBucketName"]
$databaseSecretArn = $parameters["DatabaseSecretArn"]
$vpcId = (Invoke-AwsJson @("ec2", "describe-subnets", "--profile", $Profile, "--region", $Region, "--subnet-ids", $parameters["PrivateSubnetIds"].Split(",")[0])).Subnets[0].VpcId
$subnetId = $parameters["PrivateSubnetIds"].Split(",")[0]
$lambdaSecurityGroupId = $parameters["LambdaSecurityGroupId"]
$rdsEndpoint = $parameters["ExistingRdsEndpoint"]

$random = [guid]::NewGuid().ToString("N")
$webhookSecretValue = @{ N8N_WEBHOOK_SECRET = $random } | ConvertTo-Json -Compress
$apiTokenValue = @{ API_AUTH_TOKEN = ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")) } | ConvertTo-Json -Compress
$webhookSecret = Get-OrCreateSecret -Name "loan-engine/sandbox/n8n-webhook" -SecretString $webhookSecretValue
$apiToken = Get-OrCreateSecret -Name "loan-engine/sandbox/api-auth-token" -SecretString $apiTokenValue
$apiTokenObject = Get-JsonSecretValue -SecretString $apiToken.Value

$sesCredentialSecretName = "loan-engine/sandbox/n8n-ses-credential"
try {
    $sesCredentialSecret = Invoke-AwsJson @("secretsmanager", "describe-secret", "--profile", $Profile, "--region", $Region, "--secret-id", $sesCredentialSecretName)
    $sesCredentialValue = Invoke-AwsText @("secretsmanager", "get-secret-value", "--profile", $Profile, "--region", $Region, "--secret-id", $sesCredentialSecretName, "--query", "SecretString")
} catch {
    $keys = Invoke-AwsJson @("iam", "list-access-keys", "--profile", $Profile, "--user-name", "loan-engine-n8n-ses")
    if (@($keys.AccessKeyMetadata | Where-Object { $_.Status -eq "Active" }).Count -ge 2) {
        throw "The loan-engine-n8n-ses IAM user already has two active keys and no stored Secrets Manager credential. Delete one unused key or create the secret manually before rerunning."
    }
    $newKey = Invoke-AwsJson @("iam", "create-access-key", "--profile", $Profile, "--user-name", "loan-engine-n8n-ses")
    $sesCredentialValue = @{
        accessKeyId = [string]$newKey.AccessKey.AccessKeyId
        secretAccessKey = [string]$newKey.AccessKey.SecretAccessKey
    } | ConvertTo-Json -Compress
    $sesCredentialSecret = Invoke-AwsJson @("secretsmanager", "create-secret", "--profile", $Profile, "--region", $Region, "--name", $sesCredentialSecretName, "--secret-string", $sesCredentialValue, "--description", "Encrypted AWS SES credential for the demo n8n host")
}
$sesCredentialSecretArn = [string]$sesCredentialSecret.ARN
$sesCredentialObject = Get-JsonSecretValue -SecretString $sesCredentialValue

$n8nGroupName = "loan-engine-n8n-demo"
$existingGroup = Invoke-AwsJson @("ec2", "describe-security-groups", "--profile", $Profile, "--region", $Region, "--filters", "Name=group-name,Values=$n8nGroupName", "Name=vpc-id,Values=$vpcId")
if (@($existingGroup.SecurityGroups).Count -eq 0) {
    $n8nSecurityGroupId = Invoke-AwsText @("ec2", "create-security-group", "--profile", $Profile, "--region", $Region, "--group-name", $n8nGroupName, "--description", "Loan Eligibility Engine demo n8n host", "--vpc-id", $vpcId, "--query", "GroupId")
} else {
    $n8nSecurityGroupId = [string]$existingGroup.SecurityGroups[0].GroupId
}

$publicIp = (Invoke-RestMethod -Uri "https://api.ipify.org").Trim()
try { & aws ec2 authorize-security-group-ingress --profile $Profile --region $Region --group-id $n8nSecurityGroupId --protocol tcp --port 5678 --cidr "$publicIp/32" | Out-Null } catch { }
try { & aws ec2 authorize-security-group-ingress --profile $Profile --region $Region --group-id $lambdaSecurityGroupId --protocol tcp --port 5432 --source-group $n8nSecurityGroupId | Out-Null } catch { }
$secretsEndpoint = Invoke-AwsJson @("ec2", "describe-vpc-endpoints", "--profile", $Profile, "--region", $Region, "--filters", "Name=vpc-id,Values=$vpcId", "Name=service-name,Values=com.amazonaws.$Region.secretsmanager")
foreach ($endpointGroup in @($secretsEndpoint.VpcEndpoints.Groups)) {
    try { & aws ec2 authorize-security-group-ingress --profile $Profile --region $Region --group-id $endpointGroup.GroupId --protocol tcp --port 443 --source-group $n8nSecurityGroupId | Out-Null } catch { }
}

$trustPath = Join-Path $buildDir "n8n-trust-policy.json"
$policyPath = Join-Path $buildDir "n8n-instance-policy.json"
@{
    Version = "2012-10-17"
    Statement = @(@{ Effect = "Allow"; Principal = @{ Service = "ec2.amazonaws.com" }; Action = "sts:AssumeRole" })
} | ConvertTo-Json -Depth 8 | ForEach-Object { [System.IO.File]::WriteAllText($trustPath, $_, [System.Text.UTF8Encoding]::new($false)) }
@{
    Version = "2012-10-17"
    Statement = @(
        @{ Effect = "Allow"; Action = @("secretsmanager:GetSecretValue"); Resource = @($databaseSecretArn, $webhookSecret.Arn, $sesCredentialSecretArn) },
        @{ Effect = "Allow"; Action = @("s3:GetObject"); Resource = "arn:aws:s3:::$bucket/aws/*" },
        @{ Effect = "Allow"; Action = @("s3:ListBucket"); Resource = "arn:aws:s3:::$bucket"; Condition = @{ StringLike = @{ "s3:prefix" = "aws/*" } } },
        @{ Effect = "Allow"; Action = @("logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"); Resource = "*" }
    )
} | ConvertTo-Json -Depth 8 | ForEach-Object { [System.IO.File]::WriteAllText($policyPath, $_, [System.Text.UTF8Encoding]::new($false)) }

try { Invoke-AwsJson @("iam", "get-role", "--profile", $Profile, "--role-name", "loan-engine-n8n-ec2") | Out-Null } catch { & aws iam create-role --profile $Profile --role-name loan-engine-n8n-ec2 --assume-role-policy-document "file://$trustPath" | Out-Null }
& aws iam put-role-policy --profile $Profile --role-name loan-engine-n8n-ec2 --policy-name loan-engine-n8n-ec2-runtime --policy-document "file://$policyPath" | Out-Null
try { Invoke-AwsJson @("iam", "get-instance-profile", "--profile", $Profile, "--instance-profile-name", "loan-engine-n8n-ec2") | Out-Null } catch { & aws iam create-instance-profile --profile $Profile --instance-profile-name loan-engine-n8n-ec2 | Out-Null; Start-Sleep -Seconds 5 }
$profileState = Invoke-AwsJson @("iam", "get-instance-profile", "--profile", $Profile, "--instance-profile-name", "loan-engine-n8n-ec2")
if (@($profileState.InstanceProfile.Roles | Where-Object { $_.RoleName -eq "loan-engine-n8n-ec2" }).Count -eq 0) {
    & aws iam add-role-to-instance-profile --profile $Profile --instance-profile-name loan-engine-n8n-ec2 --role-name loan-engine-n8n-ec2 | Out-Null
}

& aws s3 cp (Join-Path $root "aws\n8n-compose.yml") "s3://$bucket/aws/n8n-compose.yml" --profile $Profile --region $Region | Out-Null
& aws s3 cp (Join-Path $root "local\n8n-bootstrap.js") "s3://$bucket/aws/n8n-bootstrap.js" --profile $Profile --region $Region | Out-Null
& aws s3 cp (Join-Path $root "n8n") "s3://$bucket/aws/n8n" --recursive --exclude "*.bak" --profile $Profile --region $Region | Out-Null
& aws s3 cp (Join-Path $root "llm_stub\server.py") "s3://$bucket/aws/llm_stub/server.py" --profile $Profile --region $Region | Out-Null

$userDataTemplate = Get-Content -LiteralPath (Join-Path $root "aws\n8n-ec2-user-data.sh") -Raw
$userData = $userDataTemplate.Replace("__REGION__", $Region).Replace("__BUCKET__", $bucket).Replace("__DATABASE_SECRET_ARN__", $databaseSecretArn).Replace("__N8N_WEBHOOK_SECRET_ARN__", $webhookSecret.Arn).Replace("__SES_CREDENTIAL_SECRET_ARN__", $sesCredentialSecretArn)
$userDataPath = Join-Path $buildDir "n8n-user-data.sh"
[System.IO.File]::WriteAllText($userDataPath, $userData, [System.Text.UTF8Encoding]::new($false))
& aws s3 cp $userDataPath "s3://$bucket/aws/n8n-user-data.sh" --profile $Profile --region $Region | Out-Null

$existingInstance = Invoke-AwsJson @("ec2", "describe-instances", "--profile", $Profile, "--region", $Region, "--filters", "Name=tag:Name,Values=loan-engine-n8n-demo", "Name=instance-state-name,Values=pending,running,stopping,stopped")
$instances = @($existingInstance.Reservations | ForEach-Object { $_.Instances } | Where-Object { $_ })
if ($instances.Count -eq 0) {
    $ami = Invoke-AwsText @("ssm", "get-parameter", "--profile", $Profile, "--region", $Region, "--name", "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-arm64", "--query", "Parameter.Value")
    $instanceId = Invoke-AwsText @("ec2", "run-instances", "--profile", $Profile, "--region", $Region, "--image-id", $ami, "--instance-type", $InstanceType, "--iam-instance-profile", "Name=loan-engine-n8n-ec2", "--network-interfaces", "DeviceIndex=0,SubnetId=$subnetId,Groups=$n8nSecurityGroupId,AssociatePublicIpAddress=true", "--user-data", "fileb://$userDataPath", "--tag-specifications", "ResourceType=instance,Tags=[{Key=Name,Value=loan-engine-n8n-demo}]", "--query", "Instances[0].InstanceId")
} else {
    $instanceId = [string]$instances[0].InstanceId
    $state = [string]$instances[0].State.Name
    if ($state -eq "stopped") { & aws ec2 start-instances --profile $Profile --region $Region --instance-ids $instanceId | Out-Null }
}
 $currentInstance = Invoke-AwsJson @("ec2", "describe-instances", "--profile", $Profile, "--region", $Region, "--instance-ids", $instanceId)
 if (-not $currentInstance.Reservations[0].Instances[0].IamInstanceProfile) {
     & aws ec2 associate-iam-instance-profile --profile $Profile --region $Region --instance-id $instanceId --iam-instance-profile "Name=loan-engine-n8n-ec2" | Out-Null
 }
& aws ec2 wait instance-running --profile $Profile --region $Region --instance-ids $instanceId
$instance = Invoke-AwsJson @("ec2", "describe-instances", "--profile", $Profile, "--region", $Region, "--instance-ids", $instanceId)
$privateIp = [string]$instance.Reservations.Instances[0].PrivateIpAddress
$publicIpAddress = [string]$instance.Reservations.Instances[0].PublicIpAddress

if (-not $publicIpAddress) {
    $allocation = Invoke-AwsJson @("ec2", "allocate-address", "--profile", $Profile, "--region", $Region, "--domain", "vpc")
    & aws ec2 associate-address --profile $Profile --region $Region --instance-id $instanceId --allocation-id $allocation.AllocationId | Out-Null
    $publicIpAddress = [string]$allocation.PublicIp
}

$callbackUrl = "http://$privateIp`:5678/webhook/loan-ingestion-completed"
$deployArgs = @(
    "--yes", "serverless@3.39.0", "deploy", "--stage", "sandbox", "--region", $Region,
    "--param=EnableApiRoutes=true",
    "--param=ApiAuthTokenSecretArn=$($apiToken.Arn)",
    "--param=DatabaseSecretArn=$databaseSecretArn",
    "--param=N8nWebhookSecretArn=$($webhookSecret.Arn)",
    "--param=N8nCompletionWebhookUrl=$callbackUrl",
    "--param=ExistingRdsEndpoint=$rdsEndpoint",
    "--param=ExistingRdsPort=5432",
    "--param=PrivateSubnetIds=$($parameters["PrivateSubnetIds"])",
    "--param=LambdaSecurityGroupId=$lambdaSecurityGroupId",
    "--param=PythonDependenciesLayerArn=$($parameters["PythonDependenciesLayerArn"])",
    "--param=WebOrigin=http://localhost:8080"
)
& npx @deployArgs
if ($LASTEXITCODE -ne 0) { throw "Serverless deployment failed" }

$updatedStack = Invoke-AwsJson @("cloudformation", "describe-stacks", "--profile", $Profile, "--region", $Region, "--stack-name", $StackName)
$updatedOutputs = @{}
foreach ($output in $updatedStack.Stacks[0].Outputs) { $updatedOutputs[[string]$output.OutputKey] = [string]$output.OutputValue }
$apiEndpoint = $updatedOutputs["ApiEndpoint"]
$uiConfigPath = Join-Path $buildDir "aws-demo-ui.env"
@("API_BASE_URL=$apiEndpoint", "API_TOKEN=$($apiTokenObject.API_AUTH_TOKEN)", "UI_URL=http://localhost:8080/?api=$apiEndpoint&token=$($apiTokenObject.API_AUTH_TOKEN)", "N8N_URL=http://$publicIpAddress`:5678", "AWS_N8N_PRIVATE_IP=$privateIp", "AWS_BUCKET=$bucket", "AWS_INSTANCE_ID=$instanceId", "AWS_UPLOAD_EXAMPLE=uploads/{uuid}/aws-demo.csv") | Set-Content -LiteralPath $uiConfigPath -Encoding utf8

Write-Output "AWS demo deployment complete."
Write-Output "API endpoint: $apiEndpoint"
Write-Output "n8n URL: http://$publicIpAddress`:5678"
Write-Output "Private n8n callback URL: $callbackUrl"
Write-Output "UI configuration (contains the private API token): $uiConfigPath"
