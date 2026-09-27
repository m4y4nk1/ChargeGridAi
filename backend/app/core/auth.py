"""Who is calling, in which org, with which roles (Section 14).

A request's principal comes from, in order:
1. a local JWT (fastapi-users login, or minted for agent sessions' in-process calls);
2. an OIDC access token (Keycloak), verified against the issuer's JWKS: issuer,
   audience, expiry and RS256 signature; users are provisioned on first sign-in, their
   roles read from `oidc_roles_claim` and org from `oidc_org_claim`;
3. no token: an anonymous member of the default org with `anonymous_role`, only while
   AUTH_REQUIRED is false (dev). Outside dev it is always required.

Roles: viewer < planner < admin; org_admin manages members of its org and implies
admin there. Every org-owned resource (scenario, run, optimisation, agent session) is
visible only inside its org; the guard answers 404 for another org's ids so their
existence doesn't leak.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.db.models.org import ROLES, AuditLog, Org, RoleBinding
from app.db.models.user import User
from app.db.session import get_session

RANK = {"viewer": 1, "planner": 2, "admin": 3, "org_admin": 3}
LOCAL_AUDIENCE = ["fastapi-users:auth"]


@dataclass
class Principal:
    user_id: uuid.UUID | None
    label: str
    org_id: uuid.UUID
    org_slug: str
    roles: set[str] = field(default_factory=set)
    method: str = "anonymous"  # local | oidc | anonymous

    @property
    def rank(self) -> int:
        return max((RANK.get(r, 0) for r in self.roles), default=0)

    def has(self, role: str) -> bool:
        return self.rank >= RANK[role]


def mint_local_token(user_id: uuid.UUID, minutes: int = 60) -> str:
    """A local JWT for `user_id` (same format as fastapi-users login tokens)."""
    now = datetime.now(UTC)
    payload = {"sub": str(user_id), "aud": LOCAL_AUDIENCE, "exp": now + timedelta(minutes=minutes)}
    return jwt.encode(payload, get_settings().jwt_secret, algorithm="HS256")


@lru_cache(maxsize=4)
def _jwks(issuer: str) -> jwt.PyJWKClient:
    """JWKS client from the issuer's OIDC discovery document."""
    import httpx

    discovery = httpx.get(f"{issuer.rstrip('/')}/.well-known/openid-configuration", timeout=10)
    discovery.raise_for_status()
    return jwt.PyJWKClient(discovery.json()["jwks_uri"], cache_keys=True, lifespan=3600)


@lru_cache(maxsize=4)
def _jwks_direct(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)


def _claim(claims: dict[str, Any], path: str) -> Any:
    node: Any = claims
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def verify_oidc(token: str, jwks_client: Any | None = None) -> dict[str, Any]:
    s = get_settings()
    if not s.oidc_issuer:
        raise HTTPException(401, "Invalid token")
    client = jwks_client or (
        _jwks_direct(s.oidc_jwks_url) if s.oidc_jwks_url else _jwks(s.oidc_issuer)
    )
    try:
        key = client.get_signing_key_from_jwt(token).key
        return dict(
            jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=s.oidc_audience or None,
                issuer=s.oidc_issuer,
                options={"require": ["exp", "iss", "sub"], "verify_aud": bool(s.oidc_audience)},
            )
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "Invalid token") from exc


async def _default_org(session: AsyncSession) -> Org:
    slug = get_settings().default_org_slug
    org = (await session.execute(select(Org).where(Org.slug == slug))).scalar_one_or_none()
    if org is None:
        org = Org(slug=slug, name="Default organisation")
        session.add(org)
        await session.flush()
    return org


async def _bindings(session: AsyncSession, user_id: uuid.UUID) -> list[tuple[Org, list[str]]]:
    rows = (
        await session.execute(
            select(Org, RoleBinding.roles)
            .join(RoleBinding, RoleBinding.org_id == Org.id)
            .where(RoleBinding.user_id == user_id)
            .order_by(Org.slug)
        )
    ).all()
    return [(o, list(r)) for o, r in rows]


async def _local_principal(session: AsyncSession, token: str, org_hint: str | None) -> Principal:
    try:
        claims = jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=["HS256"],
            audience=LOCAL_AUDIENCE,
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "Invalid token") from exc
    user = await session.get(User, uuid.UUID(claims["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(401, "Invalid token")
    bindings = await _bindings(session, user.id)
    if user.is_superuser:
        org = await _default_org(session)
        chosen = next(((o, r) for o, r in bindings if o.slug == org_hint), (org, ["admin"]))
        return Principal(
            user.id, user.email, chosen[0].id, chosen[0].slug, set(chosen[1]) | {"admin"}, "local"
        )
    if not bindings:
        raise HTTPException(403, "Your account has no role in any organisation")
    org, roles = next(((o, r) for o, r in bindings if o.slug == org_hint), bindings[0])
    return Principal(user.id, user.email, org.id, org.slug, set(roles), "local")


async def _oidc_principal(session: AsyncSession, token: str) -> Principal:
    s = get_settings()
    claims = verify_oidc(token)
    roles = {r for r in (_claim(claims, s.oidc_roles_claim) or []) if r in ROLES}
    if not roles:
        raise HTTPException(403, "Your account has no ChargeGrid role")
    org_slug = _claim(claims, s.oidc_org_claim) or s.default_org_slug
    if isinstance(org_slug, list):
        org_slug = org_slug[0] if org_slug else s.default_org_slug
    org = (await session.execute(select(Org).where(Org.slug == str(org_slug)))).scalar_one_or_none()
    if org is None:
        raise HTTPException(403, f"Organisation '{org_slug}' is not set up")
    user = (
        await session.execute(select(User).where(User.oidc_subject == claims["sub"]))
    ).scalar_one_or_none()
    email = claims.get("email") or f"{claims['sub']}@oidc.invalid"
    if user is None:
        user = User(
            email=email,
            hashed_password="!oidc",
            is_active=True,
            is_verified=True,
            oidc_subject=claims["sub"],
            display_name=claims.get("name"),
        )
        session.add(user)
        await session.flush()
    # The identity provider owns OIDC users' roles; mirror them for auditing and
    # agent sessions' in-process calls.
    binding = await session.get(RoleBinding, (user.id, org.id))
    if binding is None:
        session.add(RoleBinding(user_id=user.id, org_id=org.id, roles=sorted(roles)))
    elif set(binding.roles) != roles:
        binding.roles = sorted(roles)
    await session.commit()
    return Principal(user.id, email, org.id, org.slug, roles, "oidc")


async def get_principal(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Principal:
    cached = getattr(request.state, "principal", None)
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    s = get_settings()
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    # EventSource can't send headers: the SSE streams take the token as a query parameter.
    if not token and request.url.path.endswith("/events"):
        token = request.query_params.get("access_token", "")
    org_hint = request.headers.get("x-org")
    if token:
        try:
            unverified = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise HTTPException(401, "Invalid token") from exc
        aud = unverified.get("aud")
        local = aud == LOCAL_AUDIENCE or aud == LOCAL_AUDIENCE[0]
        principal = (
            await _local_principal(session, token, org_hint)
            if local
            else await _oidc_principal(session, token)
        )
    elif s.auth_required:
        raise HTTPException(401, "Sign in required", headers={"WWW-Authenticate": "Bearer"})
    else:
        org = await _default_org(session)
        await session.commit()
        principal = Principal(None, "anonymous (dev)", org.id, org.slug, {s.anonymous_role})
    request.state.principal = principal
    return principal


def require(role: str) -> Any:
    async def dep(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has(role):
            raise HTTPException(403, f"Requires the {role} role")
        return principal

    return Depends(dep)


# --- org guard -----------------------------------------------------------------------------

_OWNER_SQL = {
    "scenario_id": "select org_id from scenario where id = :id",
    "run_id": "select s.org_id from planning_run r join scenario s on s.id = r.scenario_id "
    "where r.id = :id",
    "opt_id": "select s.org_id from optimisation o join planning_run r on r.id = o.run_id "
    "join scenario s on s.id = r.scenario_id where o.id = :id",
    "session_id": "select org_id from agent_session where id = :id",
}


async def org_guard(
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    """Router-level guard: reads need viewer, writes need planner, and any org-owned id
    in the path (or a run_id query parameter) must belong to the caller's org."""
    need = "viewer" if request.method in ("GET", "HEAD", "OPTIONS") else "planner"
    if not principal.has(need):
        raise HTTPException(403, f"Requires the {need} role")
    ids = dict(request.path_params)
    if "run_id" in request.query_params:
        ids.setdefault("run_id", request.query_params["run_id"])
    for key, sql in _OWNER_SQL.items():
        if key not in ids:
            continue
        try:
            rid = uuid.UUID(str(ids[key]))
        except ValueError:
            continue  # the route's own validation answers 422
        row = (await session.execute(text(sql), {"id": rid})).first()
        # Legacy rows without an org belong to the default org.
        if row is not None and (row[0] or await _default_org_id(session)) != principal.org_id:
            raise HTTPException(404, "Not found")
    return principal


_DEFAULT_ORG_ID: tuple[float, uuid.UUID] | None = None


async def _default_org_id(session: AsyncSession) -> uuid.UUID:
    global _DEFAULT_ORG_ID
    if _DEFAULT_ORG_ID and time.monotonic() - _DEFAULT_ORG_ID[0] < 300:
        return _DEFAULT_ORG_ID[1]
    org = await _default_org(session)
    _DEFAULT_ORG_ID = (time.monotonic(), org.id)
    return org.id


# --- audit ---------------------------------------------------------------------------------


async def audit(
    session: AsyncSession,
    principal: Principal | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    payload: dict[str, Any] | None = None,
    request: Request | None = None,
    actor_label: str | None = None,
) -> None:
    """Append an audit record (config changes, exports, ingestion triggers, runs,
    confirmations, role changes). Committed with the caller's transaction."""
    session.add(
        AuditLog(
            actor_id=principal.user_id if principal else None,
            actor_label=actor_label or (principal.label if principal else "system"),
            org_id=principal.org_id if principal else None,
            action=action,
            target_type=target_type,
            target_id=target_id,
            payload=payload or {},
            ip=request.client.host if request and request.client else None,
        )
    )
