.PHONY: install dev-api dev-worker dev-frontend build-frontend test

install:
	python3 -m pip install -r requirements-dev.txt
	cd frontend && npm install

dev-api:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-worker:
	python3 -m app.worker

dev-frontend:
	cd frontend && npm run dev

build-frontend:
	cd frontend && npm run build

test:
	pytest -q

