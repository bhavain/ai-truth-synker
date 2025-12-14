.PHONY: help reset bootstrap start-api start-dashboard test-workflow clean-all

help:
	@echo "Truth Engine - Available Commands"
	@echo "=================================="
	@echo "make bootstrap      - Initialize database with seed data"
	@echo "make reset          - Interactive data reset (choose scenario)"
	@echo "make start-api      - Start FastAPI server"
	@echo "make start-dashboard - Start Streamlit dashboard"
	@echo "make test-workflow  - Run batch test workflow"
	@echo "make clean-all      - Stop all services and clean data"
	@echo "make langgraph-dev  - Start LangGraph Studio dev server"

bootstrap:
	@echo "🚀 Bootstrapping database..."
	@docker-compose up -d
	@sleep 3
	@poetry run python scripts/bootstrap.py
	@echo "✅ Bootstrap complete!"

reset:
	@echo "🔄 Starting interactive reset..."
	@poetry run python scripts/reset_data.py

start-api:
	@echo "🚀 Starting API server on port 8000..."
	@cd backend && poetry run uvicorn app.main:app --port 8000 --reload

start-dashboard:
	@echo "📊 Starting Streamlit dashboard..."
	@poetry run streamlit run scripts/dashboard.py

test-workflow:
	@echo "🧪 Running batch workflow test..."
	@poetry run python scripts/test_batch_mvp.py

langgraph-dev:
	@echo "🎨 Starting LangGraph Studio..."
	@poetry run langgraph dev

clean-all:
	@echo "🧹 Cleaning all services and data..."
	@docker-compose down -v
	@rm -f notifications.json
	@rm -rf chroma_data
	@echo "✅ Cleanup complete!"
