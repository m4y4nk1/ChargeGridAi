# ChargeGrid platform on AWS ap-south-1 (Section 4 Prod row, Section 14 data residency):
# VPC, EKS, RDS PostgreSQL 16, ElastiCache Redis, S3 raw lake, ECR, Secrets Manager,
# IRSA roles for the backend and External Secrets, and a GitHub OIDC deploy role.
# One module, instantiated per environment (envs/staging, envs/prod).

locals {
  name = "chargegrid-${var.env}"
  tags = { Project = "chargegrid", Environment = var.env, ManagedBy = "terraform" }
  azs  = slice(data.aws_availability_zones.this.names, 0, 3)
  prod = var.env == "prod"
}

data "aws_availability_zones" "this" { state = "available" }
data "aws_caller_identity" "this" {}

# --- network ---------------------------------------------------------------------------

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.13"

  name             = local.name
  cidr             = var.vpc_cidr
  azs              = local.azs
  private_subnets  = [for i, _ in local.azs : cidrsubnet(var.vpc_cidr, 4, i)]
  public_subnets   = [for i, _ in local.azs : cidrsubnet(var.vpc_cidr, 8, 200 + i)]
  database_subnets = [for i, _ in local.azs : cidrsubnet(var.vpc_cidr, 8, 210 + i)]

  enable_nat_gateway           = true
  single_nat_gateway           = !local.prod
  enable_dns_hostnames         = true
  create_database_subnet_group = true

  public_subnet_tags  = { "kubernetes.io/role/elb" = 1 }
  private_subnet_tags = { "kubernetes.io/role/internal-elb" = 1 }
  tags                = local.tags
}

# --- Kubernetes ------------------------------------------------------------------------

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.24"

  cluster_name                             = local.name
  cluster_version                          = var.eks_version
  vpc_id                                   = module.vpc.vpc_id
  subnet_ids                               = module.vpc.private_subnets
  cluster_endpoint_public_access           = true
  enable_irsa                              = true
  enable_cluster_creator_admin_permissions = true
  cluster_enabled_log_types                = ["api", "audit", "authenticator"]

  eks_managed_node_groups = {
    default = {
      ami_type       = "AL2023_ARM_64_STANDARD"
      instance_types = var.node_instance_types
      min_size       = var.node_min
      max_size       = var.node_max
      desired_size   = var.node_min
    }
  }
  tags = local.tags
}

# --- encryption ------------------------------------------------------------------------

resource "aws_kms_key" "data" {
  description         = "${local.name} data at rest (RDS, S3, secrets)"
  enable_key_rotation = true
  tags                = local.tags
}

# --- database --------------------------------------------------------------------------

resource "aws_security_group" "db" {
  name   = "${local.name}-db"
  vpc_id = module.vpc.vpc_id
  ingress {
    description     = "PostgreSQL from EKS nodes"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }
  tags = local.tags
}

resource "aws_db_parameter_group" "pg" {
  name   = "${local.name}-pg16"
  family = "postgres16"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
  tags = local.tags
}

resource "aws_db_instance" "pg" {
  identifier                   = local.name
  engine                       = "postgres"
  engine_version               = "16.4"
  instance_class               = var.db_instance_class
  allocated_storage            = var.db_allocated_gb
  max_allocated_storage        = var.db_allocated_gb * 3
  storage_type                 = "gp3"
  storage_encrypted            = true
  kms_key_id                   = aws_kms_key.data.arn
  db_name                      = "chargegrid"
  username                     = "chargegrid"
  manage_master_user_password  = true # rotated, stored in Secrets Manager
  db_subnet_group_name         = module.vpc.database_subnet_group_name
  vpc_security_group_ids       = [aws_security_group.db.id]
  parameter_group_name         = aws_db_parameter_group.pg.name
  multi_az                     = var.db_multi_az
  backup_retention_period      = local.prod ? 14 : 3
  deletion_protection          = local.prod
  skip_final_snapshot          = !local.prod
  final_snapshot_identifier    = local.prod ? "${local.name}-final" : null
  performance_insights_enabled = true
  auto_minor_version_upgrade   = true
  tags                         = local.tags
  # The first Alembic migration creates postgis, h3, h3_postgis, vector and pg_trgm.
  # Confirm h3-pg is on the RDS extension list for the chosen minor version before the
  # first apply (docs/verification.md).
}

# --- cache (Celery broker) -------------------------------------------------------------

resource "aws_security_group" "redis" {
  name   = "${local.name}-redis"
  vpc_id = module.vpc.vpc_id
  ingress {
    description     = "Redis from EKS nodes"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }
  tags = local.tags
}

resource "aws_elasticache_subnet_group" "redis" {
  name       = "${local.name}-redis"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id       = local.name
  description                = "Celery broker and results"
  engine                     = "redis"
  engine_version             = "7.1"
  node_type                  = var.redis_node_type
  num_cache_clusters         = local.prod ? 2 : 1
  automatic_failover_enabled = local.prod
  subnet_group_name          = aws_elasticache_subnet_group.redis.name
  security_group_ids         = [aws_security_group.redis.id]
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  tags                       = local.tags
}

# --- storage ---------------------------------------------------------------------------

resource "aws_s3_bucket" "raw" {
  bucket = "${local.name}-raw-${data.aws_caller_identity.this.account_id}"
  tags   = local.tags
}

resource "aws_s3_bucket_versioning" "raw" {
  bucket = aws_s3_bucket.raw.id
  versioning_configuration { status = "Enabled" } # snapshots are immutable (Section 8)
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }
  }
}

resource "aws_s3_bucket_public_access_block" "raw" {
  bucket                  = aws_s3_bucket.raw.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_ecr_repository" "repo" {
  for_each             = toset(["chargegrid-backend", "chargegrid-frontend"])
  name                 = each.key
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "KMS" }
  tags = local.tags
}

# --- secrets ---------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "app" {
  name       = "chargegrid/${var.env}"
  kms_key_id = aws_kms_key.data.arn
  tags       = local.tags
  # Values (JWT_SECRET, API keys, OIDC issuer, DATABASE_URL, REDIS_URL, S3_BUCKET) are
  # put by an operator, never through Terraform state.
}

# --- IAM: backend pods (IRSA) ----------------------------------------------------------

data "aws_iam_policy_document" "backend" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.raw.arn, "${aws_s3_bucket.raw.arn}/*"]
  }
  statement {
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.data.arn]
  }
}

resource "aws_iam_policy" "backend" {
  name   = "${local.name}-backend"
  policy = data.aws_iam_policy_document.backend.json
}

module "backend_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.46"

  role_name = "${local.name}-backend"
  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["chargegrid:chargegrid"]
    }
  }
  role_policy_arns = { backend = aws_iam_policy.backend.arn }
  tags             = local.tags
}

module "external_secrets_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.46"

  role_name                             = "${local.name}-external-secrets"
  attach_external_secrets_policy        = true
  external_secrets_secrets_manager_arns = [aws_secretsmanager_secret.app.arn]
  external_secrets_kms_key_arns         = [aws_kms_key.data.arn]
  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["external-secrets:external-secrets"]
    }
  }
  tags = local.tags
}

# --- IAM: GitHub Actions deploys (no long-lived keys) ----------------------------------

data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "deploy_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      # Only jobs running in the matching GitHub environment (prod needs approval).
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:environment:${var.env}"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name               = "${local.name}-deploy"
  assume_role_policy = data.aws_iam_policy_document.deploy_trust.json
  tags               = local.tags
}

data "aws_iam_policy_document" "deploy" {
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    actions = [
      "ecr:BatchCheckLayerAvailability", "ecr:CompleteLayerUpload", "ecr:InitiateLayerUpload",
      "ecr:PutImage", "ecr:UploadLayerPart", "ecr:BatchGetImage",
    ]
    resources = [for r in aws_ecr_repository.repo : r.arn]
  }
  statement {
    actions   = ["eks:DescribeCluster"]
    resources = [module.eks.cluster_arn]
  }
}

resource "aws_iam_role_policy" "deploy" {
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}

resource "aws_eks_access_entry" "deploy" {
  cluster_name  = module.eks.cluster_name
  principal_arn = aws_iam_role.deploy.arn
}

resource "aws_eks_access_policy_association" "deploy" {
  cluster_name  = module.eks.cluster_name
  principal_arn = aws_iam_role.deploy.arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSEditPolicy"
  access_scope {
    type       = "namespace"
    namespaces = ["chargegrid"]
  }
}
