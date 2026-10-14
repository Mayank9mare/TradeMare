"""TradeMare: swing-trading signal engine for Indian and US equities."""

__version__ = "0.1.0"


def main() -> None:
    from trademare.cli import main as cli_main

    cli_main()
