# Helm

`chargegrid/` deploys the API (with an Alembic migration init container), Celery worker
and beat, the Temporal worker, the web app, Martin and Valhalla, with an ingress,
autoscaling, a disruption budget, network policies and non-root, read-only containers.
Secrets are synced from AWS Secrets Manager by the External Secrets Operator. Temporal
itself runs from its official chart in the `temporal` namespace.

```
helm lint infra/helm/chargegrid --set image.tag=test
helm upgrade --install chargegrid infra/helm/chargegrid -n chargegrid \
  --set image.registry=<ecr> --set image.tag=<sha> \
  --set serviceAccount.roleArn=<terraform output backend_role_arn>          # staging
helm upgrade --install chargegrid infra/helm/chargegrid -n chargegrid \
  -f infra/helm/chargegrid/values-prod.yaml --set image.tag=<sha> ...        # prod
```

The pipeline (.github/workflows/deploy.yml) does this for staging on every merge to
main and for prod after approval.
