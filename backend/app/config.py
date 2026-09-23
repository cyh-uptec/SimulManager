"""YAML 설정 로드 — config/default.yaml 스키마 (docs/DEVELOPMENT_PLAN.md §2.3)."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = BACKEND_DIR / "config" / "default.yaml"
# UI 설정 대시보드에서 저장한 사용자 오버라이드 — 기본 설정 위에 병합
USER_SETTINGS_PATH = BACKEND_DIR / "config" / "user_settings.yaml"


class NetworkConfig(BaseModel):
    bind: str = "0.0.0.0"
    web_port: int = 8100
    mode: Literal["single_port", "per_link_port"] = "per_link_port"
    port: int = 502
    link1_port: int = 7000
    link3_port: int = 5021
    ems_ip: str = "127.0.0.1"
    rtds_ip: str = "127.0.0.1"
    unit_id: int = 1


class SimulationConfig(BaseModel):
    start_virtual_time: datetime = datetime(2025, 7, 1, 0, 0)
    days: int = 30
    accel_seconds_per_15min: float = Field(10.0, ge=1.0, le=900.0)
    initial_soc: float = 50.0
    soc_max: float = 90.0
    soc_min: float = 10.0
    charge_limit_kw: float = 250.0
    discharge_limit_kw: float = 250.0

    @field_validator("start_virtual_time", mode="before")
    @classmethod
    def _parse_dt(cls, v: object) -> object:
        if isinstance(v, str):
            return datetime.fromisoformat(v)
        return v

    @property
    def real_seconds_per_virtual_minute(self) -> float:
        """가상 1분 = 실 (가속배율/15) 초."""
        return self.accel_seconds_per_15min / 15.0

    @property
    def total_intervals(self) -> int:
        """총 15분 구간 수 (30일 → 2,880구간)."""
        return self.days * 96


class TimingConfig(BaseModel):
    response_timeout_ms: int = 1000
    retry_count: int = 3
    reconnect_interval_s: tuple[float, float] = (1.0, 5.0)
    heartbeat_period_s: float = 1.0
    alive_timeout_s: float = 3.0
    disconnect_budget_intervals: int = 14


class ScenarioConfig(BaseModel):
    interpolation: Literal["hold", "linear"] = "hold"
    data_dir: str = "../data"


class HistorianConfig(BaseModel):
    db_path: str = "../data/historian.db"


class AppConfig(BaseModel):
    network: NetworkConfig = NetworkConfig()
    simulation: SimulationConfig = SimulationConfig()
    timing: TimingConfig = TimingConfig()
    scenario: ScenarioConfig = ScenarioConfig()
    historian: HistorianConfig = HistorianConfig()


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: str | Path | None = None) -> AppConfig:
    """설정 파일 로드. 우선순위: user_settings.yaml > (인자 > SIMUL_CONFIG > default.yaml)."""
    if path is None:
        path = os.environ.get("SIMUL_CONFIG", DEFAULT_CONFIG_PATH)
    path = Path(path)
    if not path.is_absolute():
        path = BACKEND_DIR / path
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if USER_SETTINGS_PATH.exists():
        with open(USER_SETTINGS_PATH, encoding="utf-8") as f:
            raw = _deep_merge(raw, yaml.safe_load(f) or {})
    return AppConfig.model_validate(raw)


def save_user_settings(section: str, data: dict) -> None:
    """사용자 설정 오버라이드 저장 (UI 설정 대시보드 → user_settings.yaml)."""
    existing: dict = {}
    if USER_SETTINGS_PATH.exists():
        with open(USER_SETTINGS_PATH, encoding="utf-8") as f:
            existing = yaml.safe_load(f) or {}
    existing = _deep_merge(existing, {section: data})
    with open(USER_SETTINGS_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(existing, f, allow_unicode=True, sort_keys=False)
