"""Configuration and the paper<->live MODE switch.

Non-secret settings come from a JSON file (``--config``) and/or environment
variables. Secrets (private key, API creds) come from the environment ONLY and
are never read from or written to the config file, so they can't be committed
by accident.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Optional

from .risk import RiskLimits
from .sizing import SizingConfig

PAPER = "paper"
LIVE = "live"


@dataclass
class Secrets:
    """Pulled from env only. Required for LIVE mode."""

    private_key: Optional[str] = None        # POLY_PRIVATE_KEY (wallet key)
    clob_api_key: Optional[str] = None        # POLY_API_KEY
    clob_api_secret: Optional[str] = None     # POLY_API_SECRET
    clob_api_passphrase: Optional[str] = None  # POLY_API_PASSPHRASE
    funder_address: Optional[str] = None      # POLY_FUNDER (proxy/funder addr)

    @classmethod
    def from_env(cls) -> "Secrets":
        return cls(
            private_key=os.environ.get("POLY_PRIVATE_KEY"),
            clob_api_key=os.environ.get("POLY_API_KEY"),
            clob_api_secret=os.environ.get("POLY_API_SECRET"),
            clob_api_passphrase=os.environ.get("POLY_API_PASSPHRASE"),
            funder_address=os.environ.get("POLY_FUNDER"),
        )

    def missing_for_live(self) -> list:
        need = {
            "POLY_PRIVATE_KEY": self.private_key,
            "POLY_API_KEY": self.clob_api_key,
            "POLY_API_SECRET": self.clob_api_secret,
            "POLY_API_PASSPHRASE": self.clob_api_passphrase,
        }
        return [k for k, v in need.items() if not v]


@dataclass
class Config:
    mode: str = PAPER                     # "paper" or "live"
    clob_host: str = "https://clob.polymarket.com"
    gamma_host: str = "https://gamma-api.polymarket.com"
    chain_id: int = 137                   # Polygon mainnet

    poll_interval_s: float = 5.0
    market_limit: int = 50                # how many active markets to scan
    fee_bps: float = 0.0                  # CLOB taker fee (bps of $1)
    gas_cost_per_set: float = 0.0         # amortized merge/redeem gas, USDC

    paper_starting_usdc: float = 1000.0
    dry_run_live: bool = True             # in LIVE, log orders without sending

    limits: RiskLimits = field(default_factory=RiskLimits)
    sizing: SizingConfig = field(default_factory=SizingConfig)

    def __post_init__(self):
        if self.mode not in (PAPER, LIVE):
            raise ValueError(f"mode must be {PAPER!r} or {LIVE!r}, got {self.mode!r}")
        # keep the strategy threshold and the risk threshold consistent
        self.limits.min_edge_per_set = max(
            self.limits.min_edge_per_set, 0.0
        )

    @property
    def is_live(self) -> bool:
        return self.mode == LIVE

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Config":
        data: dict = {}
        if path and os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
        limits_data = data.pop("limits", {})
        sizing_data = data.pop("sizing", {})
        cfg = cls(**data)
        if limits_data:
            cfg.limits = RiskLimits(**{**asdict(cfg.limits), **limits_data})
        if sizing_data:
            cfg.sizing = SizingConfig(**{**asdict(cfg.sizing), **sizing_data})
        # env override for the one switch people flip most
        env_mode = os.environ.get("POLYTRADER_MODE")
        if env_mode:
            cfg.mode = env_mode
            cfg.__post_init__()
        return cfg

    def build_sizer(self, starting_equity: Optional[float] = None):
        """Construct a Sizer. ``starting_equity`` defaults to the paper bankroll;
        in LIVE mode set ``paper_starting_usdc`` to your real bankroll so the
        compound-fraction and profit-target math use the right baseline."""
        from .sizing import Sizer

        eq = self.paper_starting_usdc if starting_equity is None else starting_equity
        return Sizer(self.sizing, eq)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d
