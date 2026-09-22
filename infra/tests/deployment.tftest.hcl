# Native Terraform mocks: no AWS credentials, network calls or remote resources.
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "111122223333" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws", dns_suffix = "amazonaws.com" }
  }
  mock_data "aws_availability_zones" {
    defaults = { names = ["us-east-1a", "us-east-1b", "us-east-1c"] }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_resource "aws_ecr_repository" {
    defaults = {
      arn            = "arn:aws:ecr:us-east-1:111122223333:repository/acme-ops"
      repository_url = "111122223333.dkr.ecr.us-east-1.amazonaws.com/acme-ops"
    }
  }
  mock_resource "aws_db_instance" {
    defaults = {
      address = "database.example.internal"
      master_user_secret = [{
        secret_arn    = "arn:aws:secretsmanager:us-east-1:111122223333:secret:master-EXAMPLE"
        kms_key_id    = "arn:aws:kms:us-east-1:111122223333:key/00000000-0000-0000-0000-000000000000"
        secret_status = "active"
      }]
    }
  }
  mock_resource "aws_elasticache_replication_group" {
    defaults = {
      member_clusters          = ["acme-ops-001", "acme-ops-002"]
      primary_endpoint_address = "redis.example.internal"
    }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::111122223333:role/mock" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:us-east-1:111122223333:log-group:mock" }
  }
  mock_resource "aws_lb" {
    defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:111122223333:loadbalancer/app/acme-ops/0000000000000000" }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:111122223333:targetgroup/acme-ops-api/0000000000000000" }
  }
  mock_resource "aws_ecs_cluster" {
    defaults = { id = "arn:aws:ecs:us-east-1:111122223333:cluster/acme-ops" }
  }
  mock_resource "aws_ecs_task_definition" {
    defaults = { arn = "arn:aws:ecs:us-east-1:111122223333:task-definition/acme-ops:1" }
  }
  mock_resource "aws_sns_topic" {
    defaults = { arn = "arn:aws:sns:us-east-1:111122223333:acme-ops-alarms" }
  }
}

variables {
  certificate_arn = "arn:aws:acm:us-east-1:111122223333:certificate/00000000-0000-0000-0000-000000000000"
  app_secret_arn  = "arn:aws:secretsmanager:us-east-1:111122223333:secret:app-EXAMPLE"
  image_uri       = "111122223333.dkr.ecr.us-east-1.amazonaws.com/acme-ops:reference-test"
  oidc_issuer     = "https://identity.example.com/"
  oidc_audience   = "acme-ops-api"
  oidc_jwks_url   = "https://identity.example.com/jwks"
  # Deliberately public synthetic test input; never usable against a real cache.
  redis_auth_token = "00000000000000000000000000000000"
}

run "safe_provision_defaults" {
  command = plan
  assert {
    condition     = alltrue([for service in aws_ecs_service.app : service.desired_count == 0])
    error_message = "No service may start before explicit bootstrap."
  }
  assert {
    condition     = length(aws_subnet.public) == 2 && length(aws_subnet.app) == 2 && length(aws_subnet.data) == 2 && length(aws_nat_gateway.main) == 2
    error_message = "Use two AZs with isolated data and per-AZ NAT."
  }
  assert {
    condition     = aws_db_instance.main.storage_encrypted && aws_db_instance.main.multi_az && !aws_db_instance.main.publicly_accessible && aws_db_instance.main.manage_master_user_password
    error_message = "Database protection defaults must remain enabled."
  }
  assert {
    condition     = aws_elasticache_replication_group.main.transit_encryption_enabled && aws_elasticache_replication_group.main.at_rest_encryption_enabled && aws_elasticache_replication_group.main.automatic_failover_enabled
    error_message = "Redis must require encrypted transport/storage and support failover."
  }
  assert {
    condition     = aws_lb_target_group.api.health_check[0].path == "/health/ready"
    error_message = "ALB must gate on application readiness."
  }
  assert {
    condition     = alltrue([for service in aws_ecs_service.app : !service.network_configuration[0].assign_public_ip && service.deployment_circuit_breaker[0].rollback])
    error_message = "Services must stay private with rollback circuit breakers."
  }
  assert {
    condition = alltrue([
      for task in aws_ecs_task_definition.app :
      length(jsondecode(task.container_definitions)) == 1 &&
      jsondecode(task.container_definitions)[0].user == "10001:10001" &&
      jsondecode(task.container_definitions)[0].readonlyRootFilesystem &&
      length(jsondecode(task.container_definitions)[0].secrets) == 3 &&
      contains(jsondecode(task.container_definitions)[0].environment, { name = "OPS_ENVIRONMENT", value = "production" }) &&
      contains(jsondecode(task.container_definitions)[0].environment, { name = "OPS_DEMO_MODE", value = "false" })
    ])
    error_message = "Services must use exactly one container, production settings, nonroot, read-only filesystems and injected secrets."
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.app["worker"].container_definitions)[0].healthCheck.command[4] == "worker-health"
    error_message = "Worker health must check its own hostname heartbeat."
  }
  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.db_task) == 3 && length(aws_vpc_security_group_ingress_rule.redis_task) == 2 && aws_vpc_security_group_ingress_rule.api_alb.from_port == 8000
    error_message = "Only explicit task dependencies and ALB ingress should be permitted."
  }
}

run "enable_services" {
  command = plan
  variables {
    desired_count = 2
  }
  assert {
    condition     = alltrue([for service in aws_ecs_service.app : service.desired_count == 2])
    error_message = "Both API and worker must scale explicitly."
  }
}

run "worker_first" {
  command = plan
  variables {
    desired_count        = 0
    worker_desired_count = 1
  }
  assert {
    condition     = aws_ecs_service.app["api"].desired_count == 0 && aws_ecs_service.app["worker"].desired_count == 1
    error_message = "Operators must be able to bring workers up before the API."
  }
}

run "reject_api_without_worker" {
  command = plan
  variables {
    desired_count        = 1
    worker_desired_count = 0
  }
  expect_failures = [aws_ecs_service.app]
}

run "optional_notifications" {
  command = plan
  variables {
    alarm_email   = "alerts@example.com"
    desired_count = 2
  }
  assert {
    condition     = length(aws_sns_topic.alarms) == 1 && length(aws_sns_topic_policy.alarm_publish) == 1 && aws_sns_topic_subscription.email[0].protocol == "email"
    error_message = "Opt-in notifications require a topic, restricted publish policy and subscription."
  }
}

run "reject_mutable_latest" {
  command = plan
  variables {
    image_uri = "111122223333.dkr.ecr.us-east-1.amazonaws.com/acme-ops:latest"
  }
  expect_failures = [var.image_uri]
}
