# Truth Engine 🔍

> *An autonomous multi-agent system that detects hidden dependency conflicts in hardware projects by analyzing team conversations in real-time.*

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green.svg)](https://fastapi.tiangolo.com/)
[![LangChain](https://img.shields.io/badge/LangChain-0.3+-purple.svg)](https://python.langchain.com/)

---

## 🎯 Problem Statement

### The Challenge

Hardware engineering teams face a critical problem: **hidden dependency conflicts** that derail project timelines.

**Typical Scenario:**
```
[10:01 AM] Supply Chain: "Bad news - HB900 driver stuck in customs"
[10:03 AM] Supply Chain: "New ETA: November 7th (2 week delay)"
...
[2:45 PM] Avionics: "Ready to start Q3 Integration Test on October 18th!"
```

**The Problem:**
- Q3 Integration Test **depends on** HB900 Driver
- Test date (Oct 18) is now **before** part arrival (Nov 7)
- **Nobody notices the conflict** until the test date arrives
- Cascade impact: Delayed test → Delayed milestone → Delayed deliverable
- **Manual tracking** in spreadsheets becomes stale immediately

### Current Pain Points

1. **Communication Fragmentation**: Critical updates scattered across multiple Slack channels
2. **Manual Dependency Tracking**: Spreadsheets can't keep up with real-time conversations
3. **No Cascade Analysis**: Teams don't see downstream impacts of delays
4. **Missing Audit Trail**: "Who said what when?" is impossible to reconstruct
5. **Alert Fatigue**: Too many false positives from naive automation

---

## 💡 Solution: Federated Multi-Agent Architecture

Truth Engine deploys **autonomous AI agents** that monitor team conversations, extract structured updates, detect conflicts, and issue reasoned verdicts with full evidence provenance.

### Key Innovation: Dual-Memory State Machine

```
┌─────────────────────────────────────────────────────┐
│                   Truth Engine                       │
├─────────────────────────────────────────────────────┤
│                                                      │
│  ┌──────────────────┐      ┌───────────────────┐  │
│  │   Dolt Database  │      │    ChromaDB       │  │
│  │  (Factual State) │      │  (Conversational  │  │
│  │                  │      │     Context)      │  │
│  │  • Entities      │      │  • Summaries      │  │
│  │  • Dependencies  │      │  • RAG Search     │  │
│  │  • Milestones    │      │  • Embeddings     │  │
│  │  • Git-like      │      │  • Per-channel    │  │
│  │    commits       │      │    collections    │  │
│  └──────────────────┘      └───────────────────┘  │
│                                                      │
└─────────────────────────────────────────────────────┘
```

- **Dolt**: Version-controlled SQL database (like Git + MySQL) stores factual state
- **ChromaDB**: Vector database stores conversational context for RAG

### Agent Cascade: Efficient Filtering

```
 Slack Messages
      ↓
┌─────────────────┐
│  1. BOUNCER     │  GPT-4o-mini: Classify SIGNAL vs NOISE
│  (Classifier)   │  Filters social chatter
└────────┬────────┘
         │ SIGNAL only
         ↓
┌─────────────────┐
│  2. WATCHER     │  GPT-4o-mini: Extract entity updates
│  (Extractor)    │  Uses tools: query_entity, search_history
└────────┬────────┘
         │ Updates
         ↓
┌─────────────────┐
│  3. ARBITER     │  Pure Python: Deterministic conflict detection
│  (Detector)     │  Logic: parent_date < child_date → CONFLICT
└────────┬────────┘
         │ Conflicts
         ↓
┌─────────────────┐
│  4. JUDGE       │  GPT-4o: Complex reasoning with evidence
│  (Adjudicator)  │  Tools: get_dependency_tree, calculate_date
└────────┬────────┘
         │ Verdict
         ↓
┌─────────────────┐
│  5. APPROVAL    │  Human-in-the-Loop: Dashboard review
│  (HITL)         │  Approve → Apply / Reject → Notify only
└────────┬────────┘
         │ Approved
         ↓
┌─────────────────┐
│  6. NOTIFY      │  Send alerts to affected teams
└─────────────────┘
```

**Cost Optimization**: 70% cost reduction using GPT-4o-mini for filtering, GPT-4o for reasoning

---

## 🚀 Getting Started

### Prerequisites

- Docker & Docker Compose
- Python 3.13+
- Poetry
- OpenAI API Key

### Quick Setup

```bash
# 1. Clone and setup
git clone <repo_url>
cd truth_engine
cp .env.example .env
# Edit .env with OPENAI_API_KEY

# 2. Start services
docker-compose up -d

# 3. Install dependencies
poetry install && poetry shell

# 4. Bootstrap database
python -m backend.app.db.seed

# 5. Run API
uvicorn backend.app.main:app --reload --port 8080
```

### Test Conflict Scenario

```bash
curl -X POST http://localhost:8080/ingest_batch \
  -H "Content-Type: application/json" \
  -d @data/messages/scenario_hb900_delay.json

# Check pending approvals
curl http://localhost:8080/approvals?status_filter=PENDING
```

---

## 🤖 Agent Architecture

### 1. Bouncer Agent
- **Role**: SIGNAL/NOISE classifier
- **Model**: GPT-4o-mini
- **Pass Rate**: ~30% SIGNAL

### 2. Watcher Agents (Autonomous)
- **Role**: Extract structured updates
- **Model**: GPT-4o-mini with ReAct
- **Tools**: `query_entity`, `search_history`, `validate_date`, `check_dependencies`

### 3. Arbiter Agent
- **Role**: Deterministic conflict detection
- **Logic**: Pure Python (NO LLM)
- **Rule**: `parent_date < child_date → CONFLICT`

### 4. Judge Agent (⭐ Star)
- **Role**: Evidence-based adjudication
- **Model**: GPT-4o
- **Tools**: `get_dependency_tree`, `calculate_suggested_date`, `query_vector_store`

**Key Innovation: Cascade Analysis**
```
HB900_DRIVER delays
  ↓
Q3_INTEGRATION_TEST (cascade_level=0)
  ↓
Q3_DELIVERY_MILESTONE (cascade_level=1)
  ↓
Q4_FLIGHT_READINESS (cascade_level=2)
```

### 5. Human-in-the-Loop
- **Pending approvals** created for verdicts
- **Dashboard API**: Review, approve, or reject
- **Audit trail**: Full provenance in Dolt

---

## 🛠️ Tech Stack

- **Backend**: FastAPI
- **Agents**: LangChain + LangGraph
- **Database**: Dolt (version-controlled SQL)
- **Vector Store**: ChromaDB
- **LLMs**: OpenAI GPT-4o-mini, GPT-4o
- **Observability**: LangSmith

---

## 📁 Project Structure

```
truth_engine/
├── backend/app/
│   ├── agents/          # AI agent implementations
│   ├── graph/           # LangGraph workflow
│   ├── db/              # Dolt + ChromaDB clients
│   ├── models/          # Pydantic schemas
│   └── main.py          # FastAPI app
├── data/
│   ├── seed_entities.json
│   └── messages/        # Test scenarios
└── docker-compose.yml
```

---

## 🧪 Test Scenarios

### Conflict
**File**: `data/messages/scenario_hb900_delay.json`
- HB900 delays to Nov 7
- Q3_TEST blocked (Oct 18 → Nov 10)
- Creates pending approval

### Opportunity
**File**: `data/messages/scenario_hb900_dela-1.json`
- HB900 recovers to Oct 14
- Q3_TEST unblocked (Nov 10 → Oct 18)
- Suggests date rollback

---

## 🔒 Security & Compliance

- **Audit Trail**: Every update tracked in Dolt commits
- **Evidence Provenance**: Full citation graph to source messages
- **Rejection Tracking**: Reasons stored for compliance

---

## 📄 License

MIT License

---

## 📧 Contact

**Built with ❤️ for engineering teams**
