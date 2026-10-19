# ruff: noqa: RUF001
from trademare.web.reasons import plain, split_reasons

FAST = {
    "technical": ["Uptrend: price > SMA50 > SMA200", "Pullback to SMA20 in uptrend (RSI 54)"],
    "fundamental": [
        "Interest cover 394.5x (#1 of 45 Industrials peers)",
        "ROA 26% (#1 of 82 Industrials peers)",
        "FCF yield 2% (#75 of 82 Industrials peers)",
        "Earnings yield 2% (#73 of 82 Industrials peers)",
        "Revenue growth 9% (#40 of 82 Industrials peers)",
    ],
    "events": ["RED FLAG something"],
}


def test_bottom_ranks_count_against_and_jargon_is_rewritten():
    r = split_reasons(FAST)
    assert r.pro[0] == "In an uptrend: price above its 50- and 200-day averages"
    assert "Dipped back to its 20-day average within an uptrend (RSI 54)" in r.pro
    assert r.con == [
        "FCF yield 2% (#75 of 82 Industrials peers)",
        "Earnings yield 2% (#73 of 82 Industrials peers)",
    ]
    assert r.neutral == ["Revenue growth 9% (#40 of 82 Industrials peers)"]
    assert not any("RED FLAG" in x for x in r.pro + r.con + r.neutral)  # shown as warnings instead
    assert r.summary == "In an uptrend: price above its 50- and 200-day averages; 2 points against."


def test_short_flips_and_plain_phrases():
    r = split_reasons({"technical": ["Downtrend: price < SMA50 < SMA200"]}, "SHORT")
    assert r.pro and not r.con
    assert plain("Breakout above 20-day high on 1.8x volume") == (
        "Broke above its 20-day high on 1.8× normal volume",
        1,
    )
    assert plain("Extended: +14% from SMA50, RSI 77 — poor long entry")[1] == -1
    assert plain("12-1 month momentum +85% (#10 of 190)")[1] == 1
    assert split_reasons({}).summary == ""
