from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="OPS_", extra="ignore")

    environment: Literal["local", "test", "production"] = "local"
    database_url: str = "sqlite:///ops.db"
    redis_url: str | None = None
    signing_key: SecretStr
    demo_mode: bool = False
    auth_issuer: str = "acme-ops"
    auth_audience: str = "acme-ops-api"
    oidc_jwks_url: str | None = None
    low_risk_approval: bool = True
    tool_transport: Literal["local", "mcp"] = "local"
    tool_timeout_seconds: float = 10
    tool_retries: int = 2
    job_lease_seconds: int = 300
    max_job_attempts: int = 3
    llm_api_key: SecretStr | None = None
    llm_enabled: bool = False
    llm_model: str = "gpt-4.1-mini"
    llm_escalation_model: str = "gpt-4.1"
    llm_input_cost_per_million: float = 0.40
    llm_output_cost_per_million: float = 1.60
    llm_escalation_input_cost_per_million: float = 2.0
    llm_escalation_output_cost_per_million: float = 8.0
    embedding_model: str | None = None
    document_index_path: Path | None = None
    embedding_cost_per_million: float = 0.02
    max_model_tokens: int = 1200

    @model_validator(mode="after")
    def production_guardrails(self) -> "Settings":
        if self.llm_enabled and not self.llm_api_key:
            raise ValueError("Enabled model assistance requires OPS_LLM_API_KEY")
        if len(self.signing_key.get_secret_value()) < 32:
            raise ValueError("OPS_SIGNING_KEY must have at least 32 characters")
        if self.environment == "production":
            if self.demo_mode:
                raise ValueError("Demo login must never be enabled in production")
            if not self.database_url.startswith("postgresql+psycopg://"):
                raise ValueError("Production requires PostgreSQL")
            if not self.redis_url:
                raise ValueError("Production requires Redis")
            if not self.oidc_jwks_url or not self.oidc_jwks_url.startswith("https://"):
                raise ValueError("Production requires an HTTPS OIDC JWKS endpoint")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
