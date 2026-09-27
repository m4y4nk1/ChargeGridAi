from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    # Repo-level config/ (Section 16); mounted at /config inside containers.
    config_dir: Path = Path(__file__).resolve().parents[3] / "config"
    # Explicit allow-list (Section 14); never "*".
    cors_origins: list[str] = ["http://localhost:5173"]
    database_url: str = "postgresql+asyncpg://chargegrid:chargegrid@postgis:5432/chargegrid"
    redis_url: str = "redis://redis:6379/0"
    s3_endpoint: str = "http://minio:9000"
    s3_bucket: str = "chargegrid-raw"
    s3_access_key: str = "chargegrid"
    s3_secret_key: str = "chargegrid123"
    valhalla_url: str = "http://valhalla:8002"
    martin_url: str = "http://martin:3000"
    mlflow_tracking_uri: str = "http://mlflow:5000"
    langfuse_host: str = "http://langfuse:3000"

    google_maps_server_key: str = ""
    # Solar comes from NASA POWER; Google Solar (buildingInsights) only when switched on.
    google_solar_enabled: bool = False
    mappls_client_id: str = ""
    mappls_client_secret: str = ""
    tomtom_api_key: str = ""
    ocm_api_key: str = ""
    data_gov_in_api_key: str = ""

    # Blank falls back to a dev-only secret; any other environment must set it.
    jwt_secret: str = ""

    # Auth (Section 14). Off in dev: requests without a token act as an anonymous
    # member of the default org with `anonymous_role`. Required outside dev.
    auth_required: bool = False
    anonymous_role: str = "planner"
    default_org_slug: str = "default"
    # OIDC (Keycloak). Tokens are verified against the issuer's JWKS.
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_client_id: str = ""  # public client the web app uses (PKCE)
    # Where the API fetches signing keys when the issuer URL isn't reachable from inside
    # the cluster (e.g. issuer http://localhost:8081/..., keys at http://keycloak:8080/...).
    oidc_jwks_url: str = ""
    oidc_roles_claim: str = "realm_access.roles"
    oidc_org_claim: str = "org"
    # Long workflows (planning runs): celery (default) or temporal (ADR 0011).
    job_runner: str = "celery"
    temporal_address: str = "temporal:7233"

    llm_provider: str = "anthropic"
    llm_model_planner: str = ""
    llm_model_report: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # None means "no cap configured yet" — distinct from 0 ("cap set to zero").
    cost_cap_monthly_inr_google: int | None = None
    cost_cap_monthly_inr_llm: int | None = None
    cost_cap_per_run_inr: int | None = None

    @field_validator(
        "cost_cap_monthly_inr_google",
        "cost_cap_monthly_inr_llm",
        "cost_cap_per_run_inr",
        mode="before",
    )
    @classmethod
    def _blank_env_to_none(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def _require_jwt_secret_outside_dev(self) -> "Settings":
        if not self.jwt_secret:
            if self.app_env != "dev":
                raise ValueError("JWT_SECRET must be set when APP_ENV is not 'dev'")
            self.jwt_secret = "dev-insecure-secret-change-me"
        if self.app_env != "dev" and not self.auth_required:
            raise ValueError("AUTH_REQUIRED must be true when APP_ENV is not 'dev'")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
