# Business recommendation: investing $50,000 in SPY and QQQ

*All figures are from the pipeline run on 13 October 2026 (prices 10 March 1999 to 13 October 2026;
risk-free rate 4.23%, the 3-month Treasury yield). The dashboards update every weekday, so
figures there will drift from the ones quoted here.*

## The question

> What allocation between SPY (S&P 500) and QQQ (Nasdaq-100) maximises the 5-year Sharpe
> ratio of a $50,000 portfolio, while keeping the 1-year 95% Value-at-Risk within a retail
> investor's tolerance?

Tolerance is defined as the largest 1-year loss the investor accepts in a 1-in-20 bad year:
**Conservative 10%, Moderate 20%, Aggressive 30%** of capital.

## The answer

1. **Within the stock portion, tilt toward QQQ: about 80–85% QQQ / 15–20% SPY.** This mix has the
   highest median 5-year Sharpe ratio (0.363, against 0.306 for 100% SPY). 80% QQQ is also the most robust
   choice: across five different calibration histories, it is never more than 0.009 Sharpe
   below the best mix for that history (minimax regret).
2. **Size the stock portion to your risk tolerance, and hold the rest in T-bills.** No all-stock mix
   meets the Moderate limit: even 100% SPY has a 20.9% one-year VaR. Holding the best stock mix
   plus T-bills keeps the same Sharpe ratio and brings the bad-year loss within the limit.

| Profile (max 1-yr loss) | QQQ | SPY | T-bills | Median value in 5 yrs | Bad case (5th pct.) | Bad-year loss (VaR) |
|---|---|---|---|---|---|---|
| Conservative (10%) | $18,700 | $3,300 | $28,000 | $73,300 | $48,700 | $4,990 |
| **Moderate (20%)** | **$32,300** | **$5,700** | **$12,000** | **$79,850** | **$39,330** | **$9,880** |
| Aggressive (30%) | $42,500 | $7,500 | — | $83,360 | $32,790 | $13,370 |

3. **The stocks-vs-T-bills decision matters far more than the SPY:QQQ ratio.** At the same
   Moderate risk level, a 50/50 stock mix ends with a median of $79,230, against $79,850 for the
   85/15 mix: a difference of about $600 on $50,000 over five years. The QQQ tilt is optimal but its benefit is
   small. An investor uneasy about concentrating in tech can hold 50/50 and give up very little.

## Evidence

**History favours QQQ on return, not clearly on risk-adjusted return.**
Since 1999, QQQ returned 10.9% a year against 8.6% for SPY, and beat SPY in 17 of 27 calendar
years. It also fell 83% in the dot-com crash, against SPY's worst fall of 55%. Over the last 3
years QQQ returned more (27.6% vs 23.4% a year) but had a *lower* Sharpe ratio (1.08 vs 1.16).

**The Sharpe ratio barely moves above 50% QQQ, but tail risk does.**

| QQQ share | Median 5-yr Sharpe | 1-yr 95% VaR | Worst drawdown (95th pct.) | Median 5-yr value |
|---|---|---|---|---|
| 0% | 0.306 | 20.9% | 50% | $75,580 |
| 50% | 0.356 | 23.5% | 55% | $81,100 |
| 85% (max Sharpe) | 0.363 | 26.7% | 60% | $83,360 |
| 100% | 0.361 | 28.5% | 63% | $83,790 |

The answer to *"What is the 95th-percentile worst drawdown for a tech-heavy portfolio?"*
is **63% for 100% QQQ**, against 50% for 100% SPY.

**The "optimal" ratio depends on which history you believe.** Re-calibrating the simulation
moves the max-Sharpe QQQ share between 40% and 100%:

| Calibration | Max-Sharpe QQQ share |
|---|---|
| Full history 1999–2026 (base case) | 85% |
| Last 15 years | 95% |
| Last 10 years | 100% |
| Last 5 years | 40% |
| Equal-Sharpe prior (no assumed QQQ edge) | 50% |

This is why the recommendation is a range (80–85%) and why the T-bill sizing, which does not
depend on the drift assumption, is the more important lever.

**A/B test: 60/40 vs 50/50 SPY/QQQ shows no reliable difference.**
A 1,000-iteration block bootstrap gives a Sharpe difference of −0.002 (95% CI −0.020 to +0.019,
p = 0.85). The naive paired t-test over rolling 3-year windows reports p < 0.0001, but those windows
overlap by 35 of 36 months. Corrected for the overlap (Newey-West), the same test gives p = 0.14.
The two strategies drift apart by only 1.45% a year.

**Near-term risk is below average.** The machine-learning volatility model (a HAR baseline plus
gradient boosting) forecasts next-month volatility of 15% for QQQ and 12% for SPY, against
long-run levels of 27% and 19%. Out of sample, it beats the "next month = last month" baseline by
12% (QQQ) and 12% (SPY) on RMSE. Calm forecasts do not justify extra risk, because volatility can rise quickly.

## Risks and limits

- **Model risk:** the Monte Carlo uses geometric Brownian motion, which assumes normally
  distributed returns. Real daily returns have much fatter tails: 43–46 days beyond 4σ since 1999,
  where a normal distribution predicts fewer than one. We mitigate this by calibrating on a full
  cycle that includes three crashes, reporting drawdown percentiles alongside VaR, and
  bootstrapping real returns for the A/B test. Treat the VaR figures as lower bounds.
- **Concentration:** QQQ holds about 100 stocks, heavily weighted to large technology companies.
  SPY already holds most of them, which is why the two correlate at 0.85–0.95.
- **Not personalised advice:** this is an educational analysis. It ignores taxes, trading
  costs beyond expense ratios (SPY 0.09%, QQQ 0.18%), and the investor's other assets.

## Next steps

1. Use the **Allocator** (web app, or Tableau Dashboard 3) to read off the plan for any tolerance
   between 5% and 40%.
2. Rebalance back to target weights once a year, or when either ETF drifts more than 5 points
   from target.
3. Re-run the pipeline before any change of allocation. The dashboards show whether the
   calibration, the forecast or the A/B result has moved.
