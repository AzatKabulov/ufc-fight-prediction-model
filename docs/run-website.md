# Run FightIQ locally

Start in the cloned repository. Use two terminals: one for the backend and one for the frontend. Keep both open while using the app.

## Windows / PowerShell

### Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8010
```

Copy environment examples only on first setup; repeating the copy overwrites local settings. SQLite needs no separate database server. Startup creates tables and seeds demo records.

### Frontend

From the repository root in a second terminal:

```powershell
cd frontend
npm ci
Copy-Item .env.example .env.local
npm run dev -- --host 127.0.0.1
```

## macOS / Linux

In the first terminal, from the repository root:

```sh
cd backend
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8010
```

In the second terminal, from the repository root:

```sh
cd frontend
npm ci
cp .env.example .env.local
npm run dev -- --host 127.0.0.1
```

## Local URLs

| Service | URL |
| --- | --- |
| Dashboard | http://127.0.0.1:5179 |
| API health | http://127.0.0.1:8010/health |
| API reference | http://127.0.0.1:8010/docs |

Health should return `{"status":"ok","service":"fight-iq-api"}`. This checks the API process, not external data sources.

## Configuration

Launch the backend from `backend/` so it reads `backend/.env`. Frontend settings live in `frontend/.env.local`. Restart the relevant server after changing settings.

| Variable | Location | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | Backend | Defaults to `sqlite:///./fightiq.db`; relative paths resolve from the working directory |
| `FRONTEND_ORIGIN` | Backend | Allowed browser origin, such as `http://localhost:5179` |
| `THE_ODDS_API_KEY` | Backend | Optional provider key; leave unset until needed |
| `ADMIN_API_KEY` | Backend | Configured but not currently enforced by admin routes |
| `VITE_API_BASE_URL` | Frontend | API address reachable from the browser; the example uses `http://127.0.0.1:8010` |

Vite variables are visible in browser code. Keep provider keys in the backend environment. Environment files and local databases are ignored by Git.

A fresh checkout has sample records but no trained artifacts or historical dataset. Sync, scraping, and odds refreshes depend on source availability. The pinned requirements do not include APScheduler; the fallback registers jobs without executing periodic work.

## Access from a phone

Use a trusted local network: admin operations currently lack authentication. Connect the phone and computer to the same Wi-Fi.

1. Find the computer's local IPv4 address (`ipconfig` on Windows).
2. Replace `YOUR-LAPTOP-IP` below with that address.
3. Stop the servers and restart the backend from `backend/`:

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8010
```

4. Restart the frontend from `frontend/`:

```powershell
$env:VITE_API_BASE_URL="http://YOUR-LAPTOP-IP:8010"
npm run dev -- --host 0.0.0.0
```

On macOS/Linux, use `VITE_API_BASE_URL=http://YOUR-LAPTOP-IP:8010 npm run dev -- --host 0.0.0.0`.

5. Open `http://YOUR-LAPTOP-IP:5179` on the phone. Check `http://YOUR-LAPTOP-IP:8010/health` there too.

`localhost` on a phone refers to the phone itself. The backend allows common private IPv4 browser origins; other origins may need `FRONTEND_ORIGIN` configured. If a connection fails, check private-network firewall rules and Wi-Fi client isolation. The IP address may change after reconnecting.

To return to laptop-only use, remove the PowerShell override with `Remove-Item Env:VITE_API_BASE_URL`, then use the loopback startup commands above.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Events appear, but fight details fail | The events list has an offline fallback; details need the API. Check `/health` and `VITE_API_BASE_URL`. |
| Sample dates look unexpected | Demo dates are relative to startup/load time. Sync source-backed events for current data. |
| Browser blocks API requests | Check the backend CORS settings and frontend origin. |
| Odds refresh fails | Check provider credentials, quota, network, and event coverage. |
| Predictions use a fallback | Historical data and a suitable trained model may not be available yet. |
| Frontend opens on another port | Vite can choose another port if 5179 is busy. Free it or update the allowed origin. |
| Dependencies fail to install | Check the package error and Python/Node compatibility before changing pinned versions. |

Press `Ctrl+C` in each terminal to stop the servers.
