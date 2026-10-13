# Convenience targets. Everything also works with the raw commands in README.md.
.PHONY: up down logs pipeline test web-dev api-dev venv

up:            ## Build and start the whole stack (db, pipeline, api, web)
	docker compose up --build -d

down:          ## Stop the stack (data is kept in the pgdata volume)
	docker compose down

logs:          ## Follow pipeline + api logs
	docker compose logs -f pipeline api

pipeline:      ## Re-run the full pipeline now inside the running stack
	docker compose exec pipeline python -m etf_pipeline run

test:          ## Unit tests
	.venv/bin/python -m pytest -q

venv:          ## Local Python environment for development / the EDA notebook
	python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

api-dev:       ## Run the API locally with auto-reload (needs `docker compose up -d db`)
	.venv/bin/uvicorn api.main:app --reload --port 8000

web-dev:       ## Run the front-end dev server on :5173 (proxies to the API on :8000)
	cd frontend && npm install && npm run dev
