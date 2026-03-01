// Chat App — MSAL.js authentication + Foundry agent interaction
// Fetches config from /api/config (no hardcoded IDs)

let msalInstance = null;
let currentAccount = null;
let lastResponseId = null;
let msalConfig = null;
let appInsights = null;
let pendingRetryMessage = null;
let awaitingPostConsentRetry = false;
let postConsentRetryCount = 0;
let postConsentPollCount = 0;
const MAX_CONSENT_POLLS = 4;
const CONSENT_POLL_DELAY_MS = 3000;
const sessionId = crypto.randomUUID();

// ---------------------------------------------------------------------------
// Initialization
// ---------------------------------------------------------------------------

async function initialize() {
    try {
        const resp = await fetch('/api/config');
        if (!resp.ok) {
            addSystemMessage('Failed to load configuration. Is the server configured?');
            return;
        }
        msalConfig = await resp.json();

        msalInstance = new msal.PublicClientApplication({
            auth: {
                clientId: msalConfig.clientId,
                authority: msalConfig.authority,
                redirectUri: window.location.origin,
            },
            cache: {
                cacheLocation: 'sessionStorage',
            },
        });

        await msalInstance.initialize();

        // Initialize Application Insights (if connection string provided)
        if (msalConfig.appInsightsConnectionString && window.Microsoft && window.Microsoft.ApplicationInsights) {
            var snippet = new Microsoft.ApplicationInsights.ApplicationInsights({
                config: {
                    connectionString: msalConfig.appInsightsConnectionString,
                }
            });
            appInsights = snippet.loadAppInsights();
            appInsights.trackPageView({ name: 'Chat' });
            appInsights.context.session.id = sessionId;
        }

        // Check for existing session
        const accounts = msalInstance.getAllAccounts();
        if (accounts.length > 0) {
            currentAccount = accounts[0];
            onSignedIn();
        }
    } catch (err) {
        addSystemMessage('Error initializing: ' + err.message);
    }
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

async function handleAuth() {
    if (currentAccount) {
        msalInstance.logoutPopup({ account: currentAccount });
        currentAccount = null;
        onSignedOut();
    } else {
        try {
            const result = await msalInstance.loginPopup({
                scopes: msalConfig.scopes,
            });
            currentAccount = result.account;
            if (appInsights) appInsights.trackEvent({ name: 'UserSignedIn' });
            onSignedIn();
        } catch (err) {
            if (err.errorCode !== 'user_cancelled') {
                if (appInsights) appInsights.trackException({ exception: err });
                addSystemMessage('Sign-in failed: ' + err.message);
            }
        }
    }
}

async function getAccessToken() {
    try {
        const result = await msalInstance.acquireTokenSilent({
            scopes: msalConfig.scopes,
            account: currentAccount,
        });
        return result.accessToken;
    } catch {
        const result = await msalInstance.acquireTokenPopup({
            scopes: msalConfig.scopes,
            account: currentAccount,
        });
        return result.accessToken;
    }
}

function onSignedIn() {
    document.getElementById('userInfo').textContent = currentAccount.name || currentAccount.username;
    document.getElementById('authBtn').textContent = 'Sign out';
    document.getElementById('messageInput').disabled = false;
    document.getElementById('sendBtn').disabled = false;
    document.getElementById('welcome').style.display = 'none';
}

function onSignedOut() {
    document.getElementById('userInfo').textContent = '';
    document.getElementById('authBtn').textContent = 'Sign in';
    document.getElementById('messageInput').disabled = true;
    document.getElementById('sendBtn').disabled = true;
    document.getElementById('welcome').style.display = 'flex';
    lastResponseId = null;
}

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------

async function sendMessage() {
    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    if (!message) return;

    input.value = '';
    addMessage('user', message);
    setLoading(true);

    try {
        const token = await getAccessToken();
        const resp = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: message,
                access_token: token,
                previous_response_id: lastResponseId,
                session_id: sessionId,
            }),
        });

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            const detail = err.detail || resp.statusText;
            if (isAuthError(detail)) {
                pendingRetryMessage = message;
                showReauthBanner();
                setLoading(false);
                return;
            }
            addSystemMessage('Error: ' + detail);
            setLoading(false);
            return;
        }

        const data = await resp.json();
        lastResponseId = data.response_id;
        if (appInsights) appInsights.trackEvent({
            name: 'ChatResponse',
            properties: { type: data.type, responseId: data.response_id, requestId: data.request_id }
        });
        // Store original message — consent chain may need to retry with it.
        // Reset all consent state so a fresh user message never enters the
        // silent polling loop meant only for post-consent propagation delay.
        pendingRetryMessage = message;
        postConsentRetryCount = 0;
        postConsentPollCount = 0;
        awaitingPostConsentRetry = false;
        handleResponse(data);
    } catch (err) {
        if (appInsights) appInsights.trackException({ exception: err });
        addSystemMessage('Error: ' + err.message);
    }

    setLoading(false);
}

function handleResponse(data) {
    if (data.consent_required) {
        if (awaitingPostConsentRetry && postConsentPollCount < MAX_CONSENT_POLLS) {
            // Consent was just completed but tokens haven't propagated yet.
            // Wait and retry silently instead of re-showing the banner.
            postConsentPollCount++;
            addSystemMessage('Waiting for consent to propagate... (attempt ' + postConsentPollCount + '/' + MAX_CONSENT_POLLS + ')');
            setTimeout(() => retryOriginalQuery(), CONSENT_POLL_DELAY_MS);
        } else {
            // First consent request, or all poll retries exhausted — show banner.
            postConsentPollCount = 0;
            showConsentBanner(data.consent_link);
        }
    } else if (data.approval_required) {
        addSystemMessage('Agent requesting tool access — auto-approving...');
        autoApprove(data.approval_ids.map(a => a.id));
    } else if (awaitingPostConsentRetry && pendingRetryMessage && postConsentRetryCount < 2) {
        // Consent chain completed — agent returned text without calling tools.
        // Re-send the original query so the agent uses the now-authorized MCP tools.
        awaitingPostConsentRetry = false;
        postConsentRetryCount++;
        postConsentPollCount = 0;
        retryOriginalQuery();
    } else if (data.text) {
        awaitingPostConsentRetry = false;
        postConsentPollCount = 0;
        addMessage('assistant', data.text);
    } else {
        addSystemMessage('Agent returned no text response.');
    }
}

// ---------------------------------------------------------------------------
// Consent flow
// ---------------------------------------------------------------------------

function showConsentBanner(link) {
    const banner = document.getElementById('consentBanner');
    const consentLink = document.getElementById('consentLink');
    consentLink.href = link;
    banner.classList.add('visible');
}

async function continueAfterConsent() {
    document.getElementById('consentBanner').classList.remove('visible');
    setLoading(true);

    // Signal that after the consent chain finishes, we should auto-retry
    // with the original query (pendingRetryMessage).
    awaitingPostConsentRetry = true;

    try {
        const token = await getAccessToken();
        const resp = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: 'Continue after authentication',
                access_token: token,
                previous_response_id: lastResponseId,
                session_id: sessionId,
            }),
        });

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            addSystemMessage('Error: ' + (err.detail || resp.statusText));
            setLoading(false);
            return;
        }

        const data = await resp.json();
        lastResponseId = data.response_id;
        handleResponse(data);
    } catch (err) {
        addSystemMessage('Error after consent: ' + err.message);
    }

    setLoading(false);
}

// Auto-retry with the original query after all consent rounds complete.
// Starts a FRESH conversation (no previous_response_id) so Foundry
// re-evaluates all MCP connections. Already-consented connections work
// silently; unconsented ones trigger new oauth_consent_request.
async function retryOriginalQuery() {
    setLoading(true);

    try {
        const token = await getAccessToken();
        const message = pendingRetryMessage;
        lastResponseId = null; // Fresh conversation — force full MCP re-evaluation

        const resp = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: message,
                access_token: token,
                session_id: sessionId,
            }),
        });

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            addSystemMessage('Error: ' + (err.detail || resp.statusText));
            setLoading(false);
            return;
        }

        const data = await resp.json();
        lastResponseId = data.response_id;
        handleResponse(data);
    } catch (err) {
        addSystemMessage('Error retrying query: ' + err.message);
    }

    setLoading(false);
}

// ---------------------------------------------------------------------------
// Re-authentication flow (token expiry)
// ---------------------------------------------------------------------------

function isAuthError(detail) {
    if (!detail) return false;
    const lower = detail.toLowerCase();
    return (lower.includes('401') && lower.includes('authentication')) ||
           lower.includes('tool_user_error');
}

function showReauthBanner() {
    document.getElementById('reauthBanner').classList.add('visible');
}

async function resetAndRetry() {
    document.getElementById('reauthBanner').classList.remove('visible');
    addSystemMessage('Resetting MCP connections...');
    setLoading(true);

    try {
        const token = await getAccessToken();

        // Step 1: Reset connections via managed identity
        const resetResp = await fetch('/api/reset-mcp-auth', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ access_token: token }),
        });

        if (!resetResp.ok) {
            const err = await resetResp.json().catch(() => ({ detail: resetResp.statusText }));
            addSystemMessage('Reset failed: ' + (err.detail || resetResp.statusText));
            setLoading(false);
            return;
        }

        const resetData = await resetResp.json();
        addSystemMessage('Connections reset: ' + resetData.connections.join(', '));

        // Step 2: Retry the original message — should trigger oauth_consent_request
        lastResponseId = null;
        pendingRetryMessage = pendingRetryMessage || 'List 5 Salesforce Accounts';
        const message = pendingRetryMessage;

        const resp = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: message,
                access_token: token,
                session_id: sessionId,
            }),
        });

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            addSystemMessage('Retry failed: ' + (err.detail || resp.statusText));
            setLoading(false);
            return;
        }

        const data = await resp.json();
        lastResponseId = data.response_id;
        handleResponse(data);
    } catch (err) {
        addSystemMessage('Reset error: ' + err.message);
    }

    setLoading(false);
}

// ---------------------------------------------------------------------------
// Auto-approve MCP tool calls
// ---------------------------------------------------------------------------

async function autoApprove(approvalIds) {
    setLoading(true);

    try {
        const token = await getAccessToken();
        const resp = await fetch('/api/chat/approve', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                access_token: token,
                previous_response_id: lastResponseId,
                approval_ids: approvalIds,
            }),
        });

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            addSystemMessage('Approval error: ' + (err.detail || resp.statusText));
            setLoading(false);
            return;
        }

        const data = await resp.json();
        lastResponseId = data.response_id;
        handleResponse(data);
    } catch (err) {
        addSystemMessage('Approval error: ' + err.message);
    }

    setLoading(false);
}

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------

function addMessage(role, text) {
    const container = document.getElementById('chatContainer');
    const avatar = role === 'user' ? 'U' : 'A';

    const div = document.createElement('div');
    div.className = 'message ' + role;
    div.innerHTML =
        '<div class="message-avatar">' + avatar + '</div>' +
        '<div class="message-content">' + escapeHtml(text) + '</div>';
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

function addSystemMessage(text) {
    const container = document.getElementById('chatContainer');
    const div = document.createElement('div');
    div.className = 'message system';
    div.innerHTML =
        '<div class="message-avatar">!</div>' +
        '<div class="message-content">' + escapeHtml(text) + '</div>';
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

function setLoading(visible) {
    const sendBtn = document.getElementById('sendBtn');
    const input = document.getElementById('messageInput');
    sendBtn.disabled = visible;
    input.disabled = visible;

    // Show/hide loading indicator
    let loader = document.getElementById('loadingIndicator');
    if (visible && !loader) {
        loader = document.createElement('div');
        loader.id = 'loadingIndicator';
        loader.className = 'loading visible';
        loader.innerHTML =
            '<div class="loading-dots"><span></span><span></span><span></span></div>' +
            '<span class="loading-text">Agent is thinking...</span>';
        document.getElementById('chatContainer').appendChild(loader);
        document.getElementById('chatContainer').scrollTop =
            document.getElementById('chatContainer').scrollHeight;
    } else if (!visible && loader) {
        loader.remove();
        if (currentAccount) {
            input.disabled = false;
            sendBtn.disabled = false;
        }
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
}

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

initialize();
