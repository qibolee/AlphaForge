from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from alphaforge.core.models import Mode

DEFAULT_ENV_PATH = Path("/etc/alphaforge/env")
DEFAULT_CONFIG_PATH = Path("/etc/alphaforge/config.yaml")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class EnvConfig:
    username: str
    password: str
    mode: Mode
    account: str
    live_trading_enabled: bool


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
    grid_config: Path
    kill_switch: Path


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
            raise ConfigError("IB_USERNAME is required in /etc/alphaforge/env")
        if not self.env.password:
            raise ConfigError("IB_PASSWORD is required in /etc/alphaforge/env")
        if not self.env.account:
            raise ConfigError("IB_ACCOUNT is required in /etc/alphaforge/env")
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
        ),
        ibkr=IbkrConfig(
            host=str(ibkr_raw.get("host", "127.0.0.1")),
            paper_host_port=int(ibkr_raw.get("paper_host_port", 4002)),
            live_host_port=int(ibkr_raw.get("live_host_port", 4001)),
            client_id=int(ibkr_raw.get("client_id", 10)),
            market_data_type=int(ibkr_raw.get("market_data_type", 3)),
        ),
        paths=PathsConfig(
            log_dir=Path(paths_raw.get("log_dir", "/var/log/alphaforge")),
            state_dir=Path(paths_raw.get("state_dir", "/var/lib/alphaforge")),
            audit_log=Path(paths_raw.get("audit_log", "/var/log/alphaforge/audit.jsonl")),
            trade_log=Path(paths_raw.get("trade_log", "/var/log/alphaforge/trade.jsonl")),
            grid_config=Path(paths_raw.get("grid_config", "/etc/alphaforge/grid.yaml")),
            kill_switch=Path(paths_raw.get("kill_switch", "/var/lib/alphaforge/kill-switch")),
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
