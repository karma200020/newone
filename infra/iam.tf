data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${data.aws_partition.current.partition}:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"]
    }
  }
}

resource "aws_iam_role" "execution" {
  for_each           = toset(["api", "worker", "bootstrap"])
  name               = "${var.name}-${each.key}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role" "task" {
  for_each           = toset(["api", "worker", "bootstrap"])
  name               = "${var.name}-${each.key}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
  # Deliberately no permissions: runtime code cannot call AWS remediation APIs.
}

data "aws_iam_policy_document" "execution" {
  for_each = aws_iam_role.execution
  statement {
    sid       = "ECRAuthentication"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid       = "PullDeploymentImage"
    actions   = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"]
    resources = [aws_ecr_repository.app.arn]
  }
  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.task[each.key].arn}:*"]
  }
  statement {
    sid     = "InjectStartupSecrets"
    actions = ["secretsmanager:GetSecretValue"]
    resources = concat(
      [var.app_secret_arn],
      each.key == "bootstrap" ? [aws_db_instance.main.master_user_secret[0].secret_arn] : []
    )
  }
  dynamic "statement" {
    for_each = var.app_secret_kms_key_arn == null ? [] : [var.app_secret_kms_key_arn]
    content {
      sid       = "DecryptApplicationSecretOnly"
      actions   = ["kms:Decrypt"]
      resources = [statement.value]
      condition {
        test     = "StringEquals"
        variable = "kms:ViaService"
        values   = ["secretsmanager.${var.aws_region}.${data.aws_partition.current.dns_suffix}"]
      }
      condition {
        test     = "StringEquals"
        variable = "kms:EncryptionContext:SecretARN"
        values   = [var.app_secret_arn]
      }
    }
  }
}

resource "aws_iam_role_policy" "execution" {
  for_each = aws_iam_role.execution
  name     = "startup-only"
  role     = each.value.id
  policy   = data.aws_iam_policy_document.execution[each.key].json
}
