from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from alphaforge.core.models import Mode

# Paths are project-local: ./config is mounted into the engine container at /app/config.
# Both are overridable via env vars so local dev and tests can point elsewhere.
DEFAULT_ENV_PATH = Path(os.environ.get("ALPHAFORGE_ENV", "/app/config/env"))
DEFAULT_CONFIG_PATH = Path(os.environ.get("ALPHAFORGE_CONFIG", "/app/config/config.yaml"))


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class EnvConfig:
    username: str
    password: str
    mode: Mode
    account: str
    live_trading_enabled: bool
    serverchan_sendkey: str = ""  # optional: empty -> alerts disabled (Server酱 -> 微信)


@dataclass(frozen=True)
class IbkrConfig:
    host: str
    paper_host_port: int
    live_host_port: int
    client_id: int
    market_data_type: int

    def port_for(self, mode: Mode) -> int:
        return self.paper_host_port if mode == Mode.PAPER else self.live_host_port


@dataclass(frozen=True)
class PathsConfig:
    log_dir: Path
    state_dir: Path
    audit_log: Path
    trade_log: Path
    grid_config: Path  # spec: user-owned strategy params (engine reads, never writes)
    grid_state: Path  # status: engine-owned runtime (evolved base_price, orders, state)
    kill_switch: Path
    heartbeat: Path  # status: engine liveness beat (healthcheck + afctl status)


@dataclass(frozen=True)
class StrategyConfig:
    name: str


@dataclass(frozen=True)
class RiskConfig:
    max_positions: int
    max_symbol_position_pct: float
    max_gross_exposure_pct: float
    daily_loss_limit_pct: float
    max_spread_bps: float


@dataclass(frozen=True)
class Settings:
    env: EnvConfig
    ibkr: IbkrConfig
    paths: PathsConfig
    strategy: StrategyConfig
    risk: RiskConfig

    @property
    def ibkr_port(self) -> int:
        return self.ibkr.port_for(self.env.mode)

    def validate_for_run(self) -> None:
        if not self.env.username:
            raise ConfigError("IB_USERNAME is required in config/env")
        if not self.env.password:
            raise ConfigError("IB_PASSWORD is required in config/env")
        if not self.env.account:
            raise ConfigError("IB_ACCOUNT is required in config/env")
        if self.env.mode == Mode.LIVE and not self.env.live_trading_enabled:
            raise ConfigError("IB_MODE=live requires LIVE_TRADING_ENABLED=true")
        if self.strategy.name != "grid_v1":
            raise ConfigError("strategy.name must be grid_v1")


def load_settings(
    env_path: str | Path = DEFAULT_ENV_PATH,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
) -> Settings:
    env_path = Path(env_path)
    config_path = Path(config_path)
    env_raw = _load_env(env_path)
    config_raw = _load_yaml(config_path)

    mode_raw = env_raw.get("IB_MODE", "paper").strip().lower()
    try:
        mode = Mode(mode_raw)
    except ValueError as exc:
        raise ConfigError("IB_MODE must be paper or live") from exc

    ibkr_raw = _section(config_raw, "ibkr")
    paths_raw = _section(config_raw, "paths")
    strategy_raw = _section(config_raw, "strategy")
    risk_raw = _section(config_raw, "risk")

    settings = Settings(
        env=EnvConfig(
            username=env_raw.get("IB_USERNAME", ""),
            password=env_raw.get("IB_PASSWORD", ""),
            mode=mode,
            account=env_raw.get("IB_ACCOUNT", ""),
            live_trading_enabled=_bool(env_raw.get("LIVE_TRADING_ENABLED", "false")),
            serverchan_sendkey=env_raw.get("SERVERCHAN_SENDKEY", "").strip(),
        ),
        ibkr=IbkrConfig(
            host=str(ibkr_raw.get("host", "127.0.0.1")),
            paper_host_port=int(ibkr_raw.get("paper_host_port", 4002)),
            live_host_port=int(ibkr_raw.get("live_host_port", 4001)),
            client_id=int(ibkr_raw.get("client_id", 10)),
            market_data_type=int(ibkr_raw.get("market_data_type", 3)),
        ),
        paths=PathsConfig(
            log_dir=Path(paths_raw.get("log_dir", "/app/logs")),
            state_dir=Path(paths_raw.get("state_dir", "/app/state")),
            audit_log=Path(paths_raw.get("audit_log", "/app/logs/audit.jsonl")),
            trade_log=Path(paths_raw.get("trade_log", "/app/logs/trade.jsonl")),
            grid_config=Path(paths_raw.get("grid_config", "/app/config/grid.yaml")),
            grid_state=Path(paths_raw.get("grid_state", "/app/state/grid_state.json")),
            kill_switch=Path(paths_raw.get("kill_switch", "/app/state/kill-switch")),
            heartbeat=Path(paths_raw.get("heartbeat", "/app/state/heartbeat.json")),
        ),
        strategy=StrategyConfig(
            name=str(strategy_raw.get("name", "grid_v1")),
        ),
        risk=RiskConfig(
            max_positions=int(risk_raw.get("max_positions", 3)),
            max_symbol_position_pct=float(risk_raw.get("max_symbol_position_pct", 0.10)),
            max_gross_exposure_pct=float(risk_raw.get("max_gross_exposure_pct", 0.30)),
            daily_loss_limit_pct=float(risk_raw.get("daily_loss_limit_pct", 0.01)),
            max_spread_bps=float(risk_raw.get("max_spread_bps", 5)),
        ),
    )
    settings.validate_for_run()
    return settings


def _load_env(path: Path) -> dict[str, str]:
    if not path.exists():
        raise ConfigError(f"env file not found: {path}")
    values: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    loaded = yaml.safe_load(path.read_text()) or {}
    if not isinstance(loaded, dict):
        raise ConfigError("config.yaml must contain a mapping")
    return loaded


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{name} section must be a mapping")
    return value


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
