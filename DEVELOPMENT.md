# Development

## Dev setup

```bash
git clone https://github.com/MartinHoblisch/pbf-forge.git
cd pbf-forge
docker compose up          # runs the app at http://127.0.0.1:8000
```

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
