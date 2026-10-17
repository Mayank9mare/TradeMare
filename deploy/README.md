# Deploying TradeMare

`trademare serve` runs the dashboard plus the daily scheduler, so it needs to keep running. There are two ways to do that.

## 1. Your Mac (paper trading)

```bash
./deploy/macos/install.sh             # start at login, restart if it exits
./deploy/macos/install.sh uninstall   # remove
```

- Dashboard: http://127.0.0.1:8000. Logs go to `logs/serve.log`.
- **A sleeping Mac misses runs.** A run is still made if the Mac wakes within 6 hours of the scheduled time, and missed sessions are caught up on start. To wake the Mac before the India run on weekdays:
  `sudo pmset repeat wakeorpoweron MTWRF 16:55:00`
- Stop any manually started `trademare serve` first; both use port 8000.

## 2. A server (needed for live India trading)

SEBI requires API orders to come from a **static IP whitelisted with your broker**. Use a small VPS (1 vCPU, 1 GB RAM, about 2 GB disk) in an Indian region for low latency to NSE APIs.

```bash
git clone https://github.com/Mayank9mare/TradeMare.git && cd TradeMare
cp .env.example .env                    # broker keys, SMTP, SEC_USER_AGENT, DASHBOARD_PASSWORD
touch config/settings.local.yaml        # must exist before the first start (Docker would create a folder)
mkdir -p data logs
docker compose up -d --build
docker compose run --rm trademare uv run --no-sync trademare universe refresh
docker compose run --rm trademare uv run --no-sync trademare data update
```

- **Dashboard access:** it is published only on the server's localhost. Reach it with an SSH tunnel from your laptop: `ssh -L 8000:127.0.0.1:8000 you@server`, then open http://127.0.0.1:8000. Set `DASHBOARD_PASSWORD` anyway.
- **Zerodha daily login:** `docker compose run --rm trademare uv run --no-sync trademare broker login zerodha`. The token is saved in `data/tokens/`, which the running service shares.
- **Broker IP whitelist:** add the server's public IP in your broker's API console (Kite developer console, or Dhan's API settings).
- **Updates:** `git pull && docker compose up -d --build`. Your data, settings and secrets live outside the image.
- **Backups:** copy `data/trademare.db` regularly. It holds all signals, orders, positions and the audit log.

## Before any real money

Follow the going-live checklist in the user guide: a validated strategy, months of paper results, `broker check` clean, a small `budget`, and `validated: true` for that market only.
