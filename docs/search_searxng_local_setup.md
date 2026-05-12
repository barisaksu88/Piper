# Local SearXNG Setup for Piper Manual Testing

This guide explains how to run a local SearXNG instance so you can manually test Piper's SearXNG search backend. **SearXNG is completely optional** — Piper works fine with the default DuckDuckGo backend.

---

## What is SearXNG?

SearXNG is a free, self-hosted metasearch engine. It queries multiple search engines and aggregates results. Piper can use it as an alternative search backend when `SEARCH_BACKEND` is set to `"searxng"`.

- Piper uses SearXNG **only** as a search backend
- It is **optional** — the default DuckDuckGo backend works without it
- No SearXNG integration runs during normal Piper startup

---

## Windows Prerequisites

SearXNG runs inside Docker. You must install Docker Desktop manually first.

1. Download and install [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/)
   - Installation may require Administrator rights
   - A system restart may be required after install
2. During setup, enable the **WSL2 backend** when prompted
3. After installation, open a new PowerShell window and verify:

```powershell
docker --version
docker info
```

If either command fails, Docker Desktop is not installed or not on your PATH. Restart your terminal or reboot and try again.

---

## Start SearXNG

Once Docker is working, run:

```powershell
docker run --rm -d --name piper-searxng -p 8888:8080 searxng/searxng:latest
```

This downloads the SearXNG image and starts it on `http://127.0.0.1:8888`.

---

## Test SearXNG Manually

### Browser

Open:

```
http://127.0.0.1:8888/search?q=test&format=json
```

You should see a JSON response with search results.

### PowerShell

```powershell
Invoke-RestMethod "http://127.0.0.1:8888/search?q=test&format=json"
```

---

## Configure Piper

Edit `config.py` (or create `data/state/config_override.json`):

```python
SEARCH_BACKEND = "searxng"
SEARXNG_URL = "http://127.0.0.1:8888"
SEARXNG_TIMEOUT_S = 10.0
```

Restart Piper after changing the backend.

---

## Run Piper and Test

Try these queries to exercise the new resolver + backend:

- `Search online for recent developments in AI.`
- `Can you search for it please online?`
- `No, I meant latest AI news.`
- `Now search for recent models.`
- `Search for it online.`

Watch the console/logs to confirm results come from SearXNG.

---

## Stop SearXNG

```powershell
docker stop piper-searxng
```

The `--rm` flag in the run command automatically removes the container when stopped.

---

## Reset Piper to DuckDuckGo

If you want to switch back:

```python
SEARCH_BACKEND = "duckduckgo"
```

No other changes are needed.

---

## Optional Helper Script

`scripts/searxng_local_check.py` can check whether Docker and SearXNG are available, and optionally start the container for you.

```powershell
# Check status
python scripts/searxng_local_check.py

# Check status as JSON
python scripts/searxng_local_check.py --json

# Start SearXNG if not running
python scripts/searxng_local_check.py --start
```

The script will **never** install Docker for you. If Docker is missing, it prints instructions to install Docker Desktop manually.
