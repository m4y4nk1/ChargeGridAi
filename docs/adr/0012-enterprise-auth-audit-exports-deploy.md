# 0012: Organisations, roles, SSO, audit, versioned config, exports, and the deploy path

## Status
Accepted (Phase 11)

## Context
Section 14 asks for:

- RBAC with the roles viewer, planner, admin and org_admin;
- scenario and run visibility scoped to the organisation;
- audit logging for config changes, exports and ingestion triggers;
- licence-filtered exports;
- Keycloak/OIDC for enterprise sign-in;
- a staging → prod pipeline on AWS ap-south-1.

The MVP ran open (fastapi-users present but unused), and every earlier phase had to
keep working in local development without sign-in.

## Decision
1. **One principal resolver** (`app/core/auth.py`), tried in this order:
   - a local JWT (fastapi-users, or minted for agent sessions);
   - an OIDC access token, verified against the issuer's JWKS for RS256 signature,
     issuer, audience and expiry;
   - anonymous. Anonymous is allowed only when `AUTH_REQUIRED=false`, which the
     settings refuse outside dev.

   OIDC users are provisioned on first sign-in. Roles come from `realm_access.roles` and
   the org from an `org` claim. The identity provider owns their roles; we mirror them
   for audit.
2. **Access checks: one router-level guard** (`org_guard`) on every org-owned router
   (scenarios, runs, optimisations, grid, exports, agent sessions, twin runs).
   - Reads need viewer; writes need planner.
   - Any org-owned id in the path, or a `run_id` query parameter, must belong to the
     caller's org. Otherwise the answer is **404, not 403**, so existence doesn't leak.
   - List endpoints filter by org.
   - Platform-wide actions (config, ingestion) need admin in the default org.
   - Org admins manage their own org's members.
3. **Agents act as their owner.** The in-process API calls from agent tools carry a
   short-lived local token for the session's owner, so an agent can't reach more than
   its user can.
4. **Audit log.** An append-only `audit_log` table covers scenario creation, runs,
   what-ifs, phasing, confirmations, exports, config edits and rollbacks, org and
   member changes, ingestion triggers and twin runs.
5. **Versioned config.** Admin edits store a new version in `config_version`, and the
   latest version overrides the shipped file.
   - Every process reads overrides through a 30-second cache.
   - Edits are validated: YAML, unchanged `schema_version`, no top-level keys removed.
   - Rollback to any version, or to the shipped file.
   - Cached site economics carry a fingerprint of the tariff, cost and sizing configs
     and recompute when it changes.
6. **Exports.** GeoJSON, XLSX and PDF contain only derived outputs, plus an attribution
   sheet built from the run's evidence.
   - Restricted sources are named only as "contractual/restricted source; content not
     exported".
   - Google Solar content never leaves the Google map page.
7. **Deploy path.**
   - GitHub Actions builds multi-arch production images (non-root, read-only
     filesystem) and pushes them to ECR.
   - It Helm-deploys to staging and runs smoke tests, including an anonymous 401 check.
   - Prod follows after a GitHub environment approval.
   - AWS access is through GitHub OIDC roles scoped per environment.
   - Terraform defines VPC, EKS, RDS, ElastiCache, S3, ECR, KMS, Secrets Manager and
     IRSA.
   - A security workflow runs pip-audit, npm audit, bandit, gitleaks, Trivy and the
     security test suite.

## Consequences
- **Local development is unchanged.** Anonymous access acts as a planner in the
  default org.
- **The pen-test checklist** (docs/security/pentest-checklist.md) separates what is
  verified automatically from what needs an external test before production.
- **Terraform and the deploy workflow are validated but not applied.** Applying needs
  an AWS account, the state bucket, and the GitHub environments' variables.
