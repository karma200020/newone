resource "aws_ecr_repository" "app" {
  name                 = var.name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_cloudwatch_log_group" "task" {
  for_each          = toset(["api", "worker", "bootstrap"])
  name              = "/ecs/${var.name}/${each.key}"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "rds" {
  for_each          = toset(["postgresql", "upgrade"])
  name              = "/aws/rds/instance/${var.name}/${each.key}"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "insights" {
  name              = "/aws/ecs/containerinsights/${var.name}/performance"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_cluster" "main" {
  name = var.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
  depends_on = [aws_cloudwatch_log_group.insights]
}

locals {
  services = {
    api = {
      command = ["python", "-m", "ops.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
      count   = var.desired_count
      health  = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3).close()"]
    }
    worker = {
      command = ["python", "-m", "ops.cli", "worker"]
      count   = coalesce(var.worker_desired_count, var.desired_count)
      health  = ["CMD", "python", "-m", "ops.cli", "worker-health"]
    }
  }
  environment = [
    { name = "OPS_ENVIRONMENT", value = "production" },
    { name = "OPS_DEMO_MODE", value = "false" },
    { name = "OPS_AUTH_ISSUER", value = var.oidc_issuer },
    { name = "OPS_AUTH_AUDIENCE", value = var.oidc_audience },
    { name = "OPS_OIDC_JWKS_URL", value = var.oidc_jwks_url },
    { name = "OPS_TOOL_TRANSPORT", value = "local" },
    { name = "OPS_LLM_ENABLED", value = "false" }
  ]
  secrets = [
    for field in ["database_url", "signing_key", "redis_url"] : {
      name      = "OPS_${upper(field)}"
      valueFrom = "${var.app_secret_arn}:${field}::"
    }
  ]
}

resource "aws_ecs_task_definition" "app" {
  for_each                 = local.services
  family                   = "${var.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution[each.key].arn
  task_role_arn            = aws_iam_role.task[each.key].arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name                   = each.key
    image                  = var.image_uri
    essential              = true
    user                   = "10001:10001"
    readonlyRootFilesystem = true
    command                = each.value.command
    environment            = local.environment
    secrets                = local.secrets
    stopTimeout            = 120
    linuxParameters = {
      initProcessEnabled = true
      capabilities       = { drop = ["ALL"] }
    }
    portMappings = each.key == "api" ? [{ containerPort = 8000, protocol = "tcp" }] : []
    healthCheck = {
      command     = each.value.health
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.task[each.key].name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = each.key
        mode                  = "non-blocking"
        max-buffer-size       = "25m"
      }
    }
  }])
  lifecycle {
    precondition {
      condition = (
        startswith(var.image_uri, "${aws_ecr_repository.app.repository_url}@sha256:") ||
        startswith(var.image_uri, "${aws_ecr_repository.app.repository_url}:")
      )
      error_message = "image_uri must belong to the ECR repository managed by this deployment."
    }
  }
}

resource "aws_ecs_task_definition" "bootstrap" {
  family                   = "${var.name}-bootstrap"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution["bootstrap"].arn
  task_role_arn            = aws_iam_role.task["bootstrap"].arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name                   = "bootstrap"
    image                  = var.image_uri
    essential              = true
    user                   = "10001:10001"
    readonlyRootFilesystem = true
    command                = ["python", "-c", file("${path.module}/bootstrap.py")]
    environment = concat(local.environment, [
      { name = "DB_HOST", value = aws_db_instance.main.address },
      { name = "DB_NAME", value = aws_db_instance.main.db_name }
    ])
    secrets = concat(local.secrets, [
      { name = "DB_ADMIN_USERNAME", valueFrom = "${aws_db_instance.main.master_user_secret[0].secret_arn}:username::" },
      { name = "DB_ADMIN_PASSWORD", valueFrom = "${aws_db_instance.main.master_user_secret[0].secret_arn}:password::" }
    ])
    stopTimeout = 120
    linuxParameters = {
      initProcessEnabled = true
      capabilities       = { drop = ["ALL"] }
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.task["bootstrap"].name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "bootstrap"
      }
    }
  }])
}

resource "aws_ecs_service" "app" {
  for_each                           = local.services
  name                               = "${var.name}-${each.key}"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.app[each.key].arn
  desired_count                      = each.value.count
  launch_type                        = "FARGATE"
  platform_version                   = "1.4.0"
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = each.key == "api" ? 180 : null
  enable_execute_command             = false
  propagate_tags                     = "SERVICE"

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = aws_subnet.app[*].id
    security_groups  = [aws_security_group.task[each.key].id]
    assign_public_ip = false
  }

  dynamic "load_balancer" {
    for_each = each.key == "api" ? [1] : []
    content {
      target_group_arn = aws_lb_target_group.api.arn
      container_name   = "api"
      container_port   = 8000
    }
  }

  lifecycle {
    precondition {
      condition     = var.desired_count == 0 || coalesce(var.worker_desired_count, var.desired_count) > 0
      error_message = "Production API readiness requires at least one worker."
    }
  }

  depends_on = [
    aws_lb_listener.https,
    aws_iam_role_policy.execution,
    aws_route.nat,
    aws_route_table_association.app,
    aws_vpc_security_group_egress_rule.task_https,
    aws_vpc_security_group_egress_rule.task_db,
    aws_vpc_security_group_egress_rule.task_redis,
    aws_vpc_security_group_ingress_rule.db_task,
    aws_vpc_security_group_ingress_rule.redis_task,
    aws_elasticache_replication_group.main
  ]
}
