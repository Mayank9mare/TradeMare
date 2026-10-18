import re
from pathlib import Path

from trademare.strategy.evidence import FUND_METRICS, TECH_METRICS
from trademare.web.glossary import GLOSSARY, tip

TEMPLATES = Path(__file__).parent.parent / "src" / "trademare" / "web" / "templates"


def test_every_tip_used_in_a_template_has_an_explanation():
    used = set()
    for path in TEMPLATES.glob("*.html"):
        used |= set(re.findall(r'tip\("([a-z0-9_]+)"', path.read_text()))
    assert len(used) > 60
    assert used - GLOSSARY.keys() == set()


def test_loop_rendered_keys_are_covered():
    loops = set(TECH_METRICS) | set(FUND_METRICS) | {"trend", "momentum", "setup", "volume"}
    loops |= {"quality", "health", "growth", "value"}  # fundamental factors
    loops |= {"total_return", "cagr", "volatility", "sharpe", "sortino", "max_drawdown", "calmar"}
    assert loops - GLOSSARY.keys() == set()


def test_tip_markup_is_escaped_and_accessible():
    html = str(tip("pnl", "P&L"))
    assert 'class="tip"' in html and 'aria-label="What is P&amp;L?"' in html and "&amp;" in html
    assert str(tip("no_such_term")) == ""
