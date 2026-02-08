# Makefile for common tasks

.PHONY: help install test lint format clean setup ui api frontend dev ui-streamlit ui-dev

help:
	@echo "Available commands:"
	@echo "  make install    - Install package and dependencies"
	@echo "  make ui         - Run FastAPI + React UI (new, recommended)"
	@echo "  make api        - Run FastAPI backend only"
	@echo "  make frontend   - Run React frontend only"
	@echo "  make stop       - Stop servers on ports 8000 and 3000"
	@echo "  make ui-streamlit - Run legacy Streamlit UI"
	@echo "  make test       - Run tests"
	@echo "  make lint       - Check code quality"
	@echo "  make format     - Format code"
	@echo "  make clean      - Clean cache and build files"
	@echo "  make setup      - Run setup script"

install:
	pip install -r requirements.txt
	pip install -e .

test:
	pytest -v

test-integration:
	@echo "Running E2E integration tests (requires INTEGRATION_TESTS=true)"
	INTEGRATION_TESTS=true pytest tests/test_integration_e2e.py -v -s

test-ui:
	@echo "Running UI integration tests (requires UI_TESTS=true)"
	@echo "This will start a Streamlit server and test the UI with Playwright"
	UI_TESTS=true pytest tests/test_ui_integration.py -v -s

test-frontend-playwright:
	@echo "Running React UI Playwright tests (requires UI_TESTS=true)"
	@echo ""
	@echo "⚠️  IMPORTANT: Both servers must be running before tests:"
	@echo "   1. Start backend:  make api (or: uvicorn api.main:app --reload --port 8000)"
	@echo "   2. Start frontend: make frontend (or: cd frontend && npm run dev)"
	@echo "   3. Then run: UI_TESTS=true pytest tests/test_frontend_playwright.py -v"
	@echo ""
	@if ! lsof -ti:8000 > /dev/null 2>&1; then \
		echo "❌ Backend server (port 8000) is NOT running!"; \
		echo "   Start it with: make api"; \
		echo ""; \
	fi
	@if ! lsof -ti:3000 > /dev/null 2>&1; then \
		echo "❌ Frontend server (port 3000) is NOT running!"; \
		echo "   Start it with: make frontend"; \
		echo ""; \
	fi
	@if lsof -ti:8000 > /dev/null 2>&1 && lsof -ti:3000 > /dev/null 2>&1; then \
		echo "✓ Both servers are running. Starting tests..."; \
		echo ""; \
		UI_TESTS=true pytest tests/test_frontend_playwright.py -v -s; \
	else \
		echo "⚠️  Please start both servers first, then run tests again."; \
		exit 1; \
	fi

test-backend-api:
	@echo "Running backend API integration tests"
	pytest tests/test_backend_api_integration.py -v

test-cache:
	@echo "Running cache system tests"
	pytest tests/test_cache_integration.py -v

test-workflow:
	@echo "Running workflow integration tests"
	pytest tests/test_workflow_integration.py -v

test-all:
	pytest -v
	@echo "\nTo run integration tests: make test-integration"
	@echo "To run UI tests: make test-ui"
	@echo "To run React UI tests: make test-frontend-playwright"

lint:
	@echo "Linting with flake8..."
	@which flake8 > /dev/null && flake8 resume_agent tests || echo "flake8 not installed, skipping"
	@echo "Type checking with mypy..."
	@which mypy > /dev/null && mypy resume_agent || echo "mypy not installed, skipping"

format:
	@echo "Formatting with black..."
	@which black > /dev/null && black resume_agent tests || echo "black not installed, skipping"

clean:
	find . -type d -name __pycache__ -exec rm -r {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -r {} + 2>/dev/null || true
	rm -rf build/ dist/ .pytest_cache/ .mypy_cache/ 2>/dev/null || true

setup:
	python scripts/setup_env.py

ui: ## Run FastAPI + React UI (new)
	@echo "Starting Resume Agent (FastAPI + React)..."
	@echo "Backend: http://localhost:8000"
	@echo "Frontend: http://localhost:3000"
	@echo ""
	@if lsof -ti:8000 > /dev/null 2>&1; then \
		echo "⚠️  WARNING: Port 8000 is already in use!"; \
		echo "   Kill existing process: lsof -ti:8000 | xargs kill -9"; \
		echo "   Or stop existing servers and try again."; \
		echo ""; \
	fi
	@if lsof -ti:3000 > /dev/null 2>&1; then \
		echo "⚠️  WARNING: Port 3000 is already in use!"; \
		echo "   Vite will automatically use the next available port (e.g., 3001)"; \
		echo "   Kill existing process: lsof -ti:3000 | xargs kill -9"; \
		echo "   Or update frontend/vite.config.js if you want a different port."; \
		echo ""; \
	fi
	@echo "Starting backend and frontend in parallel..."
	@echo "Press Ctrl+C to stop both servers"
	@echo ""
	@bash -c ' \
		set -e; \
		trap "kill 0" EXIT; \
		echo "🚀 Starting backend server..." && \
		uvicorn api.main:app --reload --host 0.0.0.0 --port 8000 > /tmp/resume-agent-backend.log 2>&1 & \
		BACKEND_PID=$$!; \
		echo "   Backend PID: $$BACKEND_PID"; \
		BACKEND_READY=0; \
		for i in {1..10}; do \
			sleep 1; \
			if ! kill -0 $$BACKEND_PID 2>/dev/null; then \
				echo "❌ Backend process died! Check /tmp/resume-agent-backend.log"; \
				cat /tmp/resume-agent-backend.log; \
				exit 1; \
			fi; \
			if curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/health 2>/dev/null | grep -q "200"; then \
				BACKEND_READY=1; \
				break; \
			fi; \
		done; \
		if [ $$BACKEND_READY -eq 0 ]; then \
			echo "⚠️  Backend process is running but not responding yet (check logs)"; \
		fi; \
		echo "✓ Backend started on http://localhost:8000"; \
		echo ""; \
		echo "🚀 Starting frontend server..." && \
		cd frontend && npm run dev > /tmp/resume-agent-frontend.log 2>&1 & \
		FRONTEND_PID=$$!; \
		echo "   Frontend PID: $$FRONTEND_PID"; \
		FRONTEND_READY=0; \
		for i in {1..15}; do \
			sleep 1; \
			if ! kill -0 $$FRONTEND_PID 2>/dev/null; then \
				echo "❌ Frontend process died! Check /tmp/resume-agent-frontend.log"; \
				cat /tmp/resume-agent-frontend.log; \
				kill $$BACKEND_PID 2>/dev/null || true; \
				exit 1; \
			fi; \
			if curl -s -o /dev/null -w "%{http_code}" http://localhost:3000 2>/dev/null | grep -q "200\|404"; then \
				FRONTEND_READY=1; \
				break; \
			fi; \
		done; \
		if [ $$FRONTEND_READY -eq 0 ]; then \
			echo "⚠️  Frontend process is running but not responding yet (check logs)"; \
		fi; \
		echo "✓ Frontend started on http://localhost:3000"; \
		echo ""; \
		echo "✅ Both servers are running!"; \
		echo "   Backend:  http://localhost:8000"; \
		echo "   Frontend: http://localhost:3000"; \
		echo ""; \
		echo "Logs:"; \
		echo "   Backend:  tail -f /tmp/resume-agent-backend.log"; \
		echo "   Frontend: tail -f /tmp/resume-agent-frontend.log"; \
		echo ""; \
		wait'

ui-streamlit: ## Run legacy Streamlit UI
	@echo "Starting Resume Agent Web UI (Streamlit - legacy)..."
	streamlit run app.py

ui-dev: ## Run legacy Streamlit UI in headless mode
	@echo "Starting Resume Agent Web UI in development mode (Streamlit - legacy)..."
	streamlit run app.py --server.headless true

api: ## Run FastAPI backend only
	@echo "Starting FastAPI backend on http://localhost:8000..."
	@if lsof -ti:8000 > /dev/null 2>&1; then \
		echo "⚠️  ERROR: Port 8000 is already in use!"; \
		echo "   Kill existing process: lsof -ti:8000 | xargs kill -9"; \
		echo "   Or use: make stop"; \
		echo "   Or use a different port: uvicorn api.main:app --reload --port 8001"; \
		exit 1; \
	fi
	uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

frontend: ## Run React frontend only
	@echo "Starting React frontend on http://localhost:3000..."
	@echo "Note: Make sure to run 'cd frontend && npm install' first if you haven't already"
	@if lsof -ti:3000 > /dev/null 2>&1; then \
		echo "⚠️  WARNING: Port 3000 is already in use!"; \
		echo "   Vite will automatically use the next available port (e.g., 3001)"; \
		echo "   Kill existing process: lsof -ti:3000 | xargs kill -9"; \
		echo ""; \
	fi
	@cd frontend && npm run dev

dev: ## Run both API and frontend (alias for 'make ui')
	@make ui

stop: ## Stop servers running on ports 8000 and 3000
	@echo "Stopping servers on ports 8000 and 3000..."
	@if lsof -ti:8000 > /dev/null 2>&1; then \
		echo "Stopping process on port 8000..."; \
		lsof -ti:8000 | xargs kill -9 2>/dev/null || true; \
		echo "✓ Port 8000 freed"; \
	else \
		echo "✓ Port 8000 is already free"; \
	fi
	@if lsof -ti:3000 > /dev/null 2>&1; then \
		echo "Stopping process on port 3000..."; \
		lsof -ti:3000 | xargs kill -9 2>/dev/null || true; \
		echo "✓ Port 3000 freed"; \
	else \
		echo "✓ Port 3000 is already free"; \
	fi
	@echo "Done!"
