"""Security checks behind docs/security/pentest-checklist.md (OWASP ASVS L2 items).

Run against the real app with AUTH_REQUIRED on and the local database; they create
their own orgs, users and a scenario and delete them afterwards. Skipped without a
database.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
import pytest_asyncio
from sqlalchemy import delete, text

from app.core.auth import LOCAL_AUDIENCE, mint_local_token
from app.core.settings import get_settings
from app.db.models.org import Org, RoleBinding
from app.db.models.planning import Scenario
from app.db.models.user import User
from app.db.session import async_session_factory

# The app's pooled engine binds connections to an event loop: run the module on one.
pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest_asyncio.fixture(loop_scope="session")
async def world(monkeypatch: Any) -> AsyncIterator[dict[str, Any]]:
    try:
        async with async_session_factory() as db:
            await db.execute(text("select 1"))
    except Exception:
        pytest.skip("no database")
    monkeypatch.setattr(get_settings(), "auth_required", True)
    tag = uuid.uuid4().hex[:8]
    async with async_session_factory() as db:
        default = (await db.execute(text("select id from org where slug = 'default'"))).first()
        if default is None:
            db.add(Org(slug="default", name="Default organisation"))
            await db.flush()
        default_id = (await db.execute(text("select id from org where slug = 'default'"))).scalar()
        other = Org(slug=f"sec-{tag}", name="Security test org")
        db.add(other)
        await db.flush()
        users = {}
        for name, org_id, roles in (
            ("viewer", default_id, ["viewer"]),
            ("planner", default_id, ["planner"]),
            ("outsider", other.id, ["planner", "viewer", "org_admin"]),
        ):
            u = User(
                email=f"{name}-{tag}@sec.test",
                hashed_password="!",
                is_active=True,
                is_verified=True,
            )
            db.add(u)
            await db.flush()
            db.add(RoleBinding(user_id=u.id, org_id=org_id, roles=roles))
            users[name] = u.id
        scenario = Scenario(
            name=f"sec {tag}",
            region_id="pmr",
            target_year=2028,
            adoption_case="base",
            adoption_multiplier=1.0,
            charger_classes=["DC_60"],
            constraints={},
            org_id=default_id,
        )
        db.add(scenario)
        await db.commit()
        sid = scenario.id
    from app.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://sec/api/v1"
    ) as api:
        yield {"api": api, "users": users, "scenario": sid, "other_org": f"sec-{tag}"}
    async with async_session_factory() as db:
        await db.execute(delete(Scenario).where(Scenario.id == sid))
        await db.execute(delete(User).where(User.id.in_(list(users.values()))))
        await db.execute(delete(Org).where(Org.slug == f"sec-{tag}"))
        await db.execute(
            text("delete from audit_log where actor_label like :p"), {"p": f"%-{tag}@sec.test"}
        )
        await db.commit()


def bearer(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_local_token(user_id)}"}


async def test_anonymous_is_refused_everywhere_but_health(world: dict[str, Any]) -> None:
    api = world["api"]
    for path in (
        "/runs",
        "/scenarios",
        "/stations/stats",
        "/readiness",
        "/admin/audit",
        "/twin/runs",
        "/agent/sessions",
    ):
        assert (await api.get(path)).status_code == 401, path
    async with httpx.AsyncClient(transport=api._transport, base_url="http://sec") as root:
        assert (await root.get("/health")).status_code == 200


async def test_forged_expired_and_unsigned_tokens(world: dict[str, Any]) -> None:
    api, uid = world["api"], world["users"]["planner"]
    now = datetime.now(UTC)
    forged = jwt.encode(
        {"sub": str(uid), "aud": LOCAL_AUDIENCE, "exp": now + timedelta(hours=1)},
        "not-the-secret",
        algorithm="HS256",
    )
    expired = jwt.encode(
        {"sub": str(uid), "aud": LOCAL_AUDIENCE, "exp": now - timedelta(minutes=1)},
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    unsigned = jwt.encode(
        {"sub": str(uid), "aud": LOCAL_AUDIENCE, "exp": now + timedelta(hours=1)},
        "",
        algorithm="none",
    )
    for token in (forged, expired, unsigned, "garbage"):
        r = await api.get("/runs", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401


async def test_roles_are_enforced(world: dict[str, Any]) -> None:
    api, u = world["api"], world["users"]
    body = {"name": "x", "target_year": 2028, "charger_classes": ["DC_60"], "region_id": "pmr"}
    assert (await api.get("/scenarios", headers=bearer(u["viewer"]))).status_code == 200
    assert (await api.post("/scenarios", json=body, headers=bearer(u["viewer"]))).status_code == 403
    assert (await api.get("/admin/config", headers=bearer(u["planner"]))).status_code == 403
    assert (await api.get("/admin/audit", headers=bearer(u["planner"]))).status_code == 403
    r = await api.put(
        "/admin/config/tariffs/maharashtra.yaml",
        json={"content": "a: 1\nb: 2"},
        headers=bearer(u["planner"]),
    )
    assert r.status_code == 403


async def test_other_orgs_resources_are_invisible(world: dict[str, Any]) -> None:
    api, out, sid = world["api"], world["users"]["outsider"], world["scenario"]
    h = bearer(out)
    assert (await api.get(f"/scenarios/{sid}", headers=h)).status_code == 404
    assert all(s["id"] != str(sid) for s in (await api.get("/scenarios", headers=h)).json())
    r = await api.post("/runs", json={"scenario_id": str(sid)}, headers=h)
    assert r.status_code == 404
    # An org admin manages only their own org.
    assert (
        await api.get(f"/admin/orgs/{world['other_org']}/members", headers=h)
    ).status_code == 200
    assert (await api.get("/admin/orgs/default/members", headers=h)).status_code == 404


async def test_injection_and_traversal_attempts(world: dict[str, Any]) -> None:
    api, h = world["api"], bearer(world["users"]["planner"])
    r = await api.get("/stations", params={"operator": "' OR 1=1; drop table evse;--"}, headers=h)
    assert r.status_code == 200 and r.json()["features"] == []
    r = await api.get("/runs/1;select pg_sleep(5)", headers=h)
    assert r.status_code == 422
    async with async_session_factory() as db:
        assert (await db.execute(text("select to_regclass('evse')"))).scalar() is not None


async def test_config_keys_cannot_escape_the_config_dir(world: dict[str, Any]) -> None:
    api = world["api"]
    # A platform admin, to get past the role check and reach the path check.
    admin = await _admin(world)
    for key in ("../.env", "..%2F..%2F.env", "tariffs/../../backend/app/main.py", "nope.yaml"):
        r = await api.get(f"/admin/config/{key}", headers=bearer(admin))
        assert r.status_code == 404, key


async def _admin(world: dict[str, Any]) -> uuid.UUID:
    async with async_session_factory() as db:
        default_id = (await db.execute(text("select id from org where slug = 'default'"))).scalar()
        u = User(
            email=f"admin-{uuid.uuid4().hex[:8]}-{world['other_org'][4:]}@sec.test",
            hashed_password="!",
            is_active=True,
            is_verified=True,
        )
        db.add(u)
        await db.flush()
        db.add(RoleBinding(user_id=u.id, org_id=default_id, roles=["admin"]))
        await db.commit()
        world["users"][f"admin-{u.id}"] = u.id
        return u.id


async def test_security_headers_and_cors(world: dict[str, Any]) -> None:
    api = world["api"]
    r = await api.get("/runs", headers=bearer(world["users"]["viewer"]))
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
    pre = await api.options(
        "/runs", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"}
    )
    assert "access-control-allow-origin" not in pre.headers
    ok = await api.options(
        "/runs", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"}
    )
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"


async def test_login_is_rate_limited(world: dict[str, Any]) -> None:
    api = world["api"]
    codes = [
        (
            await api.post("/auth/login", data={"username": "nobody@sec.test", "password": "x"})
        ).status_code
        for _ in range(12)
    ]
    assert 429 in codes and codes.index(429) <= 10


async def test_exports_never_carry_restricted_sources(world: dict[str, Any]) -> None:
    from app.services.exports import EXPORTABLE

    assert EXPORTABLE == {"OPEN", "GOVERNMENT", "SYNTHETIC"}
    async with async_session_factory() as db:
        run_id = (
            await db.execute(
                text(
                    "select r.id from planning_run r join scenario s on s.id = r.scenario_id "
                    "join org o on o.id = s.org_id where o.slug = 'default' and "
                    "r.funnel->'optimisation'->>'base_id' is not null limit 1"
                )
            )
        ).scalar()
    if run_id is None:
        pytest.skip("no optimised run to export")
    r = await world["api"].get(
        f"/exports/runs/{run_id}.geojson", headers=bearer(world["users"]["viewer"])
    )
    assert r.status_code == 200
    body = r.json()
    assert all(a["license_class"] in EXPORTABLE for a in body["attribution"])
    assert "RESTRICTED" not in r.text  # no restricted source or content is exported
    assert all("google" not in k for f in body["features"] for k in f["properties"])
