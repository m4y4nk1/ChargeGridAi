# Terraform (AWS ap-south-1)

`modules/platform` builds one environment:

- **Network:** a VPC across 3 AZs.
- **Kubernetes:** EKS with Graviton nodes and IRSA.
- **Database:** RDS PostgreSQL 16, encrypted, with TLS forced and a managed master
  password. The first migration creates PostGIS, h3 and pgvector.
- **Cache:** ElastiCache Redis with TLS.
- **Storage:** a versioned, KMS-encrypted S3 raw lake, and immutable ECR repositories
  with scan on push.
- **Secrets:** a Secrets Manager secret for app settings.
- **Access:** IRSA roles for the backend and External Secrets, and a GitHub OIDC deploy
  role bound to the matching GitHub environment.

`envs/staging` and `envs/prod` instantiate it. Prod gets a multi-AZ database, more
nodes and deletion protection.

```
cd infra/terraform/envs/staging
terraform init
terraform plan -var github_repo=<owner>/<repo>
```

**Not applied from this repository.** Applying needs an AWS account, the state bucket
and lock table (created once by hand), and the GitHub OIDC provider in that account.

Valhalla tiles are built by a one-off job onto the `chargegrid-valhalla-tiles` volume.

CI runs `terraform fmt -check` and `terraform validate` on every change.
