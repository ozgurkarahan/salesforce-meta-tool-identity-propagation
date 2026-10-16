#!/usr/bin/env bash
set -euo pipefail
set -uo pipefail

echo "=== Post-provision hook ==="

# Load azd environment variables as exports
echo "Loading azd environment variables..."
while IFS= read -r line; do
    export "$line"
done < <(azd env get-values 2>/dev/null | sed 's/^//' | tr -d '"')

# Install Python dependencies
echo "Installing Python dependencies..."
pip install --quiet -r requirements.txt

# Run the post-provision script (skills/agent/publish failures fail the hook; Teams catalog is non-fatal)
echo "Running post-provision script..."
python hooks/postprovision.py 