# Local Keycloak (dev realm)

`docker compose --profile enterprise up -d keycloak` starts Keycloak on
http://localhost:8081 with the `chargegrid` realm imported from `realm-chargegrid.json`:

- realm roles `viewer`, `planner`, `admin`, `org_admin`;
- public client `chargegrid-web` (authorization code + PKCE S256, redirect
  `http://localhost:5173/*`), whose access tokens carry an `org` claim and audience
  `chargegrid-api`;
- test users `admin`, `planner`, `viewer` (org `default`) and `partner`
  (org `partner-cpo`, org_admin). Their passwords are `<username>-dev-only`.

**Dev only.** The passwords, the admin console login (`admin` / `admin-dev-only`) and
`sslRequired: none` exist for local testing. Production uses a managed realm (see
infra/helm/chargegrid/values-prod.yaml).

Point the API at it in `.env`:

```
OIDC_ISSUER=http://localhost:8081/realms/chargegrid
OIDC_AUDIENCE=chargegrid-api
OIDC_CLIENT_ID=chargegrid-web
OIDC_JWKS_URL=http://keycloak:8080/realms/chargegrid/protocol/openid-connect/certs
```

The `partner-cpo` org must exist first (`POST /api/v1/admin/orgs`).
