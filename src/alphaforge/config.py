from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alphaforge.models import Mode

DEFAULT_ENV_PATH = Path("/etc/alphaforge/env")
DEFAULT_CONFIG_PATH = Path("/etc/alphaforge/config.yaml")

try:
    import yaml
except ImportError:  # pragma: no cover - dependency is installed by install.sh.
    yaml = None


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class EnvConfig:
    username: str
    password: str
    mode: Mode
    account: str
    live_trading_enabled: bool
    auto_restart_time: str


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
    kill_switch: Path


@dataclass(frozen=True)
class StrategyConfig:
    universe: tuple[str, ...]
    bar_seconds: int
    cooldown_seconds: int
    short_ema: int
    long_ema: int


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
        if self.strategy.short_ema >= self.strategy.long_ema:
            raise ConfigError("strategy.short_ema must be less than strategy.long_ema")
        if not self.strategy.universe:
            raise ConfigError("strategy.universe must not be empty")


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
            auto_restart_time=env_raw.get("AUTO_RESTART_TIME", "23:45"),
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
            kill_switch=Path(paths_raw.get("kill_switch", "/var/lib/alphaforge/kill-switch")),
        ),
        strategy=StrategyConfig(
            universe=tuple(str(item).upper() for item in strategy_raw.get("universe", [])),
            bar_seconds=int(strategy_raw.get("bar_seconds", 60)),
            cooldown_seconds=int(strategy_raw.get("cooldown_seconds", 300)),
            short_ema=int(strategy_raw.get("short_ema", 3)),
            long_ema=int(strategy_raw.get("long_ema", 12)),
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
    text = path.read_text()
    if yaml is not None:
        loaded = yaml.safe_load(text) or {}
        if not isinstance(loaded, dict):
            raise ConfigError("config.yaml must contain a mapping")
        return loaded
    return _minimal_yaml(text)


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{name} section must be a mapping")
    return value


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _minimal_yaml(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not raw_line.startswith(" ") and line.endswith(":"):
            key = line[:-1].strip()
            current = {}
            result[key] = current
            continue
        if current is None or ":" not in line:
            raise ConfigError(f"unsupported YAML line: {raw_line}")
        key, value = line.split(":", 1)
        current[key.strip()] = _parse_scalar(value.strip())
    return result


def _parse_scalar(value: str) -> Any:
    if value.startswith("[") and value.endswith("]"):
        body = value[1:-1].strip()
        return [] if not body else [item.strip() for item in body.split(",")]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value
