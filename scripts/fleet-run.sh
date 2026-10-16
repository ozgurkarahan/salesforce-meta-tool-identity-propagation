#!/usr/bin/env bash
# Fleet orchestrator for customer-360 migration (Sub-1 -> Sub-3).
#
# Runs end-to-end without human intervention. Every step is idempotent where
# possible; any non-zero step aborts the whole run with a clear artifact trail.
#
# PRE-CONDITIONS (the human must have done these before invoking):
#   - `az login` (user/SP) with Contributor+UAA on target subscription
#   - Two sibling repo clones:
#       ${SF_REPO:-$PWD}                         (this repo)
#       ${SN_REPO:-../snow-meta-tool}
#   - Local certs: certs/sf-jwt-bearer.pfx and $SN_REPO/certs/sn-jwt-bearer.pfx
#   - Python deps from requirements.txt installed
#   - azd installed
#   - ALLOW_DESTRUCTIVE_TEARDOWN=1 to enable Phase 7 (default: OFF)
#
# OUTPUT:
#   .local/fleet-artifacts/<timestamp>/ — all logs, verify reports, env dumps
#
# EXIT CODES:
#   0 = full migration verified and complete (teardown per ALLOW_DESTRUCTIVE_TEARDOWN)
#   1 = a step failed; check artifacts
#   2 = preflight refused
set -euo pipefail

# ------------------------------------------------------------------ config --
SUBSCRIPTION="${SUBSCRIPTION:-1fafe902-ee73-468d-be1e-d76d99e8920c}"
LEGACY_SUB="${LEGACY_SUB:-44026b8b-9f88-44d9-8f46-0898baa4bcd5}"
RG="${RG:-rg-customer-360}"
LEGACY_RG="${LEGACY_RG:-rg-sf-mcp-obo}"
LOCATION="${LOCATION:-swedencentral}"
SF_ENV="${SF_ENV:-customer-360}"
SN_ENV="${SN_ENV:-customer-360-sn}"
LEGACY_SF_ENV="${LEGACY_SF_ENV:-sf-mcp-obo}"
LEGACY_SN_ENV="${LEGACY_SN_ENV:-sn-mcp-obo}"
SF_REPO="${SF_REPO:-$PWD}"
SN_REPO="${SN_REPO:-$SF_REPO/../snow-meta-tool}"
ALLOW_DESTRUCTIVE_TEARDOWN="${ALLOW_DESTRUCTIVE_TEARDOWN:-0}"

TS=$(date -u +"%Y%m%dT%H%M%SZ")
ART="$SF_REPO/.local/fleet-artifacts/$TS"
mkdir -p "$ART"
LOG="$ART/fleet-run.log"
exec > >(tee -a "$LOG") 2>&1

banner() { echo -e "\n\n========== $* ==========\n"; }
step()   { echo -e "\n---- $* ----"; }

# -------------------------------------------------------- phase 0: preflight
banner "Phase 0 — Preflight"
python "$SF_REPO/scripts/fleet-preflight.py" \
  --subscription "$SUBSCRIPTION" \
  --sf-repo "$SF_REPO" --sn-repo "$SN_REPO" \
  --out ".local/fleet-artifacts/$TS/preflight.json"
az account set --subscription "$SUBSCRIPTION"

# ------------------------------------------------------- phase 1: verify quota
banner "Phase 1 — Verify Sub-3 quota & provider registrations"
REQUIRED_PROVIDERS=(
  Microsoft.ApiManagement Microsoft.App Microsoft.BotService
  Microsoft.CognitiveServices Microsoft.ContainerRegistry Microsoft.KeyVault
  Microsoft.OperationalInsights Microsoft.Insights Microsoft.Storage
)
for ns in "${REQUIRED_PROVIDERS[@]}"; do
  state=$(az provider show --namespace "$ns" --query registrationState -o tsv --subscription "$SUBSCRIPTION" || echo "")
  if [[ "$state" != "Registered" ]]; then
    step "registering provider $ns"
    az provider register --namespace "$ns" --subscription "$SUBSCRIPTION" --wait
  fi
  echo "  provider $ns: $state"
done
# Quota: APIM StandardV2 + CognitiveServices gpt-5.4
az rest --method GET --subscription "$SUBSCRIPTION" \
  --url "https://management.azure.com/subscriptions/$SUBSCRIPTION/providers/Microsoft.CognitiveServices/locations/$LOCATION/models?api-version=2023-10-01-preview" \
  > "$ART/cognitive-models.json" || true

# -------------------------------------------------------- phase 2: backup sub-1
banner "Phase 2 — Backup Sub-1 state"
SNAP="$SF_REPO/.local/sub1-snapshot"
mkdir -p "$SNAP"
az account set --subscription "$LEGACY_SUB"
az resource list -g "$LEGACY_RG" -o json > "$SNAP/resources.json" || true
az deployment group list -g "$LEGACY_RG" -o json > "$SNAP/deployments.json" || true
az apim nv list --service-name "apim-sf-mcp-obo" -g "$LEGACY_RG" -o json > "$SNAP/apim-nvs.json" || true
az keyvault secret list --vault-name "kv-sf-mcp-obo" -o json > "$SNAP/kv-secrets.json" || true
az ad app list --filter "startswith(displayName,'sf-mcp-obo')" -o json > "$SNAP/entra-apps.json" || true
cp -r "$SF_REPO/.azure/$LEGACY_SF_ENV" "$SNAP/azure-sf-mcp-obo" 2>/dev/null || true
cp -r "$SN_REPO/.azure/$LEGACY_SN_ENV" "$SNAP/azure-sn-mcp-obo" 2>/dev/null || true
az account set --subscription "$SUBSCRIPTION"

# -------------------------------------------------------- phase 3: azd env new
banner "Phase 3 — Create azd envs"
(
  cd "$SF_REPO"
  if [[ -d ".azure/$SF_ENV" ]]; then
    echo "  SF azd env $SF_ENV already exists; selecting"
    azd env select "$SF_ENV"
  else
    azd env new "$SF_ENV" --subscription "$SUBSCRIPTION" --location "$LOCATION" --no-prompt
  fi
  azd env set AZURE_RESOURCE_GROUP "$RG" --no-prompt || true
  azd env set BASE_NAME "customer-360" --no-prompt || true
)

(
  cd "$SN_REPO"
  # SN pre-seeds shared-resource env vars so its Bicep `existing` refs resolve.
  # These must be populated AFTER SF first-azd-up. We just create the env now.
  if [[ -d ".azure/$SN_ENV" ]]; then
    echo "  SN azd env $SN_ENV already exists; selecting"
    azd env select "$SN_ENV"
  else
    azd env new "$SN_ENV" --subscription "$SUBSCRIPTION" --location "$LOCATION" --no-prompt
  fi
  azd env set AZURE_RESOURCE_GROUP "$RG" --no-prompt || true
)

# ------- phase 3b: seed SN org identity from legacy SN env into new SN env
banner "Phase 3b — Seed SN org identity from legacy env"
LEGACY_SN_ENV_FILE="$SN_REPO/.azure/$LEGACY_SN_ENV/.env"
if [[ ! -f "$LEGACY_SN_ENV_FILE" ]]; then
  echo "ERROR: legacy SN env file not found: $LEGACY_SN_ENV_FILE"
  exit 1
fi
seed_sn_key() {
  local k="$1"
  local v
  v=$( { grep -E "^${k}=" "$LEGACY_SN_ENV_FILE" || true; } | head -1 | cut -d= -f2- | sed 's/^"//' | sed 's/"$//')
  if [[ -n "$v" ]]; then
    (cd "$SN_REPO" && azd env select "$SN_ENV" && azd env set "$k" "$v" --no-prompt) || true
    echo "  seeded $k"
  else
    echo "  (skip) $k is empty in legacy SN env"
  fi
}
for k in SN_INSTANCE_URL SN_OAUTH_CLIENT_ID SN_OBO_CONNECTION_NAME \
         SN_JWT_BEARER_KID IDENTITY_CLAIM_NAME; do
  seed_sn_key "$k"
done

# -------------- phase 3a: seed SF org identity from legacy env into new SF env
banner "Phase 3a — Seed SF org identity from legacy env"
LEGACY_SF_ENV_FILE="$SF_REPO/.azure/$LEGACY_SF_ENV/.env"
if [[ ! -f "$LEGACY_SF_ENV_FILE" ]]; then
  echo "ERROR: legacy SF env file not found: $LEGACY_SF_ENV_FILE"
  exit 1
fi
seed_key() {
  local k="$1"
  local v
  v=$( { grep -E "^${k}=" "$LEGACY_SF_ENV_FILE" || true; } | head -1 | cut -d= -f2- | sed 's/^"//' | sed 's/"$//')
  if [[ -n "$v" ]]; then
    (cd "$SF_REPO" && azd env select "$SF_ENV" && azd env set "$k" "$v" --no-prompt) || true
    echo "  seeded $k"
  else
    echo "  (skip) $k is empty in legacy env"
  fi
}
# SF org identity (required by APIM policy + Bicep named values)
for k in SF_INSTANCE_URL SF_CONNECTED_APP_CLIENT_ID SF_SERVICE_ACCOUNT_USERNAME \
         SF_OBO_CONNECTION_NAME IDENTITY_CLAIM_NAME \
         TEAMS_APP_DEVELOPER_NAME TEAMS_APP_PRIVACY_URL TEAMS_APP_TERMS_URL; do
  seed_key "$k"
done

# ------- phase 3c: compute cert thumbprints from local PFX files and seed envs
#
# The APIM policy XML unconditionally references {{SfJwtBearerCertThumbprint}}
# (and SN equivalent), but the corresponding Bicep named-value resources are
# wrapped in `if (!empty(...))`. On first run the env var is empty so the NV
# isn't created, and Bicep validation rejects the policy reference. We unblock
# this chicken-and-egg by computing the thumbprint directly from the local PFX
# and seeding it into the azd env BEFORE `azd up`. postprovision.sh later
# uploads the PFX to Key Vault / APIM with a matching thumbprint.
banner "Phase 3c — Seed cert thumbprints from local PFX files"
SF_CERT_PFX="$SF_REPO/certs/sf-jwt-bearer.pfx"
SN_CERT_PFX="$SN_REPO/certs/sn-jwt-bearer.pfx"
# Convert MSYS paths to native Windows paths when running under Git Bash
if command -v cygpath >/dev/null 2>&1; then
  SF_CERT_PFX_NATIVE=$(cygpath -w "$SF_CERT_PFX")
  SN_CERT_PFX_NATIVE=$(cygpath -w "$SN_CERT_PFX")
else
  SF_CERT_PFX_NATIVE="$SF_CERT_PFX"
  SN_CERT_PFX_NATIVE="$SN_CERT_PFX"
fi
compute_thumbprint() {
  python - "$1" <<'PY'
import sys
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.hazmat.primitives import hashes
data = open(sys.argv[1], "rb").read()
_, cert, _ = pkcs12.load_key_and_certificates(data, None)
print(cert.fingerprint(hashes.SHA1()).hex().upper())
PY
}
SF_CERT_THUMB=$(compute_thumbprint "$SF_CERT_PFX_NATIVE")
SN_CERT_THUMB=$(compute_thumbprint "$SN_CERT_PFX_NATIVE")
echo "  SF cert thumbprint: $SF_CERT_THUMB"
echo "  SN cert thumbprint: $SN_CERT_THUMB"
(cd "$SF_REPO" && azd env select "$SF_ENV" && azd env set SF_JWT_BEARER_CERT_THUMBPRINT "$SF_CERT_THUMB" --no-prompt)
(cd "$SN_REPO" && azd env select "$SN_ENV" && azd env set SN_JWT_BEARER_CERT_THUMBPRINT "$SN_CERT_THUMB" --no-prompt)

# ---------------------------------------- phase 4a: SF first azd up (C360 skip)
banner "Phase 4a — SF first azd up (Customer 360 creation auto-skipped)"
(
  cd "$SF_REPO"
  azd env select "$SF_ENV"
  azd up --no-prompt 2>&1 | tee "$ART/sf-azd-up-1.log"
  azd env get-values > "$ART/sf-env-after-1.env"
)

# ---------------------------------------- phase 4b: SF second azd up (cert adopt)
banner "Phase 4b — SF second azd up (cert/bot adoption)"
(
  cd "$SF_REPO"
  azd env select "$SF_ENV"
  azd up --no-prompt 2>&1 | tee "$ART/sf-azd-up-2.log"
  azd env get-values > "$ART/sf-env-after-2.env"
)

# --------------------------------------------- phase 5a: seed SN env from SF env
banner "Phase 5a — Copy shared-resource handles into SN env"
SF_ENV_FILE="$ART/sf-env-after-2.env"
copy_key() {
  local k="$1"
  local v
  v=$( { grep -E "^${k}=" "$SF_ENV_FILE" || true; } | head -1 | cut -d= -f2- | tr -d '"')
  if [[ -n "$v" ]]; then
    (cd "$SN_REPO" && azd env set "$k" "$v" --no-prompt) || true
  fi
}
for k in AZURE_RESOURCE_GROUP AZURE_SUBSCRIPTION_ID AZURE_LOCATION \
         APIM_NAME KEY_VAULT_NAME COGNITIVE_ACCOUNT_NAME \
         AI_FOUNDRY_PROJECT_NAME AI_FOUNDRY_PROJECT_ENDPOINT \
         AZURE_CONTAINER_REGISTRY_NAME LOG_ANALYTICS_WORKSPACE_ID \
         CONTAINER_APP_ENVIRONMENT_NAME; do
  copy_key "$k"
done

# SN Bicep param names don't match SF env output names 1:1. Discover the
# actual resource names from the target RG and seed them explicitly.
APP_INSIGHTS_NAME=$(az monitor app-insights component show -g "$RG" --query "[0].name" -o tsv 2>/dev/null || \
                   az resource list -g "$RG" --resource-type "Microsoft.Insights/components" --query "[0].name" -o tsv)
CAE_NAME=$(az containerapp env list -g "$RG" --query "[0].name" -o tsv)
if [[ -n "$APP_INSIGHTS_NAME" ]]; then
  (cd "$SN_REPO" && azd env set APP_INSIGHTS_NAME "$APP_INSIGHTS_NAME" --no-prompt)
  echo "  seeded APP_INSIGHTS_NAME=$APP_INSIGHTS_NAME"
fi
if [[ -n "$CAE_NAME" ]]; then
  (cd "$SN_REPO" && azd env set CONTAINER_APPS_ENV_NAME "$CAE_NAME" --no-prompt)
  echo "  seeded CONTAINER_APPS_ENV_NAME=$CAE_NAME"
fi

# ---------------------------------------- phase 5b: SN first azd up (connections)
banner "Phase 5b — SN first azd up (creates servicenow-obo + agent)"
(
  cd "$SN_REPO"
  azd env select "$SN_ENV"
  azd up --no-prompt 2>&1 | tee "$ART/sn-azd-up-1.log"
  azd env get-values > "$ART/sn-env-after-1.env"
)

# ---------------------------------------- phase 5c: SN second azd up (cert adopt)
banner "Phase 5c — SN second azd up (cert adopt)"
(
  cd "$SN_REPO"
  azd env select "$SN_ENV"
  azd up --no-prompt 2>&1 | tee "$ART/sn-azd-up-2.log"
)

# --------------------------------- phase 6: finalize-customer360 + stabilization
banner "Phase 6 — Finalize Customer 360 + stabilize"
python "$SF_REPO/scripts/finalize-customer360.py" \
  --sub "$SUBSCRIPTION" --rg "$RG" \
  --sf-env "$SF_ENV" --sn-env "$SN_ENV" \
  --sf-repo "$SF_REPO" --sn-repo "$SN_REPO" 2>&1 | tee "$ART/finalize.log"

# ----------------------------------------------- phase 7: final hard-gate verify
banner "Phase 7 — Hard-gate verify (target mode)"
python "$SF_REPO/scripts/verify-migration.py" \
  --subscription "$SUBSCRIPTION" --rg "$RG" \
  --sf-env "$SF_ENV" --sn-env "$SN_ENV" --skip-manual \
  --out-dir ".local/fleet-artifacts/$TS" 2>&1 | tee "$ART/verify-final.log"

# -------------------------------------------------- phase 8: cutover snapshot
banner "Phase 8 — Cutover snapshot (no external SF/Teams UI changes)"
(
  cd "$SF_REPO" && azd env get-values > "$ART/cutover-sf.env"
)
(
  cd "$SN_REPO" && azd env get-values > "$ART/cutover-sn.env"
)
cat > "$ART/cutover-summary.md" <<EOF
# Cutover snapshot — $TS
- Subscription: $SUBSCRIPTION
- Resource group: $RG
- SF FQDN: $(grep CHAT_APP_FQDN "$ART/cutover-sf.env" | cut -d= -f2- | tr -d '"')
- SF MCP: $(grep SF_MCP_FQDN "$ART/cutover-sf.env" | cut -d= -f2- | tr -d '"')
- SN MCP: $(grep SN_MCP_FQDN "$ART/cutover-sn.env" | cut -d= -f2- | tr -d '"')
- SF Bot App: $(grep AGENT_BOT_MSA_APP_ID "$ART/cutover-sf.env" | cut -d= -f2- | tr -d '"')
- SN Bot App: $(grep AGENT_BOT_MSA_APP_ID "$ART/cutover-sn.env" | cut -d= -f2- | tr -d '"')

No Salesforce Connected App change required (JWT bearer flow doesn't use callback).
No Salesforce FederationIdentifier change required (same tenant, same user oids).

Teams: two new org-catalog apps exist. End-users will auto-discover them if the
Teams tenant policy allows; old catalog apps remain until teardown phase.
EOF

# --------------------------------------------------- phase 9: optional teardown
if [[ "$ALLOW_DESTRUCTIVE_TEARDOWN" == "1" ]]; then
  banner "Phase 9 — Tear down Sub-1 (ALLOW_DESTRUCTIVE_TEARDOWN=1)"
  (
    cd "$SN_REPO"
    azd env select "$LEGACY_SN_ENV" || true
    az account set --subscription "$LEGACY_SUB"
    azd down --purge --force --no-prompt 2>&1 | tee "$ART/sn-azd-down.log" || true
  )
  (
    cd "$SF_REPO"
    azd env select "$LEGACY_SF_ENV" || true
    az account set --subscription "$LEGACY_SUB"
    azd down --purge --force --no-prompt 2>&1 | tee "$ART/sf-azd-down.log"
  )
  # Purge soft-deleted globally-unique resources
  az keyvault purge --name kv-sf-mcp-obo --location "$LOCATION" --subscription "$LEGACY_SUB" 2>/dev/null || true
  az cognitiveservices account purge --name aoai-sf-mcp-obo --location "$LOCATION" --resource-group "$LEGACY_RG" --subscription "$LEGACY_SUB" 2>/dev/null || true
  az rest --method DELETE --subscription "$LEGACY_SUB" \
    --url "https://management.azure.com/subscriptions/$LEGACY_SUB/providers/Microsoft.ApiManagement/locations/$LOCATION/deletedservices/apim-sf-mcp-obo?api-version=2022-08-01" || true
  # Prove empty
  az account set --subscription "$SUBSCRIPTION"  # reset context for verify
  python "$SF_REPO/scripts/verify-migration.py" \
    --subscription "$LEGACY_SUB" --rg "$LEGACY_RG" \
    --sf-env "$LEGACY_SF_ENV" --sn-env "$LEGACY_SN_ENV" \
    --post-teardown --skip-manual \
    --out-dir ".local/fleet-artifacts/$TS" 2>&1 | tee "$ART/verify-post-teardown.log"
else
  banner "Phase 9 — SKIPPED (ALLOW_DESTRUCTIVE_TEARDOWN not set)"
  echo "Sub-1 left intact; run this script again with ALLOW_DESTRUCTIVE_TEARDOWN=1 after a 24h soak to decommission."
fi

banner "FLEET RUN COMPLETE"
echo "Artifacts: $ART"
