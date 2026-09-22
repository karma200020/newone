output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "alb_dns_name" {
  value = aws_lb.main.dns_name
}

output "alb_zone_id" {
  value = aws_lb.main.zone_id
}

output "database_host" {
  value = aws_db_instance.main.address
}

output "redis_host" {
  value = aws_elasticache_replication_group.main.primary_endpoint_address
}

output "rds_master_secret_arn" {
  description = "Identifier only. Restricted bootstrap execution role, never the application, may read this secret."
  value       = aws_db_instance.main.master_user_secret[0].secret_arn
}

output "cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "service_names" {
  value = { for key, service in aws_ecs_service.app : key => service.name }
}

output "task_definition_arns" {
  value = { for key, task in aws_ecs_task_definition.app : key => task.arn }
}

output "bootstrap_task_definition_arn" {
  value = aws_ecs_task_definition.bootstrap.arn
}

output "bootstrap_network_configuration" {
  value = {
    awsvpcConfiguration = {
      subnets        = aws_subnet.app[*].id
      securityGroups = [aws_security_group.task["bootstrap"].id]
      assignPublicIp = "DISABLED"
    }
  }
}

output "log_groups" {
  value = { for key, group in aws_cloudwatch_log_group.task : key => group.name }
}
