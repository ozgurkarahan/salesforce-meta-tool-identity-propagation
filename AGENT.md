# AGENT.md

This file provides guidance to code agents when working with this repository.

## Project Overview

**Salesforce MCP Tool** — standalone deployment of a Salesforce MCP server with identity propagation, deployed via Azure Developer CLI (`azd`) and Bicep. Extracted from the multi-tool `secu-propagate-identity` PoC to be independently deployable.

**Architecture:** Chat App (FastAPI + MSAL.js) → AI Foundry Agent → APIM (JWT validation) → Salesforce MCP Server (FastMCP) → Salesforce APIs

## Development Quick Reference

### Deploy
```bash
azd up
```

### Required Environment Variables
```bash
azd env set SF_INSTANCE_URL "https://your-org.my.salesforce.com"
azd env set SF_CONNECTED_APP_CLIENT_ID "<consumer-key>"
azd env set SF_CONNECTED_APP_CLIENT_SECRET "<consumer-secret>"
```

### Key Paths
- `src/salesforce-mcp/` — MCP server (6 tools, bearer passthrough)
- `src/chat-app/` — FastAPI + MSAL.js frontend
- `infra/` — Bicep IaC (main.bicep + modules/)
- `hooks/postprovision.py` — Entra app + Foundry agent + OAuth connection setup
- `scripts/` — Setup, consent, and test scripts

### SF Org Setup (after new Dev Trial)

After creating a new Dev Trial org and running `sf org login web`:

```bash
# One command to set up everything:
python scripts/setup-sf-org.py --org <alias> --email <admin-email>

# Or run individual steps:
python scripts/setup-sf-external-client-app.py --org <alias> --email <admin-email>
python scripts/configure-sf-connected-app.py --app-name Identity_PoC_MCP --org <alias>
python scripts/setup-sf-demo-user.py --org <alias> --email <admin-email>
```
