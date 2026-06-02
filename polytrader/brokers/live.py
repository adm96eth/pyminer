"""Live broker via ``py-clob-client``.

Lazy-imported so the rest of the package (and the whole test suite) runs with
no SDK, no keys, and no network. Constructing this class is the only thing that
pulls in ``py_clob_client``.

IMPORTANT -- this places REAL orders with REAL funds when ``dry_run=False``.
Validate it with the smallest possible size first. Marketable orders use
fill-and-kill (FAK) with a hard price cap so they never cross worse than your
limit, but fills, fees and on-chain settlement still need real-world
verification before you trust it with size.
"""

from __future__ import annotations

from typing import Optional

from ..config import Config, Secrets
from .base import Broker, Fill, Side


class LiveBroker(Broker):
    name = "live"

    def __init__(self, config: Config, secrets: Secrets):
        missing = secrets.missing_for_live()
        if missing:
            raise RuntimeError(
                "LIVE mode requires env vars: " + ", ".join(missing)
            )
        self.config = config
        self.dry_run = config.dry_run_live

        try:
            from py_clob_client.client import ClobClient  # type: ignore
            from py_clob_client.clob_types import ApiCreds  # type: ignore
        except ImportError as e:  # pragma: no cover - depends on optional dep
            raise RuntimeError(
                "py-clob-client not installed. `pip install py-clob-client` "
                "to use LIVE mode."
            ) from e

        creds = ApiCreds(
            api_key=secrets.clob_api_key,
            api_secret=secrets.clob_api_secret,
            api_passphrase=secrets.clob_api_passphrase,
        )
        # signature_type=1/2 are the Polymarket proxy/email-wallet flavors; 0 is
        # an EOA. funder is the address holding the USDC. Adjust to your account.
        kwargs = dict(
            host=config.clob_host,
            key=secrets.private_key,
            chain_id=config.chain_id,
            creds=creds,
        )
        if secrets.funder_address:
            kwargs["funder"] = secrets.funder_address
            kwargs["signature_type"] = 2
        self._client = ClobClient(**kwargs)

    def usdc_balance(self) -> float:
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType  # type: ignore

            res = self._client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            # USDC has 6 decimals on Polygon
            return float(res.get("balance", 0)) / 1_000_000.0
        except Exception as e:  # pragma: no cover - network
            raise RuntimeError(f"balance query failed: {e}") from e

    def _marketable(self, token_id: str, side: Side, size: float, limit_price: float) -> Fill:
        from py_clob_client.clob_types import OrderArgs, OrderType  # type: ignore
        from py_clob_client.order_builder.constants import BUY, SELL  # type: ignore

        args = OrderArgs(
            token_id=token_id,
            price=round(limit_price, 3),     # CLOB tick is 0.001
            size=round(size, 2),
            side=BUY if side is Side.BUY else SELL,
        )

        if self.dry_run:
            return Fill(
                token_id, side, size, 0.0, limit_price, 0.0, ok=False,
                detail=f"DRY-RUN: would {side.value} {size:.2f} @<= {limit_price:.3f}",
            )

        signed = self._client.create_order(args)
        # FAK = fill-and-kill: take what's available at/under the cap, cancel rest.
        resp = self._client.post_order(signed, OrderType.FAK)
        return self._parse_fill(token_id, side, size, limit_price, resp)

    @staticmethod
    def _parse_fill(token_id, side, size, limit_price, resp: dict) -> Fill:
        success = bool(resp.get("success"))
        # Response shapes vary; pull what we can and verify against trades later.
        making = float(resp.get("makingAmount", 0) or 0)
        taking = float(resp.get("takingAmount", 0) or 0)
        if side is Side.BUY:
            filled = taking          # tokens received
            spent = making           # USDC paid
            avg = (spent / filled) if filled else limit_price
            signed_cost = -spent
        else:
            filled = making          # tokens sold
            recv = taking            # USDC received
            avg = (recv / filled) if filled else limit_price
            signed_cost = recv
        return Fill(
            token_id, side, size, filled, avg, signed_cost,
            ok=success and filled > 0,
            detail=f"status={resp.get('status', '?')} id={resp.get('orderID', '?')}",
        )

    def buy(self, token_id: str, size: float, max_price: float) -> Fill:
        return self._marketable(token_id, Side.BUY, size, max_price)

    def sell(self, token_id: str, size: float, min_price: float) -> Fill:
        return self._marketable(token_id, Side.SELL, size, min_price)
