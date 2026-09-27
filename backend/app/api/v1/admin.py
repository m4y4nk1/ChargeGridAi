"""Administration (Section 11, 14): versioned config, audit log, org members,
ingestion triggers. Platform-wide actions (config, ingestion) need `admin` in the
default org (the platform operator) or a superuser; members are managed by the org's
`org_admin` or a platform admin. Every change is audited."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, audit, get_principal
from app.core.config_store import invalidate
from app.core.settings import get_settings
from app.db.models.org import ROLES, AuditLog, ConfigVersion, Org, RoleBinding
from app.db.models.user import User
from app.db.session import get_session

router = APIRouter(prefix="/admin", tags=["admin"])

INGEST_JOBS = {
    "osm-chargers": "ingest.osm_chargers",
    "ocm": "ingest.ocm",
    "ocpi": "ingest.ocpi",
    "calibrate": "ingest.calibrate",
    "demand": "demand.run",
}


def platform_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if not (principal.has("admin") and principal.org_slug == get_settings().default_org_slug):
        raise HTTPException(403, "Requires a platform admin (admin in the default org)")
    return principal


def _config_path(key: str) -> Path:
    root = get_settings().config_dir.resolve()
    path = (root / key).resolve()
    if path.suffix != ".yaml" or root not in path.parents or not path.is_file():
        raise HTTPException(404, "Unknown config key")
    return path


async def _versions(session: AsyncSession, key: str) -> list[ConfigVersion]:
    return list(
        (
            await session.execute(
                select(ConfigVersion)
                .where(ConfigVersion.key == key)
                .order_by(ConfigVersion.version.desc())
            )
        ).scalars()
    )


@router.get("/config")
async def list_config(
    session: AsyncSession = Depends(get_session), _: Principal = Depends(platform_admin)
) -> list[dict[str, Any]]:
    root = get_settings().config_dir.resolve()
    rows = (
        await session.execute(
            select(ConfigVersion.key, func.max(ConfigVersion.version)).group_by(ConfigVersion.key)
        )
    ).all()
    latest: dict[str, int] = {k: v for k, v in rows}
    return [
        {"key": str(p.relative_to(root)), "version": latest.get(str(p.relative_to(root)), 0)}
        for p in sorted(root.rglob("*.yaml"))
    ]


@router.get("/config/{key:path}")
async def get_config(
    key: str, session: AsyncSession = Depends(get_session), _: Principal = Depends(platform_admin)
) -> dict[str, Any]:
    path = _config_path(key)
    versions = await _versions(session, key)
    return {
        "key": key,
        "version": versions[0].version if versions else 0,
        "content": versions[0].content if versions else path.read_text(),
        "history": [
            {"version": v.version, "author": v.author_label, "at": v.at, "note": v.note}
            for v in versions
        ],
    }


class ConfigIn(BaseModel):
    content: str = Field(min_length=10, max_length=500_000)
    note: str | None = Field(default=None, max_length=500)


def _validate(new_text: str, current_text: str) -> None:
    try:
        new = yaml.safe_load(new_text)
    except yaml.YAMLError as exc:
        raise HTTPException(422, f"Not valid YAML: {exc}") from exc
    current = yaml.safe_load(current_text)
    if not isinstance(new, dict):
        raise HTTPException(422, "The config must be a YAML mapping")
    if isinstance(current, dict):
        if current.get("schema_version") != new.get("schema_version"):
            raise HTTPException(422, "schema_version can't change through the admin API")
        missing = sorted(set(current) - set(new))
        if missing:
            raise HTTPException(422, f"Top-level keys can't be removed: {missing}")


async def _store(
    session: AsyncSession,
    key: str,
    content: str,
    note: str | None,
    principal: Principal,
    request: Request,
    action: str,
) -> dict[str, Any]:
    versions = await _versions(session, key)
    current = versions[0].content if versions else _config_path(key).read_text()
    _validate(content, current)
    version = (versions[0].version if versions else 0) + 1
    session.add(
        ConfigVersion(
            key=key, version=version, content=content, note=note, author_label=principal.label
        )
    )
    await audit(
        session, principal, action, "config", key, {"version": version, "note": note}, request
    )
    await session.commit()
    invalidate()
    return {"key": key, "version": version}


@router.put("/config/{key:path}")
async def put_config(
    key: str,
    body: ConfigIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(platform_admin),
) -> dict[str, Any]:
    _config_path(key)
    return await _store(session, key, body.content, body.note, principal, request, "config.update")


class RollbackIn(BaseModel):
    version: int = Field(ge=0, description="0 = back to the file shipped with the code")


@router.post("/config/{key:path}/rollback")
async def rollback_config(
    key: str,
    body: RollbackIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(platform_admin),
) -> dict[str, Any]:
    path = _config_path(key)
    if body.version == 0:
        content = path.read_text()
    else:
        match = next((v for v in await _versions(session, key) if v.version == body.version), None)
        if match is None:
            raise HTTPException(404, "No such version")
        content = match.content
    return await _store(
        session, key, content, f"rollback to v{body.version}", principal, request, "config.rollback"
    )


@router.get("/audit")
async def audit_log(
    action: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    """Platform admins see everything; an org's admins see their org's entries."""
    if not principal.has("admin"):
        raise HTTPException(403, "Requires the admin role")
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if principal.org_slug != get_settings().default_org_slug:
        q = q.where(AuditLog.org_id == principal.org_id)
    if action:
        q = q.where(AuditLog.action == action)
    return [
        {
            "id": a.id,
            "at": a.at,
            "actor": a.actor_label,
            "action": a.action,
            "target_type": a.target_type,
            "target_id": a.target_id,
            "payload": a.payload,
            "ip": a.ip,
        }
        for a in (await session.execute(q)).scalars()
    ]


class OrgIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,40}$")
    name: str = Field(min_length=2, max_length=200)


@router.get("/orgs")
async def list_orgs(
    session: AsyncSession = Depends(get_session), _: Principal = Depends(platform_admin)
) -> list[dict[str, Any]]:
    return [
        {"id": str(o.id), "slug": o.slug, "name": o.name}
        for o in (await session.execute(select(Org).order_by(Org.slug))).scalars()
    ]


@router.post("/orgs", status_code=201)
async def create_org(
    body: OrgIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(platform_admin),
) -> dict[str, Any]:
    if (await session.execute(select(Org).where(Org.slug == body.slug))).scalar_one_or_none():
        raise HTTPException(409, "An organisation with that slug exists")
    org = Org(slug=body.slug, name=body.name)
    session.add(org)
    await session.flush()
    await audit(session, principal, "org.create", "org", body.slug, {"name": body.name}, request)
    await session.commit()
    return {"id": str(org.id), "slug": org.slug, "name": org.name}


async def _org_for_member_admin(session: AsyncSession, slug: str, principal: Principal) -> Org:
    org = (await session.execute(select(Org).where(Org.slug == slug))).scalar_one_or_none()
    if org is None:
        raise HTTPException(404, "Not found")
    platform = principal.has("admin") and principal.org_slug == get_settings().default_org_slug
    own = principal.org_id == org.id and "org_admin" in principal.roles
    if not (platform or own):
        raise HTTPException(404 if principal.org_id != org.id else 403, "Not allowed")
    return org


@router.get("/orgs/{slug}/members")
async def list_members(
    slug: str,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[dict[str, Any]]:
    org = await _org_for_member_admin(session, slug, principal)
    rows = (
        await session.execute(
            select(User, RoleBinding.roles)
            .join(RoleBinding, RoleBinding.user_id == User.id)
            .where(RoleBinding.org_id == org.id)
        )
    ).all()
    return sorted(
        ({"email": u.email, "roles": sorted(r)} for u, r in rows), key=lambda m: m["email"]
    )


class MemberIn(BaseModel):
    email: EmailStr
    roles: list[str] = Field(description="Empty removes the member")


@router.put("/orgs/{slug}/members")
async def set_member(
    slug: str,
    body: MemberIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    org = await _org_for_member_admin(session, slug, principal)
    unknown = sorted(set(body.roles) - set(ROLES))
    if unknown:
        raise HTTPException(422, f"Unknown roles {unknown}; use {list(ROLES)}")
    user = (
        await session.execute(select(User).where(func.lower(User.email) == body.email.lower()))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(404, "No user with that email; they must sign in or register first")
    if (
        user.id == principal.user_id
        and "org_admin" not in body.roles
        and "org_admin" in principal.roles
        and principal.org_id == org.id
    ):
        raise HTTPException(409, "You can't remove your own org_admin role")
    binding = await session.get(RoleBinding, (user.id, org.id))
    if not body.roles:
        if binding is not None:
            await session.delete(binding)
    elif binding is None:
        session.add(RoleBinding(user_id=user.id, org_id=org.id, roles=sorted(set(body.roles))))
    else:
        binding.roles = sorted(set(body.roles))
    await audit(
        session,
        principal,
        "org.member",
        "org",
        slug,
        {"email": body.email, "roles": sorted(set(body.roles))},
        request,
    )
    await session.commit()
    return {"email": body.email, "org": slug, "roles": sorted(set(body.roles))}


class IngestIn(BaseModel):
    args: list[str] = Field(default_factory=list, max_length=3)


@router.post("/ingest/{job}", status_code=202)
async def trigger_ingest(
    job: str,
    body: IngestIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(platform_admin),
) -> dict[str, Any]:
    from app.workers.celery_app import celery_app

    task = INGEST_JOBS.get(job)
    if task is None:
        raise HTTPException(404, f"Unknown job; use {sorted(INGEST_JOBS)}")
    if job == "ocpi" and len(body.args) != 1:
        raise HTTPException(422, "ocpi needs the operator id as the only argument")
    args: list[Any] = [float(body.args[0])] if job == "demand" and body.args else list(body.args)
    result = celery_app.send_task(task, args=args)
    await audit(
        session,
        principal,
        "ingest.trigger",
        "job",
        job,
        {"args": body.args, "task_id": result.id},
        request,
    )
    await session.commit()
    return {"job": job, "task_id": result.id}
