# Development

## Dev setup

```bash
git clone https://github.com/MartinHoblisch/pbf-forge.git
cd pbf-forge
./start.sh                 # start.bat on Windows; runs the app at http://127.0.0.1:8000
```

The launchers do the first-run setup (config file, data directory) that a bare
`docker compose up` skips.

For backend development without Docker:

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

## Running tests

```bash
cd backend
pytest
```

## Linting

```bash
pip install ruff
ruff check .
ruff format --check .
```
