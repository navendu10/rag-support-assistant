# Setup on Windows 11

These steps use **PowerShell**. In Windows PowerShell 5.1, chain commands with `;`, not `&&`.

## 1. Prerequisites

- **Python 3.12** from python.org. Check with `py -3.12 --version`.
- **Docker Desktop** with the WSL 2 backend, running. Check with `docker --version`.
- **Git** and **VS Code**.
- A free **Groq API key** from <https://console.groq.com/keys>.

## 2. Virtual environment and dependencies

```powershell
cd C:\path\to\rag-support-assistant
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
pip install -r requirements-dev.txt
pre-commit install
```

If activation is blocked, allow local scripts once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## 3. Secrets

```powershell
Copy-Item .env.example .env
notepad .env
```

The file must contain a line of the form `GROQ_API_KEY=gsk_...`. `.env` is git-ignored;
never commit it.

## 4. Start Weaviate

```powershell
docker compose up -d
docker compose ps        # STATUS should become "healthy"
```

Weaviate listens on `http://localhost:8080` (REST) and `localhost:50051` (gRPC), stores
data in the named volume `weaviate_data`, and is capped at 1 GB of memory. To stop it, run
`docker compose down`. Add `-v` to also delete the stored data.

## 5. Verify

```powershell
python scripts/check_setup.py
ruff check .
pytest
```

`check_setup.py` prints a `[PASS]`/`[FAIL]` line for Python, config, Weaviate, FastEmbed
and Groq, and exits with code 1 if anything fails. The first FastEmbed run downloads the
model (about 130 MB).

## Configuration

Settings come from `configs/base.yaml` and are overridden by `.env` and then by
environment variables. Nested keys use `__`, for example:

```powershell
$env:LLM__SMALL_MODEL = "openai/gpt-oss-20b"
$env:RAG_CONFIG_FILE  = "configs/stages/s01_naive.yaml"   # use a different YAML
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| Weaviate `ConnectError` / not ready | Start Docker Desktop, then `docker compose up -d`; wait for "healthy" |
| `groq_api_key Field required` | `.env` is missing or lacks the `GROQ_API_KEY=` prefix |
| Groq `404 model_not_found` | Not every key can use every model. List yours with the `/openai/v1/models` endpoint and update `configs/base.yaml` |
| Groq `401` | Key is wrong or revoked; create a new one |
| Groq `429` | Free-tier rate limit; wait a minute |
| Hugging Face "symlinks" warning on first FastEmbed run | Harmless on Windows; enable Developer Mode to silence it |
| Port 8080 in use | Stop the other process, or change the host port in `docker-compose.yml` and `configs/base.yaml` |
