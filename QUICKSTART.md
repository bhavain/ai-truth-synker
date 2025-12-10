# Truth Engine - Quick Start Guide

This guide will get you from zero to running the complete "Delayed Driver" test scenario in under 10 minutes.

## Prerequisites

Ensure you have:
- ✅ Python 3.13+
- ✅ Docker & Docker Compose
- ✅ Poetry (`curl -sSL https://install.python-poetry.org | python3 -`)
- ✅ OpenAI API key
- ✅ LangSmith API key (optional but recommended)

## Step-by-Step Setup

### 1. Start Infrastructure Services

```bash
cd truth_engine
./scripts/setup_dolt.sh
```

This starts:
- **Dolt** (version-controlled SQL database) on port 3306
- **ChromaDB** (vector database) on port 8000

### 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and add your API keys:

```bash
OPENAI_API_KEY=sk-your-key-here
LANGCHAIN_API_KEY=ls__your-key-here
```

### 3. Install Python Dependencies

```bash
poetry install
```

This installs:
- FastAPI, Uvicorn
- LangChain, LangGraph, LangSmith
- OpenAI SDK
- ChromaDB, PyMySQL
- Pydantic, and other dependencies

### 4. Bootstrap the Database

```bash
poetry run python scripts/bootstrap.py
```

This will:
- Create the `hardware_sync_db` database in Dolt
- Load 4 seed entities (HB900_DRIVER, Q3_INTEGRATION_TEST, etc.)
- Load 3 dependencies (critical blockers)
- Populate ChromaDB with 5 historical context summaries

**Expected output:**
```
============================================================
BOOTSTRAPPING TRUTH ENGINE DATABASE
============================================================
...
Loaded 4 entities and 3 dependencies
Loaded 5 historical contexts
BOOTSTRAP COMPLETE
```

### 5. Start the API Server

```bash
cd backend
poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

**Expected output:**
```
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8080
```

Keep this terminal running.

### 6. Run the Test Scenario (New Terminal)

```bash
poetry run python scripts/test_scenario.py
```

This will:
1. Send 5 mock Slack messages simulating the "Delayed Driver" scenario
2. Process each through the full pipeline (Bouncer → Watcher → Arbiter → Judge)
3. Detect the dependency conflict
4. Generate a Judge verdict with evidence provenance

**Expected output:**
```
======================================================================
TRUTH ENGINE - DELAYED DRIVER SCENARIO TEST
======================================================================

Scenario: Delayed Driver - Hidden Dependency Conflict
...
[1/5] Sending message from #supply-chain...
  ✓ Status: processed
  Classification: SIGNAL

[2/5] Sending message from #avionics...
  ✓ Status: processed
  Classification: SIGNAL
  🚨 CONFLICT DETECTED!
     Verdict: CRITICAL_CONFLICT

...
CONFLICTS AND VERDICTS
======================================================================

Conflict ID: conflict_20231024_140000
Summary: Q3 Integration Test depends on H-Bridge Driver
Verdict: CRITICAL_CONFLICT
Reasoning: Supply chain reports HB-900 delayed to Oct 25 due to customs...
Recommended Action: Reschedule Integration Test to Oct 26 or expedite customs
Confidence: 95.00%
```

## Verify Results

### Check API Endpoints

```bash
# Health check
curl http://localhost:8080/health

# Project status
curl http://localhost:8080/status

# Conflicts
curl http://localhost:8080/conflicts
```

### Check Notifications

```bash
cat notifications.json | jq
```

### View LangSmith Traces

Visit: https://smith.langchain.com

Navigate to your project: `truth_engine_dev`

You'll see full traces of:
- Bouncer classification decisions
- Watcher extraction & RAG queries
- Judge reasoning with evidence provenance

## Test Scenario Explained

The "Delayed Driver" scenario simulates a real-world engineering conflict:

1. **Message 1 (supply-chain):** "H-Bridge Driver stuck in customs, 2 week delay, new ETA Oct 25"
   - Watcher extracts: HB900_DRIVER status → DELAYED, date → 2023-10-25
   - Commits update to Dolt

2. **Message 2 (avionics):** "Integration Test confirmed for Friday, Oct 15"
   - Watcher extracts: Q3_INTEGRATION_TEST date → 2023-10-15
   - Commits update to Dolt

3. **Message 3 (random):** "Who ate the last bagel?"
   - Bouncer classifies as NOISE
   - Dropped immediately

4. **Arbiter Logic Check:**
   - Queries dependencies: Q3_INTEGRATION_TEST DEPENDS ON HB900_DRIVER
   - Detects: Parent scheduled Oct 15, child available Oct 25
   - **CONFLICT DETECTED** ✅

5. **Judge Adjudication:**
   - Gathers evidence from both Slack threads
   - Queries historical context (vendor has history of customs delays)
   - Issues verdict: CRITICAL_CONFLICT
   - Recommends: Reschedule test or expedite customs
   - Notifies both teams (supply-chain + avionics)

## Architecture Validation

After running the test, you've validated:

✅ **Dual-Memory Architecture:** Dolt (facts) + ChromaDB (context) working together
✅ **Cascade Filtering:** Bouncer drops noise, Watchers extract signals
✅ **Version Control:** Every update creates a Dolt commit with provenance
✅ **RAG Context:** Watchers query historical summaries before extraction
✅ **Deterministic Arbiter:** Pure Python logic detects dependency violations
✅ **Judge Evidence Provenance:** Full citation graph in verdicts
✅ **LangSmith Observability:** Every LLM call traced and logged

## Troubleshooting

**Docker services not starting:**
```bash
docker-compose down -v  # Clean volumes
docker-compose up -d
docker-compose logs
```

**Dolt connection errors:**
```bash
docker exec -it truth_engine_dolt dolt version
# Should show Dolt version
```

**ChromaDB connection errors:**
```bash
curl http://localhost:8000/api/v1/heartbeat
# Should return: {"nanosecond heartbeat": ...}
```

**LangSmith not showing traces:**
- Check `.env` has correct `LANGCHAIN_API_KEY`
- Verify `LANGCHAIN_TRACING_V2=true`
- Check LangSmith project name matches `truth_engine_dev`

## Next Steps

Now that the MVP is running:

1. **Experiment with messages:** Modify `data/mock_slack_messages.json` and rerun
2. **Add entities:** Insert new parts/tests into seed data
3. **Tune prompts:** Edit `backend/app/agents/prompts.py`
4. **Build frontend:** Create a Streamlit dashboard (see README future enhancements)
5. **Real Slack integration:** Replace `/ingest` mock with actual Slack webhooks

## Development Commands

```bash
# Format code
poetry run black backend/

# Lint
poetry run ruff check backend/

# Type check
poetry run mypy backend/

# Run tests (when implemented)
poetry run pytest backend/tests/
```

## Questions?

Review the full Technical Design Document for architectural details.

Key files:
- `backend/app/graph/workflow.py` - LangGraph orchestration
- `backend/app/agents/` - Individual agent implementations
- `backend/app/db/` - Database clients
- `data/` - Seed data and test scenarios

Happy building! 🚀
