"""polytrader -- a Polymarket internal-arbitrage trading bot.

Strategy: a complete set of a market's mutually-exclusive outcome tokens
redeems for exactly $1.00 at resolution (and can be merged back to $1 of
collateral on-chain at any time). If the cost to *buy* a complete set on the
CLOB is below $1.00 (after fees + gas), the difference is a near-riskless edge.

The package is deliberately split so the money-sensitive parts are isolated and
testable:

    models      pure data types (order books, markets, opportunities)
    strategy    pure arb-detection math (no I/O, fully unit-tested)
    risk        pre-trade risk checks + kill switch
    brokers     PaperBroker (simulated) and LiveBroker (py-clob-client)
    client      read-only Polymarket REST access (markets + order books)
    engine      the wiring/loop
    config      configuration + the paper<->live MODE switch
"""

__version__ = "0.1.0"
