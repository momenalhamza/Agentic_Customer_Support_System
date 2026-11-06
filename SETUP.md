# Setup Guide
 
This guide takes a fresh machine to a fully running chat interface in
**under 30 minutes**, even if you've never set up a project like this
before. Follow the section for your operating system — the steps are
functionally identical, only the terminal commands differ.
 
If you get stuck, check the [Troubleshooting](#troubleshooting) section
near the end before asking for help — most first-run issues are covered
there.
 
---

## Before you start: what you'll need
 
Install these first. All are free.
 
| Requirement | Why | Get it |
|---|---|---|
| **Python 3.11** | Runs the backend and ingestion pipeline | [python.org/downloads](https://www.python.org/downloads/) |
| **Docker Desktop** | Runs Weaviate, Neo4j, and Redis locally | [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/) |
| **Node.js 18 or later** | Runs the frontend chat UI | [nodejs.org](https://nodejs.org/) |
| **Git** | To clone the repository | [git-scm.com](https://git-scm.com/) |
| **A free Groq API key** | Powers the agent's LLM calls | [console.groq.com](https://console.groq.com/) → sign up → "API Keys" → Create Key |
 
You'll also pick a **password for the Neo4j database** yourself in Step 2
below — any password you choose works, you're just setting it for the
first time.
 
**Optional:** a Slack Incoming Webhook URL, if you want escalations
posted to a real Slack channel. If you skip this, the system still works
completely — escalations are just logged locally instead of posted
anywhere. You can add this later without redoing any other step.
 
Once everything above is installed, open a terminal, clone the
repository, and move into it:
 
```bash
git clone <your-repository-url>
cd <repository-folder-name>
```
 
Everything below assumes you're running commands from inside this
folder.
 
---
 
## Windows
 
These exact steps were tested and verified working end-to-end.
 
**1. Create and activate a Python virtual environment**
 
Open **Git Bash** (not Command Prompt or PowerShell — if you don't have
Git Bash, it's installed automatically alongside Git for Windows) and
run:
 
```bash
py -3.11 -m venv .venv
source .venv/Scripts/activate
```
 
Your terminal prompt should now show `(.venv)` at the start of the line
— that confirms the virtual environment is active.
 
> **Using PowerShell instead of Git Bash?** Activate with
> `.venv\Scripts\Activate.ps1` instead of the `source` command above. If
> PowerShell blocks the script with an execution-policy error, run
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first,
> then try activating again.
 
**2. Install Python dependencies**
 
```bash
pip install -r requirements.txt
```
 
This installs everything the backend needs — it can take a few minutes
the first time, since it includes the machine learning libraries used
for embeddings.
 
**3. Set up your environment file**
 
```bash
cp .env.example .env
```
 
Open `.env` in any text editor and fill in:
- `GROQ_API_KEY` — paste the key you created at console.groq.com
- `NEO4J_PASSWORD` — choose any password (you're setting it for the
  first time; you'll use this same value again in Step 4)
- `SLACK_WEBHOOK_URL` — optional, leave blank if you don't have one
- `ADMIN_API_TOKEN` — not in `.env.example` by default (nothing to leave
  blank); only add a line like `ADMIN_API_TOKEN=some-random-string` to
  your own `.env` if you want to test the live-handoff "respond to
  escalation" feature (Phase D). Without it, that one endpoint fails
  closed — it's not silently open just because the variable is unset.
Save and close the file.
 
**4. Start the databases**
 
```bash
docker compose up -d weaviate neo4j redis
```
 
This downloads and starts three services in the background: Weaviate
(vector database), Neo4j (graph database), and Redis (session storage).
The first run downloads several hundred MB of Docker images — this is
normal and only happens once. Give it about 30–60 seconds after the
command finishes before moving to the next step, so the services finish
starting up.
 
**5. Load the knowledge base**
 
```bash
python -m ingestion.ingest
```
 
This reads the policy documents in `data/corpus/`, splits them into
chunks, embeds them, and loads them into Weaviate and Neo4j. You should
see log lines ending in something like `Ingestion run complete: 93
chunks added, 0 removed, version=1.` — that confirms it worked.
 
**6. Start the backend**
 
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 &
```
 
The trailing `&` runs this in the background so your terminal is free
for the next step. You should see a line like `Uvicorn running on
http://0.0.0.0:8000`.
 
**7. Start the frontend**
 
Open a **new** Git Bash terminal window (leave the first one running),
navigate back into the project folder, then:
 
```bash
cd frontend
cp .env.local.example .env.local
npm install && npm run dev
```
 
`npm install` can take a minute or two the first time.
 
**8. Open the app**
 
Visit **[http://localhost:3000](http://localhost:3000)** in your
browser. You should see the chat interface. Try sending a message like
*"What's your standard shipping time?"*
 
---
 
## macOS
 
**1. Create and activate a Python virtual environment**
 
Open **Terminal** and run:
 
```bash
python3.11 -m venv .venv
source .venv/bin/activate
```
 
Your terminal prompt should now show `(.venv)` at the start of the line.
 
> **Don't have Python 3.11?** Install it with `brew install python@3.11`
> (requires [Homebrew](https://brew.sh/)), or download it directly from
> [python.org/downloads](https://www.python.org/downloads/).
 
**2. Install Python dependencies**
 
```bash
pip install -r requirements.txt
```
 
**3. Set up your environment file**
 
```bash
cp .env.example .env
```
 
Open `.env` in any text editor (e.g. `open -e .env` or `nano .env`) and
fill in:
- `GROQ_API_KEY` — paste the key you created at console.groq.com
- `NEO4J_PASSWORD` — choose any password (you're setting it for the
  first time; you'll use this same value again in Step 4)
- `SLACK_WEBHOOK_URL` — optional, leave blank if you don't have one
- `ADMIN_API_TOKEN` — not in `.env.example` by default (nothing to leave
  blank); only add a line like `ADMIN_API_TOKEN=some-random-string` to
  your own `.env` if you want to test the live-handoff "respond to
  escalation" feature (Phase D). Without it, that one endpoint fails
  closed — it's not silently open just because the variable is unset.
Save and close the file.
 
**4. Start the databases**
 
```bash
docker compose up -d weaviate neo4j redis
```
 
Make sure Docker Desktop is running first (open it from Applications if
it isn't). The first run downloads several hundred MB of images — this
is normal and only happens once. Wait about 30–60 seconds after this
command finishes before moving on.
 
**5. Load the knowledge base**
 
```bash
python -m ingestion.ingest
```
 
Look for a log line ending in something like `Ingestion run complete: 93
chunks added, 0 removed, version=1.` to confirm success.
 
**6. Start the backend**
 
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 &
```
 
**7. Start the frontend**
 
Open a **new** Terminal tab or window (leave the first one running),
navigate back into the project folder, then:
 
```bash
cd frontend
cp .env.local.example .env.local
npm install && npm run dev
```
 
**8. Open the app**
 
Visit **[http://localhost:3000](http://localhost:3000)** in your
browser and try sending a message.
 
---
 
## Linux
 
**1. Create and activate a Python virtual environment**
 
```bash
python3.11 -m venv .venv
source .venv/bin/activate
```
 
> **Don't have Python 3.11?** On Ubuntu/Debian:
> `sudo apt update && sudo apt install python3.11 python3.11-venv`
 
Your terminal prompt should now show `(.venv)` at the start of the line.
 
**2. Install Python dependencies**
 
```bash
pip install -r requirements.txt
```
 
**3. Set up your environment file**
 
```bash
cp .env.example .env
```
 
Open `.env` in any text editor (e.g. `nano .env`) and fill in:
- `GROQ_API_KEY` — paste the key you created at console.groq.com
- `NEO4J_PASSWORD` — choose any password (you're setting it for the
  first time; you'll use this same value again in Step 4)
- `SLACK_WEBHOOK_URL` — optional, leave blank if you don't have one
- `ADMIN_API_TOKEN` — not in `.env.example` by default (nothing to leave
  blank); only add a line like `ADMIN_API_TOKEN=some-random-string` to
  your own `.env` if you want to test the live-handoff "respond to
  escalation" feature (Phase D). Without it, that one endpoint fails
  closed — it's not silently open just because the variable is unset.
Save and close the file.
 
**4. Start the databases**
 
```bash
docker compose up -d weaviate neo4j redis
```
 
If this fails with a permissions error, either run it with `sudo`, or
[add your user to the `docker` group](https://docs.docker.com/engine/install/linux-postinstall/)
so you don't need `sudo` for future commands. The first run downloads
several hundred MB of images — this is normal and only happens once.
Wait about 30–60 seconds after this command finishes before moving on.
 
**5. Load the knowledge base**
 
```bash
python -m ingestion.ingest
```
 
Look for a log line ending in something like `Ingestion run complete: 93
chunks added, 0 removed, version=1.` to confirm success.
 
**6. Start the backend**
 
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 &
```
 
**7. Start the frontend**
 
Open a **new** terminal tab or window (leave the first one running),
navigate back into the project folder, then:
 
```bash
cd frontend
cp .env.local.example .env.local
npm install && npm run dev
```
 
**8. Open the app**
 
Visit **[http://localhost:3000](http://localhost:3000)** in your
browser and try sending a message.
 
---
 
## Verifying everything is working
 
Run this in any terminal to check that all three background services
and the backend are healthy:
 
```bash
curl http://localhost:8000/health
```
 
You should get back `{"status":"ok", ...}`. If `status` says
`"degraded"` instead, the response also tells you exactly which
dependency (Weaviate, Neo4j, or Redis) isn't reachable — see
Troubleshooting below.
 
You can also confirm the knowledge base loaded correctly:
 
```bash
curl http://localhost:8000/metrics
```
 
This should return Prometheus-format text with no errors.
 
---
 
## Troubleshooting
 
**`docker compose up` fails or hangs.**
Make sure Docker Desktop (or the Docker daemon on Linux) is actually
running before this command. On Windows/macOS, open Docker Desktop from
your applications and wait until it shows "running" before retrying.
 
**`python -m ingestion.ingest` fails with a connection error.**
The databases likely haven't finished starting yet. Wait another 30
seconds and try again. You can check container status with:
```bash
docker compose ps
```
All three (`weaviate`, `neo4j`, `redis`) should show `Up` or `running`.
 
**`uvicorn` fails with "address already in use" on port 8000.**
Something is already using that port — likely a previous run that
didn't shut down cleanly. Find and stop it:
```bash
# macOS/Linux:
lsof -i :8000
kill <the PID shown>
 
# Windows (Git Bash):
netstat -ano | grep :8000
taskkill //PID <the PID shown> //F
```
 
**`GROQ_API_KEY` errors on startup.**
Double-check `.env` (not `.env.example`) has your real key with no extra
quotes or spaces, and that you saved the file after editing it.
 
**Neo4j won't accept your password / "authentication failed."**
The password in `.env`'s `NEO4J_PASSWORD` must match exactly what you
set the very first time Neo4j started. If you're re-running setup from
scratch and want to change the password, first wipe the old database
volume: `docker compose down -v`, then start again from Step 4.
 
**Frontend loads but shows a network/connection error when you send a message.**
Confirm the backend (Step 6/7 depending on your OS) is still running in
its terminal window — if that window was closed, the backend stopped.
Also confirm `frontend/.env.local` has
`NEXT_PUBLIC_API_URL=http://localhost:8000`.
 
**Still stuck?** Check [RUN.md](RUN.md) for deeper troubleshooting and
advanced usage, or reach out to the team (see [README.md](README.md)).
 
---
 
## Stopping everything
 
When you're done:
 
```bash
# Stop the frontend: press Ctrl+C in its terminal window
# Stop the backend: press Ctrl+C in its terminal window, or:
kill %1        # if you started it with the trailing `&` as shown above
 
# Stop the databases:
docker compose down          # stops containers, keeps your data
docker compose down -v       # stops containers AND deletes all data (full reset)
```