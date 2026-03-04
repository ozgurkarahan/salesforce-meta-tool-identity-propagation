// Chat App — MSAL.js authentication + Foundry agent interaction
// Fetches config from /api/config (no hardcoded IDs)

let msalInstance = null;
let currentAccount = null;
let lastResponseId = null;
let msalConfig = null;
let appInsights = null;
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
    document.getElementById('suggestions').style.display = 'flex';
}

function onSignedOut() {
    document.getElementById('userInfo').textContent = '';
    document.getElementById('authBtn').textContent = 'Sign in';
    document.getElementById('messageInput').disabled = true;
    document.getElementById('sendBtn').disabled = true;
    document.getElementById('welcome').style.display = 'flex';
    document.getElementById('suggestions').style.display = 'none';
    lastResponseId = null;
}

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------

function useSuggestion(text) {
    document.getElementById('messageInput').value = text;
    sendMessage();
}

async function sendMessage() {
    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    if (!message) return;

    input.value = '';
    document.getElementById('welcome').style.display = 'none';
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
            addSystemMessage('Error: ' + (err.detail || resp.statusText));
            setLoading(false);
            return;
        }

        const data = await resp.json();
        lastResponseId = data.response_id;
        if (appInsights) appInsights.trackEvent({
            name: 'ChatResponse',
            properties: { type: data.type, responseId: data.response_id, requestId: data.request_id }
        });
        handleResponse(data);
    } catch (err) {
        if (appInsights) appInsights.trackException({ exception: err });
        addSystemMessage('Error: ' + err.message);
    }

    setLoading(false);
}

function handleResponse(data) {
    if (data.approval_required) {
        addSystemMessage('Agent requesting tool access — auto-approving...');
        autoApprove(data.approval_ids.map(a => a.id));
    } else if (data.text) {
        addMessage('assistant', data.text);
    } else {
        addSystemMessage('Agent returned no text response.');
    }
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
