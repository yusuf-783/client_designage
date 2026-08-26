#!/usr/bin/env bash
set -e

# Change to client directory
cd "$(dirname "$0")/.."

echo "Starting Digital Signage Client on Raspberry Pi..."

# Check virtual environment
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "../.venv" ]; then
    source ../.venv/bin/activate
fi

# Run client application
python3 -m app.main
