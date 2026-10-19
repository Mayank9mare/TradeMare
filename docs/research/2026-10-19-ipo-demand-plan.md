# India IPOs: does institutional demand predict the listing? (pre-registered plan, written before any listing price was fetched)

The dashboard is getting an IPO section. Before it shows any verdict on an IPO, this study asks one
question fixed in advance: **does the final subscription of the Qualified Institutional Buyer (QIB)
portion predict how an NSE mainboard IPO trades on its listing day?** Indian studies and market
practice both point to QIB demand as the most informative public number, but this project does not
trade on folklore. Buckets, outcomes, the pass bar and how the result is used are fixed here; results
are appended at the end without editing the plan.

## What has been looked at so far
- NSE's IPO endpoints were checked for format and history depth only: `public-past-issues` (1,479
  issues, 485 of them mainboard `EQ`, back to 2016) and `ipo-active-category` (final subscription by
  category; history reaches back at least to IRCTC in 2019). Three subscription figures were seen
  (IRCTC, Zomato, LIC) and two current issues; no listing price for any IPO has been fetched.

## Sample
- NSE mainboard IPOs (`securityType` `EQ`) with an issue price and a listing date between
  2016-01-01 and 2026-10-17. SME issues are excluded: different rules, tiny floats, and they are
  not part of what the dashboard scores.
- An IPO is dropped when its final subscription cannot be fetched or Yahoo has no daily bar for
  `SYMBOL.NS` within 3 calendar days of the listing date (renames and delistings). The count dropped
  is reported.

## Measures
- **Predictor:** final QIB subscription (times the QIB portion was bid for, NSE + BSE combined).
- **Primary outcome:** listing-day gain = listing-day close ÷ issue price − 1.
- **Secondary outcomes:** listing-day open ÷ issue price − 1; return from issue price to the close
  126 trading days after listing, and the same for the Nifty 50 (`^NSEI`) over the same days.

## Fixed buckets (QIB subscription)
`< 1×` · `1–10×` · `10–50×` · `50–100×` · `≥ 100×`. Each bucket reports: count, median listing-day
gain, share listing above the issue price (close), and median 6-month excess return over the Nifty.

## Test
**H1:** the Spearman rank correlation between QIB subscription and listing-day gain is positive with
p < 0.05 **in both halves** of the sample: issues closing 2016-01 → 2021-12 and 2022-01 → 2026-10.

## Decision (fixed before results)
- **H1 holds:** the IPO page shows, for each open or closed-not-yet-listed IPO, the full-sample stats
  of its QIB bucket and a label from them:
  - **Favourable** if the bucket's share listing above issue is ≥ 75% and its median gain ≥ 10%;
  - **Unfavourable** if the share is < 60% or the median gain < 0;
  - **Mixed** otherwise.
- **H1 fails:** the page shows the facts and bucket stats but **no label**.
- Either way the 6-month result is shown as a separate note on whether holding after listing has
  paid, not as part of the label. No IPO is ever applied for automatically; applications go through
  the owner's bank or broker UPI flow.

## Known limitations (stated in advance)
- **The QIB figure is only final at the close.** QIBs bid mostly on the last day, so the number shown
  while an issue is open is partial and normally rises. Bucket stats use final figures.
- **Survivorship:** renamed or delisted companies may be missing from Yahoo; failures are more likely
  to be among them, which flatters later returns more than listing-day ones.
- **Allotment is a lottery** for retail when the retail portion is oversubscribed, so a favourable
  label is about the listing, not about what any one applicant receives.
- **Listing-day close ≠ a fill.** Selling at the open or close is assumed with no costs.
- Market mood moves all IPOs together (2021 and 2024 were hot); buckets pool across cycles.

---

# Results (appended after the run; the plan above was committed first, in 3930fca)

## Sample
466 mainboard issues listed 2016-01-01 → 2026-10-17 had an issue price and listing date. 33 had no
subscription figures at NSE and 22 had no Yahoo bar within 3 days of listing (renamed or delisted),
leaving **411**. Five large outliers were spot-checked against the known listings and matched (for
example Tata Technologies ₹500 → ₹1,313 close, Bajaj Housing Finance ₹70 → ₹165, CDSL ₹149 → ₹262).

## Test
| Half | Spearman ρ (QIB vs listing-day gain) | p | n |
|---|---|---|---|
| 2016–2021 | 0.73 | < 1e-28 | 116 |
| 2022–2026 | 0.69 | < 1e-60 | 295 |
| Full sample | 0.69 | < 1e-80 | 411 |

**H1 holds** in both halves, by a wide margin.

## Buckets (full sample)
| QIB subscribed | IPOs | Median listing-day gain | Listed above issue | Median 6 months vs Nifty | Label (fixed rule) |
|---|---|---|---|---|---|
| < 1× | 3 | +16.6% | 67% | −11.1% | Mixed (only 3 issues) |
| 1–10× | 138 | −1.3% | 43% | −0.4% | **Unfavourable** |
| 10–50× | 79 | +5.4% | 65% | −4.7% | Mixed |
| 50–100× | 60 | +26.6% | 92% | +34.5% | **Favourable** |
| ≥ 100× | 131 | +36.3% | 96% | +42.2% | **Favourable** |

All issues together: median listing-day gain +11.5%, 71% listed above issue.

## Reading
- Institutional demand sorts listings sharply. Below 10× QIB, buying in the IPO was a coin flip
  with a slightly negative median; from 50× up, more than 9 in 10 listed above the issue price.
- The 6-month column points the same way, but survivorship (22 issues missing prices) flatters it,
  and it is not part of the label.
- The < 1× bucket has 3 issues; its label is shown with that count and means little.

## Applied
The IPO page shows each issue's bucket record and the label above. The QIB figure while bidding is
open is partial (institutions bid mostly on the last day), so the page and the closing-day alert say
so; the label is only firm after the close at 5 pm.
