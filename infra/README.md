# Docker and AWS deployment reference

This reference provisions infrastructure, not live remediation capabilities.
The application still uses synthetic sources/execution. No AWS resources are
created merely by checking out these files or running the offline checks below.
Production release gates in the root operations/security documents still apply.

## Local Docker Compose

Prerequisites: Docker Engine/Desktop with Linux containers, BuildKit and Docker
Compose v2.20+; the installed application CLI for the initial `ops init`.
From the project root:

```powershell
# Only if .env does not already exist; init refuses to overwrite it.
.\.venv\Scripts\python.exe -m ops.cli init --demo
docker compose up --build -d
docker compose ps --all
curl.exe --fail http://127.0.0.1:8000/health/live
curl.exe --fail http://127.0.0.1:8000/health/ready
docker compose exec worker python -m ops.cli worker-health
```

No extra credential setup is necessary. An `.env` with only the generated
`OPS_SIGNING_KEY`, `OPS_DEMO_MODE=true` and `OPS_DATABASE_URL=sqlite:///ops.db`
also works. Compose explicitly overrides the database with PostgreSQL 16, sets
Redis 7 and `OPS_ENVIRONMENT=local`, and does **not** copy or mount `.env` into
the image. Its allowlisted environment ignores unrelated host `.env` settings.
The SQLite file created by `ops init` is unused by containers.

**Development-only credential reuse:** the random, URL-safe generated signing
key is also the local PostgreSQL password and Redis AUTH credential. This avoids
a second setup step; it is intentionally NOT the production credential model.
Do not substitute a short/human password or a value containing URL metacharacters.
Anyone with Docker access can inspect container environment/process arguments.
Do not print `docker compose config` into shared logs: interpolation exposes
credentials. Demo mode must remain limited to this local trusted environment.

Only `127.0.0.1:8000` is published. PostgreSQL and Redis have no host ports.
PostgreSQL persists in the `postgres-data` named volume. Redis wakeups are
ephemeral, because PostgreSQL is the authoritative durable queue. Redis and
database transport are not TLS inside this local-only Compose network.
Healthy data services precede a one-shot `ops migrate`; API and worker wait for
its successful completion, and API additionally waits for the worker heartbeat.
The image/API healthcheck is liveness; use `/health/ready` to check dependencies.
Local readiness is intentionally weaker than production readiness.

```powershell
docker compose logs --tail 100 migrate api worker
docker compose down
# Keep the PostgreSQL volume to retain state.
```

Changing the signing key after PostgreSQL initialized does not update its stored
password. Keep the existing key, or perform an explicit database password
rotation; do not delete a volume to recover production data. For a disposable
local reset only, `docker compose down --volumes` removes ALL Compose data.

The multi-stage Python 3.13 Debian slim image builds wheels from
`pyproject.toml` and `src`, using `requirements.lock` as pip constraints, then
installs production dependencies only from that wheelhouse. The lock is not
installed as a requirements list: its development/audit packages are not pulled
into the image unless needed by the application's production dependencies.
Keep the manifest and parent-maintained Python 3.13 lock in sync; incompatible
pins fail the image build instead of silently selecting other versions. It runs
as UID/GID 10001 with no source-tree `.env`, tests, VCS or local databases in the
build context. Compose drops capabilities and uses a read-only filesystem plus
ephemeral `/tmp`. AWS service containers also use read-only filesystems.
The base image is pinned by multi-platform digest, and the official RDS CA
bundle by SHA256. Review/update both pins for patches and CA rollover.
Production dependency versions are constrained by the full resolution lock;
build requirements remain controlled by the manifest's build-system section.
The version-only lock does not authenticate artifacts with hashes or guarantee
bit-for-bit wheel builds. Do not claim bit-for-bit rebuilds:
build once, scan, retain and deploy the same ECR digest for promotion/rollback.

## AWS topology and controls

| Surface | Reference behavior |
| --- | --- |
| Network | Two availability zones; public ALB/NAT subnets, private Fargate subnets and isolated data subnets. One NAT gateway per AZ. No public task or database IPs. |
| Ingress | Supplied regional ACM certificate, HTTPS listener using TLS 1.2/1.3, HTTP-to-HTTPS redirect. ALB alone may reach API TCP 8000. Worker/bootstrap have no ingress. TLS terminates at ALB; the private ALB-to-API hop is HTTP. |
| Egress | Tasks may reach their PostgreSQL SG on 5432, API/worker may reach Redis SG on 6379, and HTTPS 443 via NAT for ECR, logs, Secrets Manager and OIDC. Data SGs have no initiating egress. VPC-provided DNS is not filtered by SG rules. HTTPS egress is not hostname allowlisted; use a governed proxy/firewall if required. |
| Compute | Separate Fargate Linux/X86_64 API and worker task/service definitions; 512 CPU units/1 GiB default. Counts default to zero. Immutable images, 100/200% rolling deployments and rollback circuit breakers. No ECS Exec. |
| Database | RDS PostgreSQL 16, encrypted gp3, Multi-AZ default, forced TLS, 14-day backups, storage autoscaling to 100 GiB, deletion protection/final snapshot. AWS manages the admin password. Separate restricted application identity. |
| Cache/queue | Two-node Redis 7.1 replication group, Multi-AZ failover, AUTH, required TLS, encryption at rest. Redis accelerates wakeups only; durable jobs/leases/retries and audit live in PostgreSQL. Losing Redis does not lose jobs, but production readiness fails until Redis recovers. |
| IAM | Separate execution roles pull only this ECR repository, write only their own logs and inject only allowed secrets. Task roles have no AWS permissions. Only bootstrap execution can inject the RDS admin secret. No task can perform live AWS remediation. |
| Secrets | Precreated application secret JSON, injected by ECS at startup. Terraform does not read its value. Optional customer KMS key support. No secrets in tfvars, image, outputs or command arguments. Redis AUTH is the exception: its sensitive Terraform input is present in state/plan. |
| Monitoring | Structured app logs and RDS logs with finite retention, Container Insights, ALB errors/unhealthy hosts, task shortfalls, RDS storage/CPU and per-node cache-memory alarms. Optional SNS email requires confirmation. |

The default RDS version is explicit. Verify its availability in your region
before applying; use a supported PostgreSQL 16 minor via `db_engine_version`
when necessary. AWS minor-version auto-upgrades are enabled, so platform patch
levels can evolve; review subsequent plans rather than forcing a downgrade.

## Prerequisites and encrypted remote state

Install Terraform >=1.10,<2 (validated with 1.13.5), AWS CLI v2, Docker/buildx,
Python 3.13, and **PowerShell 7.4+ for the command blocks below**. AWS provider
6.13.x is bounded in `versions.tf`; `.terraform.lock.hcl` records the selected
version and official checksums. Do not casually run `init -upgrade`.

Use an authorized AWS account/region with two standard AZs and adequate
VPC/EIP/NAT/Fargate/RDS/cache quotas. Obtain a validated regional ACM certificate
covering a DNS name you control, and trusted OIDC issuer/audience/HTTPS JWKS
values. Provisioner permissions include the modeled resources and IAM role
creation; deploy operators need scoped ECR push, ECS RunTask/DescribeTasks,
CloudWatch logs, Secrets Manager secret setup, and `iam:PassRole` for the exact
task/execution roles with `iam:PassedToService=ecs-tasks.amazonaws.com`.
Guard bootstrap RunTask and its PassRole particularly closely: bootstrap holds
database-admin credentials, even though its IAM task role is empty.

Create the backend **outside this stack**: private S3 bucket with Block Public
Access, versioning, SSE-KMS, a bucket policy denying non-TLS requests, and a
customer KMS key restricted to deployment/recovery operators. Grant only the
state prefix (`GetObject`/`PutObject`), its `.tflock` object (also `DeleteObject`),
necessary bucket listing and KMS encrypt/decrypt/data-key permissions. Use
native S3 lockfiles (`use_lockfile=true`), not an unprotected local state file.
Retain state object versions according to your recovery and secret-retention
policy. Never put AWS credentials in backend configuration.

```powershell
# Project root, PowerShell 7.4+. Stop if ANY command fails.
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$env:AWS_PAGER = ""
$env:AWS_REGION = "us-east-1"
aws sts get-caller-identity
Copy-Item .\infra\backend.hcl.example .\infra\backend.hcl
Copy-Item .\infra\terraform.tfvars.example .\infra\terraform.tfvars
# Edit these two private files for YOUR bucket, region, account, certificate and IdP.
terraform -chdir=infra init -backend-config=backend.hcl -lockfile=readonly
```

Terraform state and saved plans contain the cache AUTH token despite
`sensitive=true`. Protect them as secrets, including old backend versions and
local plan files; never upload a plan to an untrusted artifact store. The current
application has no ElastiCache IAM token-refresh adapter, so static AUTH is
intentional. Generate a 64-character alphanumeric token in your approved password
manager, save it there, and load the **same value on every plan/apply**:

```powershell
$cacheCredential = Read-Host "Existing cache token from password manager" -AsSecureString
$env:TF_VAR_redis_auth_token = [System.Net.NetworkCredential]::new("", $cacheCredential).Password
```

Do not put the token directly in shell history or regenerate it for each run.
Disable shell transcripts/debug tracing; use a private administrative machine.
The application secret and RDS-managed admin secret are independent of this
token and of each other.

## Stage 1: provision idle infrastructure and publish the immutable image

Create an **empty** application secret first, or obtain the ARN of one already
created by your security operator. Terraform must not own its value:

```powershell
$appSecretArn = aws secretsmanager create-secret --name acme-ops-app --query ARN --output text
```

Save that ARN as `app_secret_arn` in `infra\terraform.tfvars`. For a customer
KMS key, create the secret with `--kms-key-id <key-arn>`, set
`app_secret_kms_key_arn`, and authorize the execution roles in its key policy.
The default AWS-managed `aws/secretsmanager` key needs no custom key setting.
Use this account and region for both secret and certificate.

Set `image_uri` to the future unique release tag in this stack's repository,
for example `<account>.dkr.ecr.<region>.amazonaws.com/acme-ops:release-20260922-01`.
Set `desired_count=0` and leave `worker_desired_count` unset/null. ECS can register
a task definition referencing an absent image/empty secret while no tasks run.

```powershell
terraform -chdir=infra plan -out=provision.tfplan
terraform -chdir=infra apply provision.tfplan
Remove-Item .\infra\provision.tfplan

$repository = terraform -chdir=infra output -raw ecr_repository_url
$registry = $repository.Split("/")[0]
$tag = "release-20260922-01" # Must match the unique tag you selected above.
aws ecr get-login-password --region $env:AWS_REGION | docker login --username AWS --password-stdin $registry
docker buildx build --platform linux/amd64 --target runtime --tag "${repository}:$tag" --push .
$digest = aws ecr describe-images --repository-name acme-ops --image-ids "imageTag=$tag" --query "imageDetails[0].imageDigest" --output text
if ($digest -notmatch '^sha256:[a-f0-9]{64}$') { throw "No immutable image digest returned" }
"Deploy this image_uri: ${repository}@$digest"
aws ecr wait image-scan-complete --repository-name acme-ops --image-id "imageDigest=$digest"
aws ecr describe-image-scan-findings --repository-name acme-ops --image-id "imageDigest=$digest" --query "imageScanFindings.findingSeverityCounts"
```

Use your configured `name` instead of `acme-ops` in ECR commands if changed.
Review findings against your release policy; push a new unique tag for a rebuild
(ECR rejects overwrites). Basic ECR scanning is not an application dependency
audit; use your approved supply-chain scanner as well. If registry-wide enhanced
scanning is configured, use that scan workflow instead of the basic-scan waiter.
Set `image_uri` in tfvars to the **actual printed repository@sha256:digest**.
All task definitions, including bootstrap, must use that same reviewed image:

```powershell
terraform -chdir=infra plan -out=image.tfplan
terraform -chdir=infra apply image.tfplan
Remove-Item .\infra\image.tfplan
```

These are still stage 1 operations: both service counts remain zero.

## Stage 2: populate secrets, bootstrap the schema, then enable services

`prepare_secret.py` uses only Python's standard library and your AWS CLI. It
refuses to overwrite a secret with existing versions. It generates independent
random application database/signing credentials and uses the existing cache
token. Only the startup secret carries these values; Terraform sees its ARN.
The JSON keys are `database_url`, `signing_key` (64 URL-safe random characters)
and `redis_url`. The database URL uses `postgresql+psycopg`, `ops_app`,
`sslmode=verify-full` and the pinned RDS CA bundle; Redis uses `rediss`, required
certificate validation and hostname verification.

```powershell
# Recover this ARN from your private tfvars if starting a new shell.
# $appSecretArn = "your existing application secret ARN"
$databaseHost = terraform -chdir=infra output -raw database_host
$redisHost = terraform -chdir=infra output -raw redis_host
python .\infra\prepare_secret.py --secret-arn $appSecretArn --database-host $databaseHost --redis-host $redisHost --region $env:AWS_REGION
```

The helper submits a short-lived AWS CLI JSON file in the OS user temp
directory, not the workspace, and deletes it on exit. POSIX files are mode 0600.
On Windows, ensure `%TEMP%` inherits only your account/System/admin ACLs; do not
run on shared/untrusted temp storage. A killed process can leave a temp file:
remove the specific `acme-ops-secret-*` directory after inspecting it. Do not
run concurrent secret initialization. Existing populated secrets may instead
be prepared by a security operator with the same keys/URL formats; skip the
helper rather than bypass its overwrite protection.

Now run the privileged **one-shot bootstrap task**, not the API task with an
admin override. It injects the AWS-managed RDS admin credentials, runs
`python -m ops.cli migrate`, creates the separate `ops_app` login and a NOLOGIN
`ops_owner`, transfers schema/table ownership, then verifies restricted
application connectivity. Runtime can read/write application state but cannot
create schemas/tables/roles/databases; audit events allow SELECT/INSERT only.
The schema grants are explicit for the version-one metadata. New tables,
sequences or stored routines require a reviewed migration and grant update.

```powershell
$cluster = terraform -chdir=infra output -raw cluster_name
$bootstrap = terraform -chdir=infra output -raw bootstrap_task_definition_arn
$networkFile = Join-Path ([System.IO.Path]::GetTempPath()) ("ops-network-" + [guid]::NewGuid() + ".json")
try {
    terraform -chdir=infra output -json bootstrap_network_configuration | Set-Content -Encoding utf8NoBOM $networkFile
    $launch = aws ecs run-task --cluster $cluster --task-definition $bootstrap --launch-type FARGATE --platform-version 1.4.0 --network-configuration "file://$networkFile" --count 1 --started-by schema-bootstrap | ConvertFrom-Json
    if ($launch.failures.Count -gt 0 -or $launch.tasks.Count -ne 1) {
        throw ("Bootstrap launch failed: " + ($launch.failures | ConvertTo-Json -Depth 10))
    }
    $taskArn = $launch.tasks[0].taskArn
    aws ecs wait tasks-stopped --cluster $cluster --tasks $taskArn
    $result = aws ecs describe-tasks --cluster $cluster --tasks $taskArn | ConvertFrom-Json
    if ($result.failures.Count -gt 0 -or $result.tasks.Count -ne 1) { throw "Unable to describe bootstrap task" }
    $container = $result.tasks[0].containers | Where-Object name -eq bootstrap
    if ($null -eq $container.exitCode -or $container.exitCode -ne 0) {
        throw ("Bootstrap failed: " + $result.tasks[0].stoppedReason)
    }
} finally {
    Remove-Item -LiteralPath $networkFile -ErrorAction SilentlyContinue
}
aws logs tail /ecs/acme-ops/bootstrap --since 1h
```

An ECS STOPPED status alone is **not success**: require container exit code zero.
On failure, leave counts zero and inspect the bootstrap log and ECS stopped
reason. `migrate` is `create_all`, a version-one bootstrap, NOT a schema upgrade
engine. Re-running bootstrap after an interrupted initial setup is supported,
but only with the same secret and all services stopped. It is not a credential
rotation or a production migration command. It does not erase existing tables.

Create a Route 53 alias (or your DNS provider's supported ALB alias/CNAME) for
your certificate's name using `alb_dns_name`/`alb_zone_id`. DNS and certificate
validation are deliberately outside this stack. Do not test HTTPS against the
raw ALB DNS name or disable certificate verification.

Set `desired_count=2` in tfvars for API and worker replicas, keeping
`worker_desired_count` unset/null. For worker-first rollout, use
`desired_count=0, worker_desired_count=2`, wait for workers, then set API to 2.
API counts above zero with zero workers are rejected by a Terraform precondition.

```powershell
terraform -chdir=infra plan -out=enable.tfplan
terraform -chdir=infra apply enable.tfplan
Remove-Item .\infra\enable.tfplan
$services = terraform -chdir=infra output -json service_names | ConvertFrom-Json
aws ecs wait services-stable --cluster $cluster --services $services.api $services.worker
aws ecs describe-services --cluster $cluster --services $services.api $services.worker --query "services[].{name:serviceName,running:runningCount,desired:desiredCount,deployments:deployments[].{status:rolloutState,reason:rolloutStateReason,task:taskDefinition}}"
curl.exe --fail https://ops.example.com/health/live
curl.exe --fail https://ops.example.com/health/ready
Remove-Item Env:TF_VAR_redis_auth_token
```

Replace `ops.example.com` with the real certificate/DNS name. Check that the
PRIMARY deployment uses the intended `task_definition_arns` output and is
COMPLETED: a service can stabilize after rolling back to an older revision.
Production readiness requires schema access, Redis and a recent worker heartbeat.
The ECS API container check is `/health/live`; ALB uses `/health/ready`.
Worker health executes `ops worker-health`, which checks that container's
hostname heartbeat, not any worker's heartbeat. Readiness failures make ALB
targets unhealthy and can trigger ECS replacements despite healthy liveness;
investigate shared dependency outages before forcing repeated deployments.

## GitHub publishing and deployment integration

The repository's `publish.yml` publishes a successful main-branch CI commit to
ECR under its full 40-character lowercase commit SHA. `deploy.yml` is a separate,
approved dispatch of that already-published SHA; `scripts/deploy_ecs.py` resolves
the tag to its immutable digest. These workflows do not provision infrastructure,
populate secrets or enable services from zero. Complete stages 1 and 2 above
first: **both existing services must have desired count at least one**.

Configure these GitHub repository/environment variables in the relevant
`image-publishing` and `production` environments:

| Variable | Value |
| --- | --- |
| `AWS_REGION` | The Terraform `aws_region`, available to both workflows. |
| `ECR_REPOSITORY` | Repository **name**, not URI: Terraform `name` (default `acme-ops`), available to both workflows. |
| `ECS_CLUSTER` | Terraform `cluster_name` output. |
| `API_SERVICE` | The `api` value in Terraform `service_names`. |
| `WORKER_SERVICE` | The `worker` value in Terraform `service_names`. |
| `AWS_PUBLISH_ROLE_ARN` | External GitHub OIDC publishing role for `image-publishing`. |
| `AWS_DEPLOY_ROLE_ARN` | Separate external GitHub OIDC deployment role for `production`. |

These federated workflow roles are deliberately not ECS task or execution roles,
and are not provisioned by this reference. Restrict their GitHub OIDC trust to
your exact repository/environment subjects and `aud=sts.amazonaws.com`. Enforce
protected branches, environment deployment restrictions and required reviewers.
Use short-lived OIDC credentials, not AWS access keys stored in GitHub.

The publishing role needs ECR authorization-token access and repository-scoped
layer-upload/image-push permissions, not ECS or database administration. The
deployment role needs ECR image/repository discovery, ECS service/task-definition
discovery and revision registration, RunTask for the worker family, task status
reads, and UpdateService for the two approved services. Scope resources and
cluster/family conditions where supported; some ECS registration/discovery
actions and ECR authorization-token access require `"*"`. Restrict its
`iam:PassRole` to the API/worker task and execution roles with
`iam:PassedToService=ecs-tasks.amazonaws.com`. It must not pass bootstrap roles,
read the RDS admin secret, modify Terraform resources or provision infrastructure.
**Runtime task roles remain permissionless**; do not grant them deployment or
remediation permissions to make a workflow succeed.

Each API/worker task definition has exactly one container, matching the deploy
script's checked contract. The script clones each current service definition,
changes its image, and registers a revision, preserving networking, runtime and
execution roles, injected secrets and health settings. It runs the new worker
revision once with `python -m ops.cli migrate`, requires exit code zero, and only
then rolls the worker followed by the API. It checks that each PRIMARY deployment
is COMPLETED on the intended revision, rather than accepting stabilization after
rollback. Review the script before adding sidecars or changing this contract.

**Schema boundary:** this workflow's `migrate` runs as restricted `ops_app`, not
the admin bootstrap identity. On the already-bootstrapped version-one schema,
`create_all` is a no-op. Missing/new tables will fail because runtime has no DDL
privileges; the script must stop rather than escalate. Actual schema evolution
needs a separately approved migration identity and ordered expand/contract
migrations before the release. Do not relax database grants or substitute the
privileged initial bootstrap task into routine deployment automation.

Workflow deployment changes ECS outside Terraform. After an approved release,
record that same deployed digest as `image_uri` in the private tfvars before the
next Terraform plan. Review reconciliation: Terraform may register equivalent
revisions and update the service references, and its task-definition outputs
still identify its own managed revisions until then. Applying stale tfvars can
roll the services back to an old image. Do not run Terraform applies concurrently
with deployments; workflow concurrency does not lock direct Terraform operations.

CI's Terraform 1.13.3 is within the supported version range and performs
format/init-without-backend/validate checks; the local native mock tests are
additional coverage. Compose interpolation needs only `OPS_SIGNING_KEY`: demo
mode defaults to false and database/Redis URLs are set by Compose. CI-only
validation keys are not credentials for a deployed environment.

## Operations, rollback, rotation and cost

Manual Terraform releases repeat image push/scan/digest pin, reviewed schema
changes, plan/apply and rollout/readiness verification. Workflow releases use
the integration above and still require readiness verification. The 100/200%
strategy needs temporary capacity for twice the desired tasks. Circuit-breaker rollback needs a prior
successful deployment; a first deployment has nothing to roll back to.
Automatic rollback does not restore the database or revert secret values.
For manual rollback, set the previous retained digest in tfvars and plan/apply.
Use only backward-compatible expand/contract migrations so the old image can
read the current schema. Restore RDS backups into a separate instance and rehearse
cutover; this reference is not a tested disaster-recovery service.

Workers receive at most 120 seconds of ECS stop grace. Work exceeding it may be
killed and then retried after its PostgreSQL lease expires (default 300 seconds).
Maintain bounded work, idempotency and lease budgets; do not assume shutdown
always drains every job. Services are not autoscaled by this reference.

Confirm an optional SNS email subscription, route alarms to an on-call owner
and test delivery. SNS/topic policy and account-level restrictions must allow
CloudWatch alarm delivery. Without `alarm_email`, alarms still exist but have
no notification actions. Task shortfall alarms disable actions while scaled to
zero; missing Container Insights metrics otherwise alarm. Retained logs and
Container Insights incur charges. Non-blocking application log buffers may
drop logs under sustained backpressure; PostgreSQL audit is the durable record.
Authenticated application metrics need a separately governed scraper/exporter;
this stack does not pretend process-local worker histograms are fleet metrics.

Execution roles receive startup-only secrets; tasks cannot read Secrets Manager
themselves. A secret change does not refresh running container environments.
Coordinate database/cache credential changes, update the secret, and force a
fresh rollout of API and worker using `aws ecs update-service --cluster $cluster
--service <service-name> --force-new-deployment`. Do not regenerate the signing
key blindly: it invalidates existing signed audit envelopes without a key-ring
migration. AWS rotates the RDS admin credential independently; bootstrap loads
its current value on each new task.

The Redis resource uses AUTH `SET`; changing its input is a cutover, not a
zero-downtime dual-token rotation. Schedule an outage or implement a reviewed
ROTATE-then-SET sequence, with matching application-secret updates and task
restarts. Retain the original AUTH token securely while rollback is possible.
Do not rerun `prepare_secret.py` or initial bootstrap as a rotation shortcut.

**Idle is not free.** Counts of zero stop Fargate compute, but two NAT gateways
and their EIPs, ALB, Multi-AZ RDS, two cache nodes, storage/backups, ECR, Secrets
Manager, logs and metrics still bill. NAT gateways have hourly and per-GB
processing charges; public IPv4 and cross-AZ traffic also cost money. Budget
before stage 1. Production keeps per-AZ NAT/Multi-AZ; cost reductions require
explicit availability tradeoffs. VPC endpoints can reduce some NAT traffic,
but have their own hourly charges and do not replace external OIDC egress.

For teardown, stop traffic and workers, take/export required backups and audit,
then set both service counts to zero. Set `deletion_protection=false` and apply
before destroy. Choose a **unique** `final_snapshot_identifier`, apply it, review
`terraform plan -destroy -out=cleanup.tfplan`, then `terraform apply cleanup.tfplan`.
RDS retains a final snapshot and automated backups; these continue billing.
ECR intentionally refuses deletion while nonempty: export/retain required image
digests, explicitly delete only this repository's unwanted images, then retry.
Redis has no snapshot because jobs are in PostgreSQL. The external app secret,
backend bucket/KMS key, domain and certificate are not destroyed by this stack.
Handle their retention/deletion separately; do not delete the backend before
the stack and recovery obligations are resolved.

## Offline validation

No AWS apply or account credentials are needed for:

```powershell
terraform -chdir=infra init -backend=false -input=false
terraform -chdir=infra fmt -check -recursive
terraform -chdir=infra validate
terraform -chdir=infra test
docker compose config --quiet
docker build --target runtime --tag acme-ops-engineer:validation .
```

The Terraform tests use a mocked AWS provider and plan-only runs for safe zero
counts, scaling, worker-first startup, optional notifications, rejection of
API-without-worker and rejection of the `latest` image tag.
They check wiring and safeguards, not actual AWS provisioning or connectivity.
Never pass live secret tfvars into offline tests. Docker checks need a running
Linux Docker daemon; an actual AWS deployment still requires authorized live
verification of OIDC, TLS/DNS, secret injection, grants, readiness and recovery.
