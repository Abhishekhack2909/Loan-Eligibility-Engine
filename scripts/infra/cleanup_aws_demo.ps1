param(
    [string]$Profile = "loan-engine",
    [string]$Region = "ap-south-1",
    [string]$StackName = "loan-eligibility-engine-sandbox",
    [string]$RdsIdentifier = "loan-engine-sandbox-db",
    [switch]$DeleteRds,
    [switch]$RemoveSesUser
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-AwsJson {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $raw = & aws @Arguments --output json
    if ($LASTEXITCODE -ne 0) { throw "aws command failed: aws $($Arguments -join ' ')" }
    if ([string]::IsNullOrWhiteSpace(($raw -join ""))) { return $null }
    return ($raw | ConvertFrom-Json)
}

function Invoke-Aws {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & aws @Arguments
    if ($LASTEXITCODE -ne 0) { throw "aws command failed: aws $($Arguments -join ' ')" }
}

function Try-Aws {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    try { Invoke-Aws -Arguments $Arguments } catch { Write-Warning $_.Exception.Message }
}

function Get-Stack {
    try {
        return Invoke-AwsJson @("cloudformation", "describe-stacks", "--profile", $Profile, "--region", $Region, "--stack-name", $StackName)
    } catch {
        if ($_.Exception.Message -match "does not exist|does not exist") { return $null }
        throw
    }
}

function Remove-S3Objects {
    param([Parameter(Mandatory = $true)][string]$Bucket)
    if ([string]::IsNullOrWhiteSpace($Bucket)) { return }

    while ($true) {
        $listing = Invoke-AwsJson @("s3api", "list-object-versions", "--profile", $Profile, "--region", $Region, "--bucket", $Bucket)
        $objects = @()
        foreach ($version in @($listing.Versions)) {
            if ($null -ne $version.Key -and $null -ne $version.VersionId) {
                $objects += @{ Key = [string]$version.Key; VersionId = [string]$version.VersionId }
            }
        }
        foreach ($marker in @($listing.DeleteMarkers)) {
            if ($null -ne $marker.Key -and $null -ne $marker.VersionId) {
                $objects += @{ Key = [string]$marker.Key; VersionId = [string]$marker.VersionId }
            }
        }
        if ($objects.Count -eq 0) { break }

        $deleteFile = Join-Path ([IO.Path]::GetTempPath()) ("loan-engine-delete-" + [guid]::NewGuid().ToString("N") + ".json")
        @{ Objects = $objects; Quiet = $true } |
            ConvertTo-Json -Depth 6 |
            Set-Content -LiteralPath $deleteFile -Encoding utf8
        try {
            Invoke-Aws @("s3api", "delete-objects", "--profile", $Profile, "--region", $Region, "--bucket", $Bucket, "--delete", "file://$deleteFile")
        } finally {
            Remove-Item -LiteralPath $deleteFile -Force -ErrorAction SilentlyContinue
        }
    }
}

$identity = Invoke-AwsJson @("sts", "get-caller-identity", "--profile", $Profile)
if ([string]$identity.Account -ne "726990466215") {
    throw "Refusing cleanup: active AWS account is $($identity.Account), expected 726990466215."
}
Write-Host "Cleaning AWS demo account $($identity.Account) in $Region" -ForegroundColor Yellow

$stack = Get-Stack
$stackData = $null
$parameters = @{}
$outputs = @{}
if ($null -ne $stack) {
    $stackData = $stack.Stacks[0]
    foreach ($parameter in @($stackData.Parameters)) { $parameters[[string]$parameter.ParameterKey] = [string]$parameter.ParameterValue }
    foreach ($output in @($stackData.Outputs)) { $outputs[[string]$output.OutputKey] = [string]$output.OutputValue }
}

$bucket = if ($outputs.ContainsKey("UploadsBucketName")) { $outputs["UploadsBucketName"] } else { "loan-eligibility-engine-sandbox-726990466215-uploads" }
$lambdaSecurityGroupId = if ($parameters.ContainsKey("LambdaSecurityGroupId")) { $parameters["LambdaSecurityGroupId"] } else { "" }
$privateSubnetIds = if ($parameters.ContainsKey("PrivateSubnetIds")) { $parameters["PrivateSubnetIds"] } else { "" }
$vpcId = ""
if ($privateSubnetIds) {
    $subnet = Invoke-AwsJson @("ec2", "describe-subnets", "--profile", $Profile, "--region", $Region, "--subnet-ids", $privateSubnetIds.Split(",")[0])
    $vpcId = [string]$subnet.Subnets[0].VpcId
}

$instanceResult = Invoke-AwsJson @(
    "ec2", "describe-instances", "--profile", $Profile, "--region", $Region,
    "--filters", "Name=tag:Name,Values=loan-engine-n8n-demo", "Name=instance-state-name,Values=pending,running,stopping,stopped"
)
$instances = @($instanceResult.Reservations | ForEach-Object { $_.Instances } | Where-Object { $_ })
$instanceIds = @($instances | ForEach-Object { [string]$_.InstanceId })
$allocationIds = @()
foreach ($instanceId in $instanceIds) {
    $addresses = Invoke-AwsJson @("ec2", "describe-addresses", "--profile", $Profile, "--region", $Region, "--filters", "Name=instance-id,Values=$instanceId")
    $allocationIds += @($addresses.Addresses | ForEach-Object { [string]$_.AllocationId })
}

$n8nGroups = Invoke-AwsJson @(
    "ec2", "describe-security-groups", "--profile", $Profile, "--region", $Region,
    "--filters", "Name=group-name,Values=loan-engine-n8n-demo"
)
$n8nSecurityGroupIds = @($n8nGroups.SecurityGroups | ForEach-Object { [string]$_.GroupId })

foreach ($groupId in $n8nSecurityGroupIds) {
    Try-Aws @("ec2", "revoke-security-group-ingress", "--profile", $Profile, "--region", $Region, "--group-id", $groupId, "--protocol", "tcp", "--port", "5678", "--cidr", "0.0.0.0/0")
    if ($lambdaSecurityGroupId) {
        Try-Aws @("ec2", "revoke-security-group-ingress", "--profile", $Profile, "--region", $Region, "--group-id", $lambdaSecurityGroupId, "--protocol", "tcp", "--port", "5432", "--source-group", $groupId)
    }
    if ($vpcId) {
        $endpoints = Invoke-AwsJson @(
            "ec2", "describe-vpc-endpoints", "--profile", $Profile, "--region", $Region,
            "--filters", "Name=vpc-id,Values=$vpcId", "Name=service-name,Values=com.amazonaws.$Region.secretsmanager"
        )
        foreach ($endpointGroup in @($endpoints.VpcEndpoints.Groups)) {
            Try-Aws @("ec2", "revoke-security-group-ingress", "--profile", $Profile, "--region", $Region, "--group-id", $endpointGroup.GroupId, "--protocol", "tcp", "--port", "443", "--source-group", $groupId)
        }
    }
}

if ($instanceIds.Count -gt 0) {
    Write-Host "Terminating n8n EC2 instance(s): $($instanceIds -join ', ')" -ForegroundColor Yellow
    $terminateArgs = @("ec2", "terminate-instances", "--profile", $Profile, "--region", $Region, "--instance-ids") + $instanceIds
    Invoke-Aws $terminateArgs
    $waitArgs = @("ec2", "wait", "instance-terminated", "--profile", $Profile, "--region", $Region, "--instance-ids") + $instanceIds
    Invoke-Aws $waitArgs
}
foreach ($allocationId in $allocationIds) {
    Try-Aws @("ec2", "release-address", "--profile", $Profile, "--region", $Region, "--allocation-id", $allocationId)
}

Try-Aws @("iam", "delete-role-policy", "--profile", $Profile, "--role-name", "loan-engine-n8n-ec2", "--policy-name", "loan-engine-n8n-ec2-runtime")
Try-Aws @("iam", "remove-role-from-instance-profile", "--profile", $Profile, "--instance-profile-name", "loan-engine-n8n-ec2", "--role-name", "loan-engine-n8n-ec2")
Try-Aws @("iam", "delete-instance-profile", "--profile", $Profile, "--instance-profile-name", "loan-engine-n8n-ec2")
Try-Aws @("iam", "delete-role", "--profile", $Profile, "--role-name", "loan-engine-n8n-ec2")

if ($null -ne $stackData) {
    Write-Host "Deleting CloudFormation stack $StackName" -ForegroundColor Yellow
    Invoke-Aws @("cloudformation", "delete-stack", "--profile", $Profile, "--region", $Region, "--stack-name", $StackName)
    Invoke-Aws @("cloudformation", "wait", "stack-delete-complete", "--profile", $Profile, "--region", $Region, "--stack-name", $StackName)
}

Remove-S3Objects -Bucket $bucket
Try-Aws @("s3api", "delete-bucket", "--profile", $Profile, "--region", $Region, "--bucket", $bucket)

foreach ($secretName in @("loan-engine/sandbox/api-auth-token", "loan-engine/sandbox/n8n-webhook", "loan-engine/sandbox/n8n-ses-credential")) {
    Try-Aws @("secretsmanager", "delete-secret", "--profile", $Profile, "--region", $Region, "--secret-id", $secretName, "--force-delete-without-recovery")
}

$sesUser = "loan-engine-n8n-ses"
try {
    $keys = Invoke-AwsJson @("iam", "list-access-keys", "--profile", $Profile, "--user-name", $sesUser)
    foreach ($key in @($keys.AccessKeyMetadata)) {
        Try-Aws @("iam", "delete-access-key", "--profile", $Profile, "--user-name", $sesUser, "--access-key-id", $key.AccessKeyId)
    }
    Try-Aws @("iam", "delete-user-policy", "--profile", $Profile, "--user-name", $sesUser, "--policy-name", "loan-engine-ses-send")
    if ($RemoveSesUser) { Try-Aws @("iam", "delete-user", "--profile", $Profile, "--user-name", $sesUser) }
} catch { Write-Warning "SES IAM cleanup skipped: $($_.Exception.Message)" }

try {
    $rds = Invoke-AwsJson @("rds", "describe-db-instances", "--profile", $Profile, "--region", $Region, "--db-instance-identifier", $RdsIdentifier)
    $rdsStatus = [string]$rds.DBInstances[0].DBInstanceStatus
    if ($DeleteRds) {
        $snapshot = ($RdsIdentifier + "-final-" + (Get-Date -Format "yyyyMMddHHmmss")).ToLower()
        Write-Host "Deleting RDS $RdsIdentifier with final snapshot $snapshot" -ForegroundColor Yellow
        Invoke-Aws @("rds", "delete-db-instance", "--profile", $Profile, "--region", $Region, "--db-instance-identifier", $RdsIdentifier, "--final-db-snapshot-identifier", $snapshot)
    } elseif ($rdsStatus -eq "available") {
        Write-Host "Stopping RDS $RdsIdentifier" -ForegroundColor Yellow
        Invoke-Aws @("rds", "stop-db-instance", "--profile", $Profile, "--region", $Region, "--db-instance-identifier", $RdsIdentifier)
        Write-Warning "RDS stopped mode is temporary; AWS may restart it after seven days. Re-run with -DeleteRds for permanent removal with a final snapshot."
    } else {
        Write-Host "RDS $RdsIdentifier is already $rdsStatus" -ForegroundColor Green
    }
} catch { Write-Warning "RDS cleanup skipped: $($_.Exception.Message)" }

foreach ($groupId in $n8nSecurityGroupIds) {
    Try-Aws @("ec2", "delete-security-group", "--profile", $Profile, "--region", $Region, "--group-id", $groupId)
}

$logGroups = Invoke-AwsJson @("logs", "describe-log-groups", "--profile", $Profile, "--region", $Region, "--log-group-name-prefix", "/aws/lambda/$StackName")
foreach ($logGroup in @($logGroups.logGroups)) {
    Try-Aws @("logs", "delete-log-group", "--profile", $Profile, "--region", $Region, "--log-group-name", $logGroup.logGroupName)
}

Write-Host "AWS demo cleanup finished. The GitHub repository and local project were not changed." -ForegroundColor Green
