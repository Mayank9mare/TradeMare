# Sentiment overlay (step 8): evaluation plan, written before any results

**What it does:** when `llm.enabled: true`, each candidate entry's last 7 days of Google News headlines are scored by Claude (`claude-opus-5-5`, low effort, strict JSON schema). A long entry is vetoed on any red flag (fraud/accounting, regulatory action, auditor resignation, default, major litigation, guidance cut, management exit, promoter pledge/selling) or a score ≤ −0.5. It never changes the ranking.

**Why it can't be backtested:** there is no free point-in-time archive of headlines, and the model's training knowledge of past events would leak into any historical test.

**How it will be judged (paper trading):**
1. Every veto is logged (`sentiment_veto` events: symbol, date, entry price, composite, sentiment, reasons).
2. After at least 30 vetoes, or 6 months, compare the 20- and 60-session forward returns of vetoed ideas with those of approved ideas from the same rebalance dates.
3. **Keep the overlay** only if vetoed ideas underperform the approved ones by more than their combined standard error. Otherwise disable it, which also saves the API cost.

The bar above is fixed now and will not be changed after seeing results.
