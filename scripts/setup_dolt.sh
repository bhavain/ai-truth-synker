#!/bin/bash

# Setup script for Dolt database in Docker

echo "========================================"
echo "Truth Engine - Dolt Database Setup"
echo "========================================"
echo ""

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker and try again."
    exit 1
fi

echo "✓ Docker is running"
echo ""

# Start Docker Compose services
echo "Starting Docker services (Dolt + ChromaDB)..."
cd "$(dirname "$0")/.." || exit
docker-compose up -d

echo ""
echo "Waiting for services to be healthy..."
sleep 10

# Check service health
echo ""
echo "Checking service status..."
docker-compose ps

echo ""
echo "========================================"
echo "Services are ready!"
echo "========================================"
echo ""
echo "Dolt: mysql://localhost:3306"
echo "ChromaDB: http://localhost:8000"
echo ""
echo "Next steps:"
echo "  1. Set up environment: cp .env.example .env (and add your API keys)"
echo "  2. Install dependencies: poetry install"
echo "  3. Bootstrap database: poetry run python scripts/bootstrap.py"
echo ""
