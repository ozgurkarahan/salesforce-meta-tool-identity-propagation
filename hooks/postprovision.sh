#!/usr/bin/env bash
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

# Run the post-provision script (non-fatal — agent creation may need portal activation)
echo "Running post-provision script..."
python hooks/postprovision.py || echo "Post-provision script completed with warnings (see above)"
