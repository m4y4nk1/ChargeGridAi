output "cluster_name" { value = module.eks.cluster_name }
output "db_endpoint" { value = aws_db_instance.pg.address }
output "db_master_secret_arn" { value = aws_db_instance.pg.master_user_secret[0].secret_arn }
output "redis_endpoint" { value = aws_elasticache_replication_group.redis.primary_endpoint_address }
output "raw_bucket" { value = aws_s3_bucket.raw.bucket }
output "ecr_repositories" { value = { for k, r in aws_ecr_repository.repo : k => r.repository_url } }
output "backend_role_arn" { value = module.backend_irsa.iam_role_arn }
output "deploy_role_arn" { value = aws_iam_role.deploy.arn }
output "app_secret_name" { value = aws_secretsmanager_secret.app.name }
