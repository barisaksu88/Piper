# Local SearXNG Setup for Piper Manual Testing

This guide explains how to run a local SearXNG instance so you can manually test Piper's SearXNG search backend. **SearXNG is completely optional** - Piper works fine with the default DuckDuckGo backend, and Docker/SearXNG failures should stay isolated from unrelated chat startup.

---

## What is SearXNG?

SearXNG is a free, self-hosted metasearch engine. It queries multiple search engines and aggregates results. Piper can use it as an alternative search backend when `SEARCH_BACKEND` is set to `"searxng"`.

- Piper uses SearXNG **only** as a search backend
- It is **optional** — the default DuckDuckGo backend works without it
- It is a local service, not part of Piper's core startup requirement
- It is preferred for local live testing because the manual search results have been better than the fallback backend in this branch
- No SearXNG integration should run during normal Piper import/module load

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

## Current Search Flow

Piper's search path is split into three layers:

- `core/routing/route_normalizer.py` decides whether a turn is `SEARCH`
- `core/search/topic_resolver.py` resolves the exact query that should be searched
- `tools/search.py` and the active backend adapter perform retrieval
- Reporter and Persona summarize grounded results honestly after retrieval completes

That separation keeps topic resolution deterministic and keeps search failures from poisoning unrelated chat turns.

## Start SearXNG

Once Docker is working, run:

```powershell
docker run --rm -d --name piper-searxng -p 8888:8080 searxng/searxng:latest
```

This downloads the SearXNG image and starts it on `http://127.0.0.1:8888`.

If Piper starts SearXNG later through its lifecycle hooks, it should only manage containers it started itself. If the container was already running before Piper booted, Piper must leave it alone on shutdown.

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
SEARXNG_AUTO_START = True
SEARXNG_STOP_ON_EXIT = True
SEARXNG_DOCKER_CONTAINER = "piper-searxng"
SEARXNG_DOCKER_IMAGE = "searxng/searxng:latest"
SEARXNG_DOCKER_HOST_PORT = 8888
SEARXNG_DOCKER_CONTAINER_PORT = 8080
SEARXNG_DOCKER_CONFIG_DIR = ".local/searxng"
SEARXNG_REQUIRE = False
```

Restart Piper after changing the backend.

These names are documented as the intended lifecycle contract for this branch. If the implementation later uses slightly different names, the docs should follow the actual code.

---

## Local Settings

Create `.local/searxng/settings.yml` for the container. This file is local-generated state and must not be committed.

```yaml
use_default_settings: true

server:
  bind_address: "0.0.0.0"
  port: 8080
  secret_key: "piper-local-test-only-change-me"
  limiter: false
  image_proxy: false

search:
  formats:
    - html
    - json
```

The JSON format must be enabled, otherwise `/search?q=test&format=json` can return `403 Forbidden`.

## Run Piper and Test

Try these queries to exercise the new resolver + backend:

- `Search online for recent developments in AI.`
- `Can you search for it please online?`
- `No, I meant latest AI news.`
- `Now search for recent models.`
- `Search for it online.`

Watch the console/logs to confirm results come from SearXNG. If SearXNG is unreachable and `SEARXNG_AUTO_START = True`, Piper may start the Docker container; if Docker is unavailable or the container fails, Piper should degrade gracefully rather than crash normal chat.

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

## Troubleshooting

- `docker : The term 'docker' is not recognized`
  - Meaning: Docker Desktop is not installed or not on `PATH`
  - Fix: install and open Docker Desktop, enable the WSL2 backend, then restart PowerShell
- `403 Forbidden` from `/search?q=test&format=json`
  - Meaning: SearXNG JSON output is not enabled or the config file was not mounted
  - Fix: ensure `.local/searxng/settings.yml` exists and the Docker run command mounts it to `/etc/searxng`
- Container already exists
  - Use `docker ps -a`
  - Stop the old container with `docker stop piper-searxng`, then start again
- Individual engines fail
  - Not fatal
  - SearXNG may report unresponsive engines while still returning results

## Manual Commands

Check Docker:

```powershell
docker --version
docker info
```

Start SearXNG manually:

```powershell
docker run --rm -d --name piper-searxng -p 8888:8080 -v C:\Projects\Piper\.local\searxng:/etc/searxng searxng/searxng:latest
```

Test SearXNG:

```powershell
Invoke-RestMethod "http://127.0.0.1:8888/search?q=test&format=json"
```

Stop SearXNG:

```powershell
docker stop piper-searxng
```
