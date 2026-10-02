.PHONY: install dev lint format-check typecheck test build infra-up infra-down api-dev ai-dev web-dev

install:
	npm install --prefix apps/web
	python3 -m pip install -e services/api -e services/ai

dev: infra-up
	@echo "Start services in separate terminals with: make web-dev, make api-dev, make ai-dev"

web-dev:
	npm run dev --prefix apps/web

api-dev:
	python3 -m uvicorn app.main:app --app-dir services/api --reload --port 8000

ai-dev:
	python3 -m uvicorn app.main:app --app-dir services/ai --reload --port 8001

lint:
	npm run lint --prefix apps/web
	python3 -m ruff check services/api services/ai

format-check:
	npm run format:check --prefix apps/web
	python3 -m ruff format --check services/api services/ai

typecheck:
	npm run typecheck --prefix apps/web
	python3 -m mypy services/api/app
	python3 -m mypy services/ai/app

test:
	npm run test --prefix apps/web
	PYTHONPATH=services/api python3 -m pytest services/api/tests
	PYTHONPATH=services/ai python3 -m pytest services/ai/tests

build:
	npm run build --prefix apps/web

infra-up:
	docker compose -f infra/docker-compose.yml up -d

infra-down:
	docker compose -f infra/docker-compose.yml down
