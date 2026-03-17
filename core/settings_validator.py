"""
Settings validation for WaggleDance configs/settings.yaml.
Fail fast with clear errors on invalid config.
"""

import logging
import os
from typing import Optional

from pydantic import BaseModel, Field, field_validator

log = logging.getLogger("waggledance.settings")


class LLMConfig(BaseModel):
    base_url: str = "http://localhost:11434"
    model: str = "phi4-mini"
    num_gpu: int = 99
    temperature: float = Field(0.4, ge=0.0, le=2.0)
    timeout: int = Field(30, ge=1)


class LearningConfig(BaseModel):
    auto_evolve: bool = False
    distill_interval_min: int = Field(15, ge=1)
    eval_queue_size: int = Field(20, ge=1)
    evolve_interval_min: int = Field(30, ge=1)
    min_finetune_score: float = Field(7.0, ge=0.0, le=10.0)
    enrichment_enabled: bool = True
    enrichment_confidence: float = Field(0.80, ge=0.0, le=1.0)
    web_learning_enabled: bool = True
    web_learning_daily_budget: int = Field(50, ge=0)
    distillation_enabled: bool = False
    distillation_model: str = "claude-haiku-4-5-20251001"
    distillation_weekly_budget_eur: float = Field(5.0, ge=0.0)
    meta_learning_enabled: bool = True
    code_review_enabled: bool = True
    micro_model_enabled: bool = True
    micro_model_v2_enabled: bool = True
    micro_model_v3_enabled: bool = False
    micro_model_training_interval: int = Field(50, ge=1)
    micro_model_min_pairs: int = Field(100, ge=1)


class HiveMindConfig(BaseModel):
    heartbeat_interval: int = Field(30, ge=5)
    idle_research_enabled: bool = True
    max_concurrent_agents: int = Field(30, ge=1, le=200)


class AlertsConfig(BaseModel):
    enabled: bool = False


class RuntimeConfig(BaseModel):
    primary: str = "waggledance"
    compatibility_mode: bool = False

    @field_validator("primary")
    @classmethod
    def valid_primary(cls, v: str) -> str:
        allowed = {"waggledance", "hivemind", "shadow"}
        if v not in allowed:
            raise ValueError(f"runtime.primary must be one of {allowed}, got '{v}'")
        return v


class WaggleSettings(BaseModel):
    """Top-level settings validation."""
    profile: str = "cottage"
    hivemind: HiveMindConfig = HiveMindConfig()
    llm: LLMConfig = LLMConfig()
    llm_heartbeat: Optional[LLMConfig] = None
    learning: LearningConfig = LearningConfig()
    alerts: AlertsConfig = AlertsConfig()
    runtime: RuntimeConfig = RuntimeConfig()

    @field_validator("profile")
    @classmethod
    def valid_profile(cls, v: str) -> str:
        allowed = {"gadget", "cottage", "home", "factory"}
        if v not in allowed:
            raise ValueError(f"profile must be one of {allowed}, got '{v}'")
        return v


def validate_settings(raw: dict) -> dict:
    """Validate settings dict. Returns validated dict.
    Raises pydantic.ValidationError on invalid config.
    """
    validated = WaggleSettings(**raw)
    log.info(f"Settings validated: profile={validated.profile}")
    return raw  # Return original dict (preserves extra keys for consumers)


def resolve_secret(yaml_value: str, env_var: str) -> str:
    """Resolve a secret: prefer env var over yaml value."""
    env_val = os.environ.get(env_var, "")
    if env_val:
        return env_val
    return yaml_value or ""
