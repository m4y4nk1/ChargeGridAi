from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, get_principal, mint_local_token
from app.core.security import auth_backend, fastapi_users
from app.core.settings import get_settings
from app.db.models.org import Org, RoleBinding
from app.db.session import get_session
from app.schemas.user import UserCreate, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])

router.include_router(fastapi_users.get_auth_router(auth_backend))
router.include_router(fastapi_users.get_register_router(UserRead, UserCreate))


@router.get("/config")
async def auth_config() -> dict[str, Any]:
    """What the web app needs to sign in: whether auth is required, and the OIDC
    issuer/client for the PKCE flow when single sign-on is configured."""
    s = get_settings()
    return {
        "required": s.auth_required,
        "local": True,
        "oidc": {"issuer": s.oidc_issuer, "client_id": s.oidc_client_id}
        if s.oidc_issuer and s.oidc_client_id
        else None,
    }


@router.post("/refresh")
async def refresh(principal: Principal = Depends(get_principal)) -> dict[str, str]:
    """Re-issue a local token for a signed-in local user (tokens are stateless)."""
    if principal.user_id is None or principal.method != "local":
        return {"detail": "Only local sessions can be refreshed here"}
    return {"access_token": mint_local_token(principal.user_id), "token_type": "bearer"}


me_router = APIRouter(tags=["auth"])


@me_router.get("/me")
async def me(
    principal: Principal = Depends(get_principal), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    orgs = []
    if principal.user_id is not None:
        orgs = [
            {"slug": o.slug, "name": o.name, "roles": sorted(r)}
            for o, r in (
                await session.execute(
                    select(Org, RoleBinding.roles)
                    .join(RoleBinding, RoleBinding.org_id == Org.id)
                    .where(RoleBinding.user_id == principal.user_id)
                )
            ).all()
        ]
    return {
        "user_id": str(principal.user_id) if principal.user_id else None,
        "label": principal.label,
        "method": principal.method,
        "org": principal.org_slug,
        "roles": sorted(principal.roles),
        "orgs": orgs,
        "auth_required": get_settings().auth_required,
    }
