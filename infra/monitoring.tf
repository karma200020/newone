resource "aws_sns_topic" "alarms" {
  count = var.alarm_email == null ? 0 : 1
  name  = "${var.name}-alarms"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email == null ? 0 : 1
  topic_arn = aws_sns_topic.alarms[0].arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

data "aws_iam_policy_document" "alarm_publish" {
  count = var.alarm_email == null ? 0 : 1
  statement {
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alarms[0].arn]
    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${data.aws_partition.current.partition}:cloudwatch:${var.aws_region}:${data.aws_caller_identity.current.account_id}:alarm:${var.name}-*"]
    }
  }
}

resource "aws_sns_topic_policy" "alarm_publish" {
  count  = var.alarm_email == null ? 0 : 1
  arn    = aws_sns_topic.alarms[0].arn
  policy = data.aws_iam_policy_document.alarm_publish[0].json
}

locals {
  alarm_actions = aws_sns_topic.alarms[*].arn
  alarms = {
    api-unhealthy = {
      namespace = "AWS/ApplicationELB", metric = "UnHealthyHostCount", statistic = "Maximum"
      threshold = 0, comparison = "GreaterThanThreshold"
      dimensions = {
        LoadBalancer = aws_lb.main.arn_suffix
        TargetGroup  = aws_lb_target_group.api.arn_suffix
      }
    }
    api-target-errors = {
      namespace = "AWS/ApplicationELB", metric = "HTTPCode_Target_5XX_Count", statistic = "Sum"
      threshold = 5, comparison = "GreaterThanThreshold"
      dimensions = {
        LoadBalancer = aws_lb.main.arn_suffix
        TargetGroup  = aws_lb_target_group.api.arn_suffix
      }
    }
    alb-errors = {
      namespace  = "AWS/ApplicationELB", metric = "HTTPCode_ELB_5XX_Count", statistic = "Sum"
      threshold  = 5, comparison = "GreaterThanThreshold"
      dimensions = { LoadBalancer = aws_lb.main.arn_suffix }
    }
    database-storage = {
      namespace  = "AWS/RDS", metric = "FreeStorageSpace", statistic = "Minimum"
      threshold  = 5368709120, comparison = "LessThanThreshold"
      dimensions = { DBInstanceIdentifier = aws_db_instance.main.identifier }
    }
    database-cpu = {
      namespace  = "AWS/RDS", metric = "CPUUtilization", statistic = "Average"
      threshold  = 80, comparison = "GreaterThanThreshold"
      dimensions = { DBInstanceIdentifier = aws_db_instance.main.identifier }
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "dependency" {
  for_each            = local.alarms
  alarm_name          = "${var.name}-${each.key}"
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  statistic           = each.value.statistic
  threshold           = each.value.threshold
  comparison_operator = each.value.comparison
  dimensions          = each.value.dimensions
  period              = 60
  evaluation_periods  = 3
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
  actions_enabled     = startswith(each.key, "database-") || var.desired_count > 0
}

resource "aws_cloudwatch_metric_alarm" "running" {
  for_each            = local.services
  alarm_name          = "${var.name}-${each.key}-task-shortfall"
  namespace           = "ECS/ContainerInsights"
  metric_name         = "RunningTaskCount"
  statistic           = "Minimum"
  threshold           = each.value.count
  comparison_operator = "LessThanThreshold"
  period              = 60
  evaluation_periods  = 3
  treat_missing_data  = "breaching"
  actions_enabled     = each.value.count > 0
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.app[each.key].name
  }
}

resource "aws_cloudwatch_metric_alarm" "redis_memory" {
  count               = 2
  alarm_name          = "${var.name}-redis-${count.index}-memory"
  namespace           = "AWS/ElastiCache"
  metric_name         = "DatabaseMemoryUsagePercentage"
  statistic           = "Maximum"
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  period              = 60
  evaluation_periods  = 3
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
  dimensions = {
    CacheClusterId = sort(tolist(aws_elasticache_replication_group.main.member_clusters))[count.index]
    CacheNodeId    = "0001"
  }
}
