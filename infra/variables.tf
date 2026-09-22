variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "name" {
  type    = string
  default = "acme-ops"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,18}[a-z0-9]$", var.name)) && !strcontains(var.name, "--")
    error_message = "Use 3-20 lowercase letters/digits/single hyphens, starting with a letter and ending with a letter or digit."
  }
}

variable "vpc_cidr" {
  type    = string
  default = "10.42.0.0/16"
  validation {
    condition     = can(cidrnetmask(var.vpc_cidr)) && can(regex("/(16|17|18|19)$", var.vpc_cidr))
    error_message = "Supply an IPv4 /16 through /19 CIDR so subnets have room for ALB nodes."
  }
}

variable "certificate_arn" {
  type        = string
  description = "Issued ACM certificate in the ALB's region, covering the public DNS name."
  validation {
    condition     = can(regex("^arn:aws[a-z-]*:acm:", var.certificate_arn))
    error_message = "An ACM certificate ARN is required."
  }
}

variable "image_uri" {
  type        = string
  description = "Image in this deployment's immutable ECR repository; digest recommended. The first apply may name a not-yet-pushed unique release tag while counts are zero."
  validation {
    condition = can(regex(
      "^[0-9]{12}\\.dkr\\.ecr\\.[a-z0-9-]+\\.amazonaws\\.com(\\.cn)?/[a-z0-9/_-]+(@sha256:[a-f0-9]{64}|:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})$",
      var.image_uri
    )) && !endswith(var.image_uri, ":latest")
    error_message = "Use an ECR sha256 digest or unique immutable release tag, never latest."
  }
}

variable "app_secret_arn" {
  type        = string
  description = "Precreated Secrets Manager secret in this account/region with database_url, signing_key and redis_url JSON keys. Terraform never reads its value."
  validation {
    condition     = can(regex("^arn:aws[a-z-]*:secretsmanager:", var.app_secret_arn))
    error_message = "Supply the precreated app secret ARN."
  }
}

variable "app_secret_kms_key_arn" {
  type        = string
  default     = null
  description = "Customer KMS key ARN for the app secret, if not using aws/secretsmanager."
}

variable "oidc_issuer" {
  type = string
  validation {
    condition     = startswith(var.oidc_issuer, "https://")
    error_message = "Use the trusted HTTPS OIDC issuer."
  }
}

variable "oidc_audience" {
  type = string
  validation {
    condition     = length(trimspace(var.oidc_audience)) > 0
    error_message = "OIDC audience cannot be empty."
  }
}

variable "oidc_jwks_url" {
  type = string
  validation {
    condition     = startswith(var.oidc_jwks_url, "https://")
    error_message = "Production requires an HTTPS JWKS endpoint."
  }
}

variable "redis_auth_token" {
  type        = string
  sensitive   = true
  description = "32-128 random alphanumeric characters. Passed privately via TF_VAR_redis_auth_token; stored in Terraform state."
  validation {
    condition     = can(regex("^[A-Za-z0-9]{32,128}$", var.redis_auth_token))
    error_message = "Use 32-128 random alphanumeric characters (also URL-safe)."
  }
}

variable "desired_count" {
  type        = number
  default     = 0
  description = "API count and default worker count. Keep zero until secrets and schema bootstrap succeed."
  validation {
    condition     = var.desired_count >= 0 && floor(var.desired_count) == var.desired_count
    error_message = "desired_count must be a nonnegative integer."
  }
}

variable "worker_desired_count" {
  type        = number
  default     = null
  description = "Optional independent worker count; null follows desired_count."
  validation {
    condition     = var.worker_desired_count == null ? true : var.worker_desired_count >= 0 && floor(var.worker_desired_count) == var.worker_desired_count
    error_message = "worker_desired_count must be null or a nonnegative integer."
  }
}

variable "task_cpu" {
  type    = number
  default = 512
  validation {
    condition     = contains([256, 512, 1024, 2048, 4096], var.task_cpu)
    error_message = "Use a supported Fargate CPU size from 256 through 4096."
  }
}

variable "task_memory" {
  type    = number
  default = 1024
  validation {
    condition = (
      var.task_cpu == 256 ? contains([512, 1024, 2048], var.task_memory) :
      var.task_cpu == 512 ? contains([1024, 2048, 3072, 4096], var.task_memory) :
      var.task_cpu == 1024 ? contains(range(2048, 8193, 1024), var.task_memory) :
      var.task_cpu == 2048 ? contains(range(4096, 16385, 1024), var.task_memory) :
      contains(range(8192, 30721, 1024), var.task_memory)
    )
    error_message = "task_memory must be a valid Fargate memory size for task_cpu."
  }
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.small"
}

variable "db_engine_version" {
  type    = string
  default = "16.10"
  validation {
    condition     = can(regex("^16\\.[0-9]+$", var.db_engine_version))
    error_message = "This reference's parameter group and bootstrap require PostgreSQL 16."
  }
}

variable "db_multi_az" {
  type        = bool
  default     = true
  description = "Keep true for production; false is only for explicitly accepted nonproduction cost reduction."
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.small"
}

variable "deletion_protection" {
  type    = bool
  default = true
}

variable "final_snapshot_identifier" {
  type        = string
  default     = null
  description = "Unique RDS final snapshot name at teardown; null uses <name>-final. Never reuse an existing name."
}

variable "log_retention_days" {
  type    = number
  default = 30
  validation {
    condition     = contains([7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.log_retention_days)
    error_message = "Use an explicitly bounded CloudWatch retention period."
  }
}

variable "alarm_email" {
  type        = string
  default     = null
  description = "Optional SNS email subscriber. The recipient must confirm the subscription."
}
