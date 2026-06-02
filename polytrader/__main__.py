"""CLI entry point:  python -m polytrader [--config cfg.json] [--live] [...]"""

from __future__ import annotations

import argparse
import logging
import sys

from .brokers.base import Broker
from .brokers.paper import PaperBroker
from .config import LIVE, PAPER, Config, Secrets
from .engine import Engine
from .risk import RiskManager


def build_broker(config: Config) -> Broker:
    if config.is_live:
        from .brokers.live import LiveBroker  # lazy: needs py-clob-client

        return LiveBroker(config, Secrets.from_env())
    return PaperBroker(starting_usdc=config.paper_starting_usdc)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="polytrader", description="Polymarket internal-arbitrage bot")
    p.add_argument("--config", help="path to JSON config")
    p.add_argument("--live", action="store_true", help="LIVE mode (real orders, real funds)")
    p.add_argument("--paper", action="store_true", help="force PAPER mode (default)")
    p.add_argument("--arm", action="store_true",
                   help="in LIVE mode, actually send orders (otherwise dry-run logs only)")
    p.add_argument("--ticks", type=int, default=None, help="run N loops then exit (default: forever)")
    p.add_argument("--once", action="store_true", help="run a single tick and exit")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log = logging.getLogger("polytrader")

    config = Config.load(args.config)
    if args.live:
        config.mode = LIVE
    if args.paper:
        config.mode = PAPER
    config.__post_init__()

    if config.is_live:
        config.dry_run_live = not args.arm
        missing = Secrets.from_env().missing_for_live()
        if missing:
            log.error("LIVE mode missing env: %s", ", ".join(missing))
            return 2
        if config.dry_run_live:
            log.warning("LIVE mode but NOT armed: orders will be logged, not sent. "
                        "Pass --arm to send real orders.")
        else:
            log.warning("LIVE + ARMED: real orders with real funds will be placed.")

    broker = build_broker(config)
    risk = RiskManager(config.limits)
    engine = Engine(config, broker, risk)

    ticks = 1 if args.once else args.ticks
    engine.run(max_ticks=ticks)

    log.info("done. broker USDC=%.2f realized PnL=$%.2f",
             broker.usdc_balance(), risk.state.realized_pnl)
    return 0


if __name__ == "__main__":
    sys.exit(main())
