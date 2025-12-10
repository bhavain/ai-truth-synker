#!/usr/bin/env python3
"""Bootstrap script to initialize Truth Engine database"""

import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.db.seed import bootstrap_database

if __name__ == "__main__":
    print("=" * 70)
    print("TRUTH ENGINE - DATABASE BOOTSTRAP")
    print("=" * 70)
    print()
    print("This script will:")
    print("  1. Create the Dolt database 'hardware_sync_db'")
    print("  2. Initialize schema (project_entities, dependencies tables)")
    print("  3. Load seed entities and dependency graph")
    print("  4. Populate ChromaDB with historical context")
    print()
    print("Make sure Docker services are running:")
    print("  $ docker-compose up -d")
    print()
    input("Press Enter to continue...")
    print()

    # Run bootstrap
    data_dir = Path(__file__).parent.parent / "data"
    bootstrap_database(data_dir)

    print()
    print("=" * 70)
    print("BOOTSTRAP COMPLETE!")
    print("=" * 70)
    print()
    print("Next steps:")
    print("  1. Start the API: cd backend && poetry run uvicorn app.main:app --reload")
    print("  2. Run test scenario: poetry run python scripts/test_scenario.py")
    print()
