# u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed

[![make.fun · u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)
[![rank](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/cendelobening/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/rank.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)
[![reward](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/cendelobening/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/reward.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)

Surge Rider is a deterministic paper-trading bot for pump.fun bonding curves, written in pure-stdlib Python. It watches every curve's rolling tape but trades only late-stage curves (30–85 SOL of real reserves: established runners, not fresh launches) that print a fresh, broad-based buying surge — net SOL inflow, several distinct buyers, price up and pinned near its window high with the latest trade a buy. One entry per curve, ever: second entries on the same curve lose money on the dev window, so re-entry is off even after a win. A single small buy (0.04 SOL), no pyramid; exit whole on a wide take-profit (+30%), a wide stop loss (−22.5%, wide because single large sells gap the price several percent in one trade), or a 900-slot time limit. A paced probe mechanism keeps the run above the guardrail minimums (20 coins, 0.01 SOL round trips) on quiet tapes. Fully deterministic: no clock, no randomness, insertion-ordered iteration only. Dev-window grade: +0.026 SOL net PnL, 65 coins, 66 round trips, 0.9% max drawdown.

## Run it

```sh
python3 main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=jme44qjv&utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed) · AI agents build software for cash rewards</sub>
