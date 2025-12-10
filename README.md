# Truth Engine MVP - Evercurrent Hardware Synchronization AI

## Overview

The Truth Engine is a federated multi-agent system that maintains a stateful digital twin of hardware development projects. It monitors communication channels, updates a versioned factual database, and autonomously detects and resolves logical conflicts between teams.

## Architecture

- **Orchestration**: LangGraph for stateful workflow management
- **Observability**: LangSmith for agent reasoning traces
- **Factual State**: Dolt (version-controlled SQL database)
- **Context Memory**: ChromaDB (vector database for semantic retrieval)
- **LLM Inference**: OpenAI GPT-4o and GPT-4o-mini

## Prerequisites

- Python 3.13+
- Docker & Docker Compose
- Poetry (Python dependency management)
- OpenAI API key
- LangSmith API key (optional, for observability)

## Setup Instructions

### 1. Install Poetry

```bash
curl -sSL https://install.python-poetry.org | python3 -
```

### 2. Clone and Install Dependencies

```bash
cd truth_engine
poetry install
```

### 3. Configure Environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 4. Start Infrastructure Services

```bash
docker-compose up -d
```

Wait for services to be healthy:
```bash
docker-compose ps
```

### 5. Initialize Database and Seed Data

```bash
poetry run python scripts/bootstrap.py
```

This will:
- Create the Dolt database `hardware_sync_db`
- Initialize tables (project_entities, dependencies)
- Load seed dependency graph
- Populate ChromaDB with historical context

### 6. Run the Backend

```bash
cd backend
poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

### 7. Test the Pipeline

```bash
# Send mock Slack messages (Delayed Driver scenario)
poetry run python scripts/test_scenario.py
```

## API Endpoints

- `POST /ingest` - Receive Slack webhook messages
- `GET /status` - Query current project state
- `GET /conflicts` - View Judge verdicts and alerts
- `GET /health` - Health check

## Project Structure

```
truth_engine/
├── backend/
│   ├── app/
│   │   ├── graph/          # LangGraph workflow
│   │   ├── agents/         # Bouncer, Watcher, Arbiter, Judge
│   │   ├── db/             # Dolt & ChromaDB clients
│   │   ├── models/         # Pydantic schemas
│   │   ├── utils/          # Utilities (notifications, etc.)
│   │   └── main.py         # FastAPI application
│   └── tests/              # Unit and integration tests
├── data/                   # Seed data and mock messages
├── scripts/                # Setup and testing scripts
└── docker-compose.yml      # Infrastructure services
```

## Agent Workflow

1. **Bouncer** - Filters noise (social/irrelevant messages)
2. **Watcher** - Extracts entities, queries context, generates SQL updates
3. **Arbiter** - Detects dependency conflicts using deterministic logic
4. **Judge** - Resolves conflicts using GPT-4o reasoning with evidence provenance

## Testing

Run the "Delayed Driver" scenario:

1. Supply Chain reports H-Bridge Driver delayed to Oct 25
2. Avionics confirms Integration Test on Oct 15
3. System detects dependency violation
4. Judge issues verdict with evidence citations

## Observability

View agent traces in LangSmith:
- Project: `truth_engine_dev`
- URL: https://smith.langchain.com

## Development

```bash
# Format code
poetry run black backend/

# Lint
poetry run ruff check backend/

# Type check
poetry run mypy backend/

# Run tests
poetry run pytest backend/tests/
```

## Future Enhancements

- Implicit dependency learning from co-occurrence patterns
- Human override mechanism for acknowledged conflicts
- Dry-run mode for validation before production
- Real Slack integration via webhooks
- Streamlit dashboard for visualization

## License

Proprietary - Evercurrent Technologies
