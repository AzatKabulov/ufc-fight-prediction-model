# FightIQ Local Website Run Instructions

Use these steps when running FightIQ locally on laptop and phone.

## Current Local Links

Laptop website:

```text
http://127.0.0.1:5179
```

Phone website:

```text
http://192.168.0.4:5179
```

Backend health check:

```text
http://127.0.0.1:8010/health
```

Phone backend health check:

```text
http://192.168.0.4:8010/health
```

Important: the phone must be on the same Wi-Fi as the laptop. If the phone link does not open, Windows Firewall may be blocking Python or Node/Vite.

## Terminal 1: Run Backend

Open PowerShell and run:

```powershell
cd "C:\Users\user\Documents\ML MODEL\backend"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8010
```

Keep this terminal open.

## Terminal 2: Run Frontend

Open another PowerShell terminal and run:

```powershell
cd "C:\Users\user\Documents\ML MODEL\frontend"
$env:VITE_API_BASE_URL="http://192.168.0.4:8010"
.\node_modules\.bin\vite.cmd --host 0.0.0.0 --port 5179
```

Keep this terminal open too.

## If The Phone Link Stops Working

Your laptop IP can change. Check it with:

```powershell
ipconfig
```

Find:

```text
Wireless LAN adapter Wi-Fi
IPv4 Address
```

Then replace `192.168.0.4` in the frontend command and phone links with the new IPv4 address.

## Quick Check

Backend running:

```text
http://127.0.0.1:8010/health
```

Frontend running:

```text
http://127.0.0.1:5179
```

Phone:

```text
http://YOUR-LAPTOP-IP:5179
```

Example:

```text
http://192.168.0.4:5179
```

## Stop Servers

In each terminal, press:

```text
Ctrl + C
```
