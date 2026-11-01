"""Intraday research and paper trading, independent of the daily system.

See docs/plans/2026-11-01-intraday-plan.md. This package has its own settings
(config/intraday.yaml), database and data folder (data/intraday/), command
(``trademare-intraday``) and dashboard (port 8766). Nothing outside it imports it, and it never
places real orders. It reads only side-effect-free shared code and the Kite login token.
"""
