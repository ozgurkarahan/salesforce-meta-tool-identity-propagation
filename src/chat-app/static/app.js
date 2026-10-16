// Chat App — MSAL.js authentication + Foundry agent interaction
// Fetches config from /api/config (no hardcoded IDs)

let msalInstance = null;
let currentAccount = null;
let lastResponseId = null;
let msalConfig = null;
let appInsights = null;
let pendingApprovalIds = null;
let lastUserMessage = null;
let sessionId = crypto.randomUUID();

// Tool call history for panel + export
let toolCallHistory = [];
let totalToolCalls = 0;
let totalErrors = 0;
let totalElapsedMs = 0;

// Token usage tracking
let totalInputTokens = 0;
let totalOutputTokens = 0;
let totalTokens = 0;

// Debug panel state
let debugEventSource = null;
let debugFilter = 'all';
let debugAutoScroll = true;
let debugLogs = [];
let debugFetching = false;
let debugFetchSince = null; // ISO timestamp — Fetch only pulls logs after this

// Tool panel state (open by default, like VS Code sidebar)
let toolPanelOpen = true;
let debugPanelOpen = false;

// Session chips (persisted in sessionStorage)
let recentSessions = JSON.parse(sessionStorage.getItem('recentSessions') || '[]');

// Agent selector state
let agentsConfig = [];  // [{project, project_endpoint, agents: [{name, label, icon?}]}]
let selectedAgent = null;  // {name, label, project_endpoint}

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
            cache: { cacheLocation: 'sessionStorage' },
        });

        await msalInstance.initialize();

        // Handle redirect response (from acquireTokenRedirect fallback)
        try {
            const redirectResp = await msalInstance.handleRedirectPromise();
            if (redirectResp && redirectResp.account) {
                currentAccount = redirectResp.account;
            }
        } catch (redirectErr) {
            console.warn('Redirect handling:', redirectErr);
        }

        // Initialize Application Insights
        if (msalConfig.appInsightsConnectionString && window.Microsoft && window.Microsoft.ApplicationInsights) {
            var snippet = new Microsoft.ApplicationInsights.ApplicationInsights({
                config: { connectionString: msalConfig.appInsightsConnectionString }
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

        // Configure marked.js for safe rendering
        try {
            if (window.marked) {
                marked.setOptions({ breaks: true, gfm: true });
            }
        } catch (e) {
            console.warn('marked.js config failed:', e);
        }

        // Keyboard shortcuts
        document.addEventListener('keydown', handleGlobalKeyDown);

        // Debug panel resize
        initDebugResize();

        // Render session chips
        renderSessionChips();

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
            const result = await msalInstance.loginPopup({ scopes: msalConfig.scopes });
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
        debugLog('CHAT', 'Acquiring token silently...');
        const result = await msalInstance.acquireTokenSilent({
            scopes: msalConfig.scopes,
            account: currentAccount,
        });
        debugLog('CHAT', 'Token acquired (silent)');
        return result.accessToken;
    } catch (silentErr) {
        debugLog('CHAT', 'Silent token failed: ' + (silentErr.message || silentErr) + ' — trying redirect...');
        // Use redirect instead of popup to avoid COOP blocking issues
        await msalInstance.acquireTokenRedirect({
            scopes: msalConfig.scopes,
            account: currentAccount,
        });
        // Page will redirect — execution stops here
        throw new Error('Redirecting for token...');
    }
}

async function onSignedIn() {
    document.getElementById('userInfo').textContent = currentAccount.name || currentAccount.username;
    document.getElementById('authBtn').textContent = 'Sign out';
    document.getElementById('newChatBtn').style.display = 'inline-block';
    document.getElementById('debugToggleBtn').style.display = 'flex';
    document.getElementById('toolPanelToggleBtn').style.display = 'flex';

    // Fetch available agents and show selector or auto-select
    await loadAgents();
}

function onSignedOut() {
    document.getElementById('userInfo').textContent = '';
    document.getElementById('authBtn').textContent = 'Sign in';
    document.getElementById('messageInput').disabled = true;
    document.getElementById('sendBtn').disabled = true;
    document.getElementById('welcome').style.display = 'flex';
    const suggestions = document.getElementById('suggestions');
    if (suggestions) suggestions.style.display = 'none';
    document.getElementById('newChatBtn').style.display = 'none';
    document.getElementById('debugToggleBtn').style.display = 'none';
    document.getElementById('toolPanelToggleBtn').style.display = 'none';
    document.getElementById('smartSuggestions').style.display = 'none';
    lastResponseId = null;
    selectedAgent = null;
    agentsConfig = [];
    updateAgentBadge();
    disconnectDebugSSE();
}

// ---------------------------------------------------------------------------
// Agent selection
// ---------------------------------------------------------------------------

async function loadAgents() {
    try {
        const token = await getAccessToken();
        const resp = await fetch('/api/agents', {
            headers: { 'Authorization': 'Bearer ' + token },
        });
        if (!resp.ok) {
            // Fallback: no agent selector, enable chat directly
            selectedAgent = { name: null, label: 'Agent', project_endpoint: null };
            enableChat();
            return;
        }
        agentsConfig = await resp.json();
    } catch {
        selectedAgent = { name: null, label: 'Agent', project_endpoint: null };
        enableChat();
        return;
    }

    // Count total agents across all projects
    let totalAgents = 0;
    for (const p of agentsConfig) totalAgents += (p.agents || []).length;

    if (totalAgents <= 1) {
        // Single agent — auto-select, no UI needed
        const proj = agentsConfig[0] || {};
        const agent = (proj.agents || [])[0] || {};
        selectedAgent = {
            name: agent.name || null,
            label: agent.label || agent.name || 'Agent',
            project_endpoint: proj.project_endpoint || null,
        };
        updateAgentBadge();
        updateWelcomeForAgent();
        enableChat();
    } else {
        // Multiple agents — show selector
        renderAgentSelector();
    }
}

function _friendlyProjectName(raw) {
    if (!raw) return 'Agents';
    // Hide raw Azure resource IDs (e.g. PROJ-C544ZEGK5TVC2)
    if (/^PROJ-/i.test(raw) || /^[0-9a-f-]{20,}$/i.test(raw)) return 'AI Foundry Agents';
    return raw;
}

function renderAgentSelector() {
    const welcome = document.getElementById('welcome');
    if (!welcome) return;

    // Hide default suggestions until agent is selected
    const suggestions = document.getElementById('suggestions');
    if (suggestions) suggestions.style.display = 'none';

    // Remove existing selector if any
    const existing = document.getElementById('agentSelector');
    if (existing) existing.remove();

    const selector = document.createElement('div');
    selector.id = 'agentSelector';
    selector.className = 'agent-selector';

    // Count total agents for search bar decision
    let totalAgents = 0;
    for (const p of agentsConfig) totalAgents += (p.agents || []).length;

    let html = '<h2>Choose an Agent</h2><p>Select which assistant you want to chat with.</p>';

    // Search bar when many agents
    if (totalAgents > 6) {
        html += '<input type="text" id="agentSearch" class="agent-search" placeholder="Search agents..." oninput="filterAgentCards(this.value)" />';
    }

    html += '<div class="agent-cards">';

    for (const proj of agentsConfig) {
        const showProjectLabel = agentsConfig.length > 1;
        const friendlyName = _friendlyProjectName(proj.project);
        if (showProjectLabel) {
            html += '<div class="agent-project-label">' + escapeHtml(friendlyName) + '</div>';
        }
        for (const agent of (proj.agents || [])) {
            const meta = getAgentMeta(agent.name);
            const icon = agent.icon || meta.icon || 'bot';
            const label = agent.label || agent.name;
            html += '<button class="agent-card" data-agent-name="' + escapeAttr(label.toLowerCase()) + '" onclick="selectAgent(\'' +
                escapeAttr(agent.name) + '\', \'' +
                escapeAttr(label) + '\', \'' +
                escapeAttr(proj.project_endpoint || '') + '\')">' +
                '<div class="agent-card-icon">' + getAgentIcon(icon) + '</div>' +
                '<div class="agent-card-name">' + escapeHtml(label) + '</div>' +
            '</button>';
        }
    }
    html += '</div>';

    selector.innerHTML = html;

    // Replace welcome content with selector
    welcome.innerHTML = '';
    welcome.appendChild(selector);
}

function filterAgentCards(query) {
    const q = query.toLowerCase().trim();
    const cards = document.querySelectorAll('.agent-card');
    const labels = document.querySelectorAll('.agent-project-label');

    cards.forEach(card => {
        const name = card.getAttribute('data-agent-name') || '';
        card.style.display = (!q || name.includes(q)) ? '' : 'none';
    });

    // Hide project labels if all their cards are hidden
    labels.forEach(label => {
        let next = label.nextElementSibling;
        let anyVisible = false;
        while (next && !next.classList.contains('agent-project-label')) {
            if (next.classList.contains('agent-card') && next.style.display !== 'none') {
                anyVisible = true;
            }
            next = next.nextElementSibling;
        }
        label.style.display = anyVisible ? '' : 'none';
    });
}

// Agent metadata — enriches dynamic discovery with icons, descriptions, suggestions
const AGENT_METADATA = {
    'salesforce-assistant': {
        icon: 'cloud',
        description: 'Interrogez vos donnees CRM Salesforce en temps reel avec votre identite.',
        prompts: [
            'Affiche mes opportunites ouvertes avec leur montant',
            'Liste mes comptes avec le chiffre d\'affaires annuel',
            'Quels contacts ai-je dans le secteur bancaire ?',
            'Montre mes cases ouvertes par priorite',
        ],
    },
    'sf-assistant': {
        icon: 'cloud',
        description: 'Assistant Salesforce (version alternative).',
        prompts: [
            'Affiche mes opportunites ouvertes',
            'Liste mes comptes',
            'Cherche un contact',
            'Quels objets puis-je consulter ?',
        ],
    },
    'servicenow-assistant': {
        icon: 'snow',
        description: 'Gerez vos incidents, demandes et changements ServiceNow.',
        prompts: [
            'Quels sont mes incidents ouverts ?',
            'Affiche les demandes de changement en attente d\'approbation',
            'Cree un incident de priorite haute pour un probleme reseau',
            'Quel est le statut de mes demandes de service ?',
        ],
    },
    'customer360-assistant': {
        icon: 'globe',
        description: 'Vue unifiee de vos clients entre Salesforce (CRM) et ServiceNow (ITSM).',
        prompts: [
            'Donne-moi une vue Customer 360 pour Contoso Ltd',
            'Acme Corp signale des pannes API gateway — quel est le tableau complet ?',
            'Prepare mon appel avec Fabrikam Inc demain',
            'Quels comptes strategiques ont le plus d\'incidents ouverts ? Montre le revenu a risque',
            'Montre les incidents P1 non resolus et les cases Salesforce correles',
            'Incidents P1/P2 ServiceNow avec les comptes Salesforce impactes — classe par revenu a risque',
        ],
    },
};

function getAgentMeta(agentName) {
    return AGENT_METADATA[agentName] || {
        icon: 'bot',
        description: 'Posez vos questions a l\'agent.',
        prompts: ['Que peux-tu faire ?', 'Qui suis-je ?'],
    };
}

function getAgentSuggestions(agentName) {
    return getAgentMeta(agentName);
}

function selectAgent(name, label, projectEndpoint) {
    selectedAgent = {
        name: name,
        label: label,
        project_endpoint: projectEndpoint || null,
    };

    const info = getAgentSuggestions(name);

    // Restore welcome screen with agent-specific content
    const welcome = document.getElementById('welcome');
    if (welcome) {
        let chipsHtml = '';
        for (const prompt of info.prompts) {
            chipsHtml += '<button class="suggestion-chip" onclick="useSuggestion(this.textContent)">' + escapeHtml(prompt) + '</button>';
        }
        welcome.innerHTML =
            '<h2>' + escapeHtml(label) + '</h2>' +
            '<p>' + escapeHtml(info.description) + '</p>' +
            '<div class="suggestions" id="suggestions" style="display: flex;">' + chipsHtml + '</div>';
    }

    updateAgentBadge();
    enableChat();

    // Reset conversation for new agent
    lastResponseId = null;

    if (appInsights) appInsights.trackEvent({ name: 'AgentSelected', properties: { agent: name } });
}

function enableChat() {
    document.getElementById('messageInput').disabled = false;
    document.getElementById('sendBtn').disabled = false;
}

function updateAgentBadge() {
    const badge = document.getElementById('agentBadge');
    if (badge && selectedAgent) {
        badge.textContent = selectedAgent.label;
        badge.style.display = 'inline-block';
    }
}

function updateWelcomeForAgent() {
    const welcomeTitle = document.querySelector('#welcome h2');
    if (welcomeTitle && selectedAgent && selectedAgent.label) {
        welcomeTitle.textContent = selectedAgent.label;
    }
}

function getAgentIcon(icon) {
    const icons = {
        'cloud': '&#9729;',
        'snow': '&#10052;',
        'bot': '&#9881;',
        'star': '&#9733;',
        'data': '&#128202;',
        'globe': '&#127760;',
    };
    return icons[icon] || icons['bot'];
}

// ---------------------------------------------------------------------------
// Session management (New Chat + chips)
// ---------------------------------------------------------------------------

function startNewChat() {
    // Save current session to chips
    if (toolCallHistory.length > 0) {
        const firstMsg = toolCallHistory[0]?.userMessage || 'Chat';
        recentSessions.unshift({
            sessionId: sessionId,
            firstMessage: firstMsg.substring(0, 40),
            timestamp: Date.now(),
        });
        if (recentSessions.length > 5) recentSessions.pop();
        sessionStorage.setItem('recentSessions', JSON.stringify(recentSessions));
        renderSessionChips();
    }

    // Reset state
    lastResponseId = null;
    sessionId = crypto.randomUUID();
    toolCallHistory = [];
    totalToolCalls = 0;
    totalErrors = 0;
    totalElapsedMs = 0;
    totalInputTokens = 0;
    totalOutputTokens = 0;
    totalTokens = 0;

    // Reset token stats display
    const tokenStatsEl = document.getElementById('tokenStats');
    if (tokenStatsEl) { tokenStatsEl.innerHTML = ''; tokenStatsEl.style.display = 'none'; }

    // Clear chat UI
    const container = document.getElementById('chatContainer');
    container.innerHTML = '';
    const welcome = document.createElement('div');
    welcome.className = 'welcome';
    welcome.id = 'welcome';

    // Count total agents
    let totalAgents = 0;
    for (const p of agentsConfig) totalAgents += (p.agents || []).length;

    if (totalAgents > 1) {
        // Multi-agent: show agent selector again
        container.appendChild(welcome);
        selectedAgent = null;
        document.getElementById('messageInput').disabled = true;
        document.getElementById('sendBtn').disabled = true;
        renderAgentSelector();
        updateAgentBadge();
    } else {
        const label = selectedAgent ? selectedAgent.label : 'Agent';
        const info = selectedAgent ? getAgentSuggestions(selectedAgent.name) : { description: '', prompts: [] };
        let chipsHtml = '';
        for (const prompt of info.prompts) {
            chipsHtml += '<button class="suggestion-chip" onclick="useSuggestion(this.textContent)">' + escapeHtml(prompt) + '</button>';
        }
        welcome.innerHTML =
            '<h2>' + escapeHtml(label) + '</h2>' +
            '<p>' + escapeHtml(info.description) + '</p>' +
            '<div class="suggestions" id="suggestions" style="display: flex;">' + chipsHtml + '</div>';
        container.appendChild(welcome);
    }

    // Clear panels
    document.getElementById('smartSuggestions').style.display = 'none';
    updateToolStats();
    renderToolTimeline();
    renderPerfProfile();
    disconnectDebugSSE();

    // Update App Insights session
    if (appInsights) {
        appInsights.context.session.id = sessionId;
        appInsights.trackEvent({ name: 'NewChat' });
    }
}

function renderSessionChips() {
    const container = document.getElementById('sessionChips');
    container.innerHTML = '';
    for (const s of recentSessions) {
        const chip = document.createElement('button');
        chip.className = 'session-chip';
        const time = new Date(s.timestamp);
        const timeStr = time.getHours().toString().padStart(2, '0') + ':' + time.getMinutes().toString().padStart(2, '0');
        chip.textContent = timeStr + ' "' + s.firstMessage + '"';
        chip.title = 'Session from ' + time.toLocaleTimeString() + ': ' + s.firstMessage;
        container.appendChild(chip);
    }
}

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------

function useSuggestion(text) {
    document.getElementById('messageInput').value = text;
    sendMessage();
}

async function sendMessage(resendMessage) {
    const input = document.getElementById('messageInput');
    const isResend = typeof resendMessage === 'string';
    const message = isResend ? resendMessage : input.value.trim();
    if (!message) return;

    if (!isResend) {
        input.value = '';
        document.getElementById('welcome').style.display = 'none';
        document.getElementById('smartSuggestions').style.display = 'none';
        addMessage('user', message);
    }
    lastUserMessage = message;
    setLoading(true);
    debugLog('CHAT', 'Sending: "' + message.substring(0, 80) + '"');

    const startTime = Date.now();

    try {
        const token = await getAccessToken();
        debugLog('CHAT', 'Token acquired, calling agent...');
        const chatBody = {
            message: message,
            access_token: token,
            previous_response_id: lastResponseId,
            session_id: sessionId,
        };
        if (selectedAgent && selectedAgent.name) chatBody.agent_name = selectedAgent.name;
        if (selectedAgent && selectedAgent.project_endpoint) chatBody.project_endpoint = selectedAgent.project_endpoint;

        const resp = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(chatBody),
        });

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            debugLog('CHAT', 'Error ' + resp.status + ': ' + (err.detail || resp.statusText));
            addSystemMessage('Error: ' + (err.detail || resp.statusText));
            setLoading(false);
            return;
        }

        const data = await resp.json();
        const elapsedMs = Date.now() - startTime;
        lastResponseId = data.response_id;
        debugLog('CHAT', 'Response in ' + (elapsedMs / 1000).toFixed(1) + 's | type=' + data.type + ' | tools=' + (data.tool_calls ? data.tool_calls.length : 0));

        // Track token usage
        if (data.usage) {
            totalInputTokens += data.usage.input_tokens || 0;
            totalOutputTokens += data.usage.output_tokens || 0;
            totalTokens += data.usage.total_tokens || 0;
            debugLog('CHAT', 'Tokens: in=' + data.usage.input_tokens + ' out=' + data.usage.output_tokens + ' total=' + data.usage.total_tokens + ' | session=' + totalTokens);
            updateTokenStats();
        }

        if (appInsights) appInsights.trackEvent({
            name: 'ChatResponse',
            properties: { type: data.type, responseId: data.response_id, requestId: data.request_id }
        });

        // Track in tool call history
        if (data.tool_calls && data.tool_calls.length > 0) {
            const entry = {
                index: toolCallHistory.length,
                userMessage: message,
                timestamp: startTime,
                requestId: data.request_id,
                elapsedMs: elapsedMs,
                toolCalls: data.tool_calls.map(tc => ({
                    name: tc.name,
                    args: tc.arguments,
                    output: tc.output,
                    error: tc.error,
                })),
                agentText: data.text || '',
                usage: data.usage || null,
            };
            toolCallHistory.push(entry);
            totalToolCalls += data.tool_calls.length;
            totalErrors += data.tool_calls.filter(tc => tc.error).length;
            totalElapsedMs += elapsedMs;
            updateToolStats();
            renderToolTimeline();
            renderPerfProfile();
            updateToolBadge();
        }

        handleResponse(data);

    } catch (err) {
        if (appInsights) appInsights.trackException({ exception: err });
        addSystemMessage('Error: ' + err.message);
    }

    setLoading(false);
}

function handleResponse(data) {
    // Render completed tool calls as collapsible cards
    if (data.tool_calls && data.tool_calls.length > 0) {
        for (let i = 0; i < data.tool_calls.length; i++) {
            const tc = data.tool_calls[i];
            const tcSource = tc.name === 'memory_search' ? 'MEM' : 'MCP';
            debugLog(tcSource, 'tool=' + (tc.system ? tc.system + ':' : '') + tc.name + (tc.error ? ' ERROR: ' + tc.error : ' OK'));
            // Show usage on the last tool call card of this turn
            const isLast = i === data.tool_calls.length - 1;
            addToolCallCard(tc, isLast ? data.usage : null);
        }
    }

    if (data.consent_required) {
        setLoading(false);
        showConsentUI(data.consent || [], lastUserMessage);
    } else if (data.approval_required) {
        setLoading(false);
        showApprovalUI(data.approval_ids);
    } else if (data.text) {
        addMessage('assistant', data.text, data.usage);
        // Show smart suggestions based on tool calls
        showSmartSuggestions(data.tool_calls || []);
    } else {
        addSystemMessage('Agent returned no text response.');
    }
}

// ---------------------------------------------------------------------------
// Smart Suggestions (Phase 5)
// ---------------------------------------------------------------------------

function showSmartSuggestions(toolCalls) {
    const container = document.getElementById('smartSuggestions');
    container.innerHTML = '';

    if (!toolCalls || toolCalls.length === 0) {
        container.style.display = 'none';
        return;
    }

    const lastTool = toolCalls[toolCalls.length - 1];
    const suggestions = getSuggestionsForTool(lastTool);

    if (suggestions.length === 0) {
        container.style.display = 'none';
        return;
    }

    for (const s of suggestions) {
        const chip = document.createElement('button');
        chip.className = 'smart-chip';
        chip.textContent = s;
        chip.onclick = () => {
            document.getElementById('messageInput').value = s;
            sendMessage();
        };
        container.appendChild(chip);
    }
    container.style.display = 'flex';
}

function getSuggestionsForTool(tc) {
    if (!tc || !tc.name) return [];
    const agentName = selectedAgent ? selectedAgent.name : '';

    // ServiceNow-specific tool suggestions
    if (agentName === 'servicenow-assistant') {
        switch (tc.name) {
            case 'query_records':
                return ['Montre plus de details', 'Filtre par priorite haute', 'Exporte ces resultats'];
            case 'create_record':
                return ['Verifie la creation', 'Affiche le ticket cree'];
            case 'update_record':
                return ['Confirme la mise a jour', 'Affiche le ticket modifie'];
            case 'whoami':
                return ['Mes incidents ouverts', 'Mes demandes en cours', 'Mes approbations en attente'];
            default:
                return [];
        }
    }

    // Salesforce-specific tool suggestions (default)
    switch (tc.name) {
        case 'soql_query':
            return ['Montre plus de details', 'Exporte ces resultats', 'Mets a jour un enregistrement'];
        case 'write_record':
            return ['Verifie la modification', 'Affiche l\'enregistrement mis a jour'];
        case 'whoami':
            return ['Mes opportunites ouvertes', 'Mes cases recentes', 'Liste mes comptes'];
        case 'list_objects':
            return ['Decris Account', 'Decris Opportunity', 'Decris Contact'];
        case 'describe_object':
            return ['Interroge cet objet', 'Montre les champs obligatoires', 'Cree un nouvel enregistrement'];
        case 'search_records':
            return ['Montre les details complets', 'Mets a jour un enregistrement', 'Cherche autre chose'];
        case 'process_approval':
            return ['Montre les approbations en attente', 'Verifie le statut'];
        default:
            return [];
    }
}

// ---------------------------------------------------------------------------
// Tool call cards (completed calls — inline in chat)
// ---------------------------------------------------------------------------

function addToolCallCard(tc, usage) {
    const container = document.getElementById('chatContainer');
    const div = document.createElement('div');
    div.className = 'message tool-call';

    const isError = !!tc.error;
    const statusClass = isError ? 'status-error' : 'status-success';
    const statusText = isError ? 'Error' : 'Success';

    let argsDisplay = '';
    if (tc.arguments) {
        try {
            const parsed = typeof tc.arguments === 'string' ? JSON.parse(tc.arguments) : tc.arguments;
            argsDisplay = JSON.stringify(parsed, null, 2);
        } catch { argsDisplay = String(tc.arguments); }
    }

    let outputDisplay = '';
    if (tc.error) {
        outputDisplay = tc.error;
    } else if (tc.output) {
        outputDisplay = typeof tc.output === 'string' ? tc.output : JSON.stringify(tc.output, null, 2);
        if (outputDisplay.length > 2000) {
            outputDisplay = outputDisplay.substring(0, 2000) + '\n... (truncated)';
        }
    }

    let usageHtml = '';
    if (usage) {
        usageHtml = '<div class="tool-call-usage">' +
            '<span title="Input tokens">in: ' + formatTokenCount(usage.input_tokens || 0) + '</span>' +
            '<span title="Output tokens">out: ' + formatTokenCount(usage.output_tokens || 0) + '</span>' +
            '<span title="Total tokens this turn">total: ' + formatTokenCount(usage.total_tokens || 0) + '</span>' +
            '</div>';
    }

    div.innerHTML =
        '<div class="message-avatar tool-avatar">T</div>' +
        '<div class="message-content tool-call-content">' +
            '<div class="tool-call-header" onclick="toggleToolDetails(this)">' +
                '<span class="tool-call-expand">&#9654;</span>' +
                '<span class="tool-call-name">' + escapeHtml(toolSystemLabel(tc.system)) + escapeHtml(tc.name) + '</span>' +
                '<span class="tool-call-status ' + statusClass + '">' + statusText + '</span>' +
            '</div>' +
            '<div class="tool-call-details" style="display: none;">' +
                (argsDisplay ?
                    '<div class="tool-call-section">' +
                        '<div class="tool-call-section-label">Arguments</div>' +
                        '<pre class="tool-call-code">' + escapeHtml(argsDisplay) + '</pre>' +
                    '</div>' : '') +
                (outputDisplay ?
                    '<div class="tool-call-section">' +
                        '<div class="tool-call-section-label">Result' +
                            '<button class="tool-copy-btn" data-output="' + escapeAttr(tc.output || tc.error || '') + '" onclick="event.stopPropagation(); copyToolOutput(this, this.dataset.output)">Copy</button>' +
                        '</div>' +
                        '<pre class="tool-call-code tool-call-output">' + escapeHtml(outputDisplay) + '</pre>' +
                    '</div>' : '') +
            '</div>' +
            usageHtml +
        '</div>';

    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

function toggleToolDetails(header) {
    const details = header.nextElementSibling;
    const arrow = header.querySelector('.tool-call-expand');
    if (details.style.display === 'none') {
        details.style.display = 'block';
        arrow.innerHTML = '&#9660;';
    } else {
        details.style.display = 'none';
        arrow.innerHTML = '&#9654;';
    }
}

function copyToolOutput(btn, text) {
    navigator.clipboard.writeText(text).then(() => {
        btn.textContent = 'Copied!';
        btn.classList.add('copied');
        setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('copied'); }, 1500);
    });
}

// ---------------------------------------------------------------------------
// Tool Panel — Waterfall Timeline
// ---------------------------------------------------------------------------

function toggleToolPanel() {
    toolPanelOpen = !toolPanelOpen;
    document.querySelector('.app-body').classList.toggle('tool-panel-closed', !toolPanelOpen);
    document.getElementById('toolPanelToggleBtn').classList.toggle('active', toolPanelOpen);
}

function updateToolBadge() {
    const badge = document.getElementById('toolBadge');
    if (totalToolCalls > 0 && !toolPanelOpen) {
        badge.textContent = totalToolCalls;
        badge.style.display = 'flex';
    } else {
        badge.style.display = 'none';
    }
}

function updateToolStats() {
    const uniqueTools = new Set();
    for (const entry of toolCallHistory) {
        for (const tc of entry.toolCalls) uniqueTools.add(tc.name);
    }
    document.getElementById('statCalls').textContent = totalToolCalls + ' calls';
    document.getElementById('statTools').textContent = uniqueTools.size + ' tools';
    document.getElementById('statTime').textContent = (totalElapsedMs / 1000).toFixed(1) + 's';
    document.getElementById('statErrors').textContent = totalErrors + ' errors';
}

function formatTokenCount(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
    return String(n);
}

function updateTokenStats() {
    const el = document.getElementById('tokenStats');
    if (!el) return;
    el.innerHTML =
        '<span title="Total input tokens">in: ' + formatTokenCount(totalInputTokens) + '</span>' +
        '<span class="stat-sep">|</span>' +
        '<span title="Total output tokens">out: ' + formatTokenCount(totalOutputTokens) + '</span>' +
        '<span class="stat-sep">|</span>' +
        '<span title="Total tokens used this session">total: ' + formatTokenCount(totalTokens) + '</span>';
    el.style.display = 'flex';
}

function renderToolTimeline() {
    const container = document.getElementById('toolTimeline');
    container.innerHTML = '';

    if (toolCallHistory.length === 0) {
        container.innerHTML = '<div style="padding: 24px 16px; color: #999; text-align: center; font-size: 13px;">No tool calls yet. Send a message to see activity here.</div>';
        return;
    }

    // Find max elapsed for scaling bars
    let maxToolTime = 0;
    for (const entry of toolCallHistory) {
        const perTool = entry.elapsedMs / Math.max(entry.toolCalls.length, 1);
        if (perTool > maxToolTime) maxToolTime = perTool;
    }

    for (const entry of toolCallHistory) {
        const group = document.createElement('div');
        group.className = 'timeline-group';

        const time = new Date(entry.timestamp);
        const timeStr = time.getHours().toString().padStart(2, '0') + ':' +
                        time.getMinutes().toString().padStart(2, '0');

        const tokenLabel = entry.usage
            ? '<span class="timeline-group-tokens" title="in: ' + (entry.usage.input_tokens || 0) + ' | out: ' + (entry.usage.output_tokens || 0) + '">' + formatTokenCount(entry.usage.total_tokens || 0) + ' tok</span>'
            : '';

        group.innerHTML = '<div class="timeline-group-header">' +
            '<span class="timeline-group-time">' + timeStr + '</span>' +
            '<span class="timeline-group-msg">"' + escapeHtml(entry.userMessage.substring(0, 50)) + '"</span>' +
            tokenLabel +
        '</div>';

        const perToolMs = entry.elapsedMs / Math.max(entry.toolCalls.length, 1);

        for (let i = 0; i < entry.toolCalls.length; i++) {
            const tc = entry.toolCalls[i];
            const durationSec = (perToolMs / 1000).toFixed(1);
            const barPct = maxToolTime > 0 ? Math.max((perToolMs / maxToolTime) * 100, 5) : 50;

            let speedClass = 'speed-fast';
            if (tc.error) speedClass = 'speed-error';
            else if (perToolMs > 3000) speedClass = 'speed-slow';
            else if (perToolMs > 1000) speedClass = 'speed-medium';

            const row = document.createElement('div');
            row.className = 'timeline-entry';
            row.innerHTML =
                '<span class="timeline-tool-name">' + escapeHtml(tc.name) + '</span>' +
                '<div class="timeline-bar-container">' +
                    '<div class="timeline-bar ' + speedClass + '" style="width: ' + barPct + '%;"></div>' +
                '</div>' +
                '<span class="timeline-duration">' + durationSec + 's</span>';

            row.onclick = () => toggleTimelineDetail(row, entry, i);
            group.appendChild(row);
        }

        container.appendChild(group);
    }
}

function toggleTimelineDetail(row, entry, toolIndex) {
    const existing = row.nextElementSibling;
    if (existing && existing.classList.contains('timeline-detail')) {
        existing.remove();
        return;
    }

    // Remove any other open details in this group
    const group = row.closest('.timeline-group');
    const openDetails = group.querySelectorAll('.timeline-detail');
    openDetails.forEach(d => d.remove());

    const tc = entry.toolCalls[toolIndex];
    const detail = document.createElement('div');
    detail.className = 'timeline-detail';

    let argsHtml = '';
    if (tc.args) {
        try {
            const parsed = typeof tc.args === 'string' ? JSON.parse(tc.args) : tc.args;
            argsHtml = '<div class="timeline-detail-section">' +
                '<div class="timeline-detail-label">Arguments</div>' +
                '<pre class="timeline-detail-code">' + escapeHtml(JSON.stringify(parsed, null, 2)) + '</pre>' +
            '</div>';
        } catch { /* skip */ }
    }

    let outputHtml = '';
    const output = tc.error || tc.output;
    if (output) {
        const outputStr = typeof output === 'string' ? output : JSON.stringify(output, null, 2);
        const preview = outputStr.length > 500 ? outputStr.substring(0, 500) + '\n...' : outputStr;

        // Try to extract record count
        let countHtml = '';
        try {
            const parsed = typeof output === 'string' ? JSON.parse(output) : output;
            if (parsed && typeof parsed.totalSize === 'number') {
                countHtml = '<div class="timeline-record-count">' + parsed.totalSize + ' records</div>';
            } else if (Array.isArray(parsed)) {
                countHtml = '<div class="timeline-record-count">' + parsed.length + ' items</div>';
            }
        } catch { /* not JSON */ }

        outputHtml = '<div class="timeline-detail-section">' +
            '<div class="timeline-detail-label">Result' +
                '<button class="tool-copy-btn" onclick="event.stopPropagation(); copyToClipboard(this, ' + JSON.stringify(outputStr).replace(/'/g, "\\'") + ')">Copy</button>' +
            '</div>' +
            '<pre class="timeline-detail-code">' + escapeHtml(preview) + '</pre>' +
            countHtml +
        '</div>';
    }

    const statusBadge = tc.error ?
        '<span class="tool-call-status status-error">Error</span>' :
        '<span class="tool-call-status status-success">Success</span>';

    detail.innerHTML =
        '<div class="timeline-detail-header">' +
            '<span class="timeline-detail-name">' + escapeHtml(tc.name) + '</span>' +
            statusBadge +
        '</div>' +
        argsHtml + outputHtml;

    row.after(detail);
}

function copyToClipboard(btn, text) {
    navigator.clipboard.writeText(text).then(() => {
        btn.textContent = 'Copied!';
        btn.classList.add('copied');
        setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('copied'); }, 1500);
    });
}

// ---------------------------------------------------------------------------
// Performance Profiling (Phase 5)
// ---------------------------------------------------------------------------

function renderPerfProfile() {
    const container = document.getElementById('perfProfile');
    if (toolCallHistory.length === 0) {
        container.innerHTML = '';
        return;
    }

    // Aggregate: avg time per tool, call count
    const toolAgg = {};
    for (const entry of toolCallHistory) {
        const perToolMs = entry.elapsedMs / Math.max(entry.toolCalls.length, 1);
        for (const tc of entry.toolCalls) {
            if (!toolAgg[tc.name]) toolAgg[tc.name] = { totalMs: 0, count: 0 };
            toolAgg[tc.name].totalMs += perToolMs;
            toolAgg[tc.name].count += 1;
        }
    }

    const sorted = Object.entries(toolAgg)
        .map(([name, agg]) => ({ name, avgMs: agg.totalMs / agg.count, count: agg.count }))
        .sort((a, b) => b.count - a.count);

    const maxAvg = Math.max(...sorted.map(s => s.avgMs), 1);

    let html = '<div class="perf-title">Tool Performance</div>';
    for (const t of sorted) {
        const pct = (t.avgMs / maxAvg) * 100;
        html += '<div class="perf-row">' +
            '<span class="perf-label">' + escapeHtml(t.name) + '</span>' +
            '<div class="perf-bar-bg"><div class="perf-bar" style="width: ' + pct + '%;"></div></div>' +
            '<span class="perf-value">' + (t.avgMs / 1000).toFixed(1) + 's avg (' + t.count + ')</span>' +
        '</div>';
    }

    container.innerHTML = html;
}

// ---------------------------------------------------------------------------
// Export — Markdown
// ---------------------------------------------------------------------------

function exportMarkdown() {
    if (toolCallHistory.length === 0) return;

    const now = new Date();
    const user = currentAccount ? (currentAccount.name || currentAccount.username) : 'Unknown';

    let md = '# Salesforce MCP Chat Session\n\n';
    md += '- **User:** ' + user + '\n';
    md += '- **Date:** ' + now.toISOString().split('T')[0] + '\n';
    md += '- **Session ID:** `' + sessionId + '`\n';
    md += '- **Stats:** ' + totalToolCalls + ' tool calls | ' + (totalElapsedMs / 1000).toFixed(1) + 's total | ' + totalErrors + ' errors\n\n';
    md += '---\n\n';

    for (const entry of toolCallHistory) {
        const time = new Date(entry.timestamp);
        const timeStr = time.toLocaleTimeString();
        md += '## ' + timeStr + ' — "' + entry.userMessage + '"\n\n';

        for (const tc of entry.toolCalls) {
            md += '### ' + tc.name + (tc.error ? ' (ERROR)' : '') + '\n\n';
            if (tc.args) {
                try {
                    const parsed = typeof tc.args === 'string' ? JSON.parse(tc.args) : tc.args;
                    md += '**Arguments:**\n```json\n' + JSON.stringify(parsed, null, 2) + '\n```\n\n';
                } catch { /* skip */ }
            }
            if (tc.output || tc.error) {
                const out = tc.error || tc.output;
                const outStr = typeof out === 'string' ? out : JSON.stringify(out, null, 2);
                md += '**Result:**\n```\n' + outStr.substring(0, 3000) + '\n```\n\n';
            }
        }

        if (entry.agentText) {
            md += '**Agent Response:**\n\n' + entry.agentText + '\n\n';
        }
        md += '---\n\n';
    }

    downloadFile('session-' + sessionId.substring(0, 8) + '.md', md, 'text/markdown');
}

// ---------------------------------------------------------------------------
// Export — HTML Replay
// ---------------------------------------------------------------------------

function exportHtmlReplay() {
    if (toolCallHistory.length === 0) return;

    const user = currentAccount ? (currentAccount.name || currentAccount.username) : 'Unknown';
    const date = new Date().toISOString().split('T')[0];

    // Build conversation data for the replay
    const replayData = toolCallHistory.map(entry => ({
        time: new Date(entry.timestamp).toLocaleTimeString(),
        userMessage: entry.userMessage,
        tools: entry.toolCalls.map(tc => ({
            name: tc.name,
            error: !!tc.error,
            durationSec: (entry.elapsedMs / Math.max(entry.toolCalls.length, 1) / 1000).toFixed(1),
        })),
        agentText: entry.agentText,
        elapsedMs: entry.elapsedMs,
    }));

    const maxTime = Math.max(...toolCallHistory.map(e => e.elapsedMs / Math.max(e.toolCalls.length, 1)), 1);

    // Sanitize JSON to prevent </script> injection in embedded script block
    const safeJson = JSON.stringify(replayData).replace(/<\//g, '<\\/');

    const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Chat Replay - ${escapeHtml(date)}</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#f5f5f5;color:#1a1a1a;max-width:800px;margin:0 auto;padding:24px}
.meta{background:#0078d4;color:white;padding:16px 20px;border-radius:8px;margin-bottom:24px}
.meta h1{font-size:18px;margin-bottom:4px}
.meta p{font-size:13px;opacity:.85}
.exchange{margin-bottom:20px;opacity:0;transform:translateY(10px);transition:opacity .4s,transform .4s}
.exchange.visible{opacity:1;transform:none}
.user-msg{background:#e8f0fe;padding:12px 16px;border-radius:8px;margin-bottom:8px;font-size:14px}
.user-label{font-size:11px;font-weight:600;color:#0078d4;margin-bottom:4px}
.tool-row{display:flex;align-items:center;gap:8px;margin:4px 0;padding:4px 0}
.tool-name{font-family:'Cascadia Code',Consolas,monospace;font-size:12px;width:120px;flex-shrink:0}
.bar-bg{flex:1;height:14px;background:#e0e0e0;border-radius:3px;overflow:hidden}
.bar{height:100%;border-radius:3px;width:0;transition:width .8s ease-out}
.bar.fast{background:#107c10}.bar.medium{background:#0078d4}.bar.slow{background:#ff8c00}.bar.error{background:#d13438}
.dur{font-size:11px;color:#888;width:40px;text-align:right;font-family:'Cascadia Code',Consolas,monospace}
.agent-msg{background:white;padding:12px 16px;border-radius:8px;margin-top:8px;font-size:14px;white-space:pre-wrap;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.agent-label{font-size:11px;font-weight:600;color:#107c10;margin-bottom:4px}
.stats{text-align:center;color:#888;font-size:13px;margin-top:24px;padding-top:16px;border-top:1px solid #e0e0e0}
button.replay-btn{display:block;margin:20px auto;padding:10px 24px;background:#0078d4;color:white;border:none;border-radius:6px;font-size:14px;cursor:pointer}
button.replay-btn:hover{background:#106ebe}
</style>
</head>
<body>
<div class="meta">
<h1>Salesforce MCP Chat Replay</h1>
<p>${escapeHtml(user)} | ${escapeHtml(date)} | ${totalToolCalls} tool calls | ${(totalElapsedMs/1000).toFixed(1)}s total</p>
</div>
<button class="replay-btn" onclick="startReplay()">Play Replay</button>
<div id="exchanges"></div>
<div class="stats">${totalToolCalls} tool calls | ${totalErrors} errors | ${(totalElapsedMs/1000).toFixed(1)}s total</div>
<script>
var data=${safeJson};
var maxTime=${maxTime};
function startReplay(){
var c=document.getElementById('exchanges');c.innerHTML='';
var btn=document.querySelector('.replay-btn');btn.style.display='none';
var delay=0;
data.forEach(function(ex,i){
var div=document.createElement('div');div.className='exchange';
var toolsHtml='';
ex.tools.forEach(function(t){
var pct=Math.max((parseFloat(t.durationSec)*1000/maxTime)*100,5);
var cls=t.error?'error':parseFloat(t.durationSec)>3?'slow':parseFloat(t.durationSec)>1?'medium':'fast';
toolsHtml+='<div class="tool-row"><span class="tool-name">'+t.name+'</span><div class="bar-bg"><div class="bar '+cls+'" data-pct="'+pct+'"></div></div><span class="dur">'+t.durationSec+'s</span></div>';
});
div.innerHTML='<div class="user-label">User</div><div class="user-msg">'+ex.userMessage.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')+'</div>'+toolsHtml+(ex.agentText?'<div class="agent-label">Agent</div><div class="agent-msg">'+ex.agentText.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')+'</div>':'');
c.appendChild(div);
setTimeout(function(){
div.classList.add('visible');
div.querySelectorAll('.bar').forEach(function(b){setTimeout(function(){b.style.width=b.dataset.pct+'%'},200)});
},delay);
delay+=800+ex.tools.length*300;
});
setTimeout(function(){btn.textContent='Replay Again';btn.style.display='block'},delay+500);
}
startReplay();
</script>
</body>
</html>`;

    downloadFile('replay-' + sessionId.substring(0, 8) + '.html', html, 'text/html');
}

function downloadFile(filename, content, mimeType) {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Debug Panel — App Insights Log Tail (Phase 3 frontend)
// ---------------------------------------------------------------------------

function debugLog(source, message) {
    addDebugLogEntry({
        timestamp: new Date().toISOString(),
        source: source,
        message: message,
    });
}

async function fetchDebugLogs() {
    if (debugFetching) return;
    debugFetching = true;
    debugLog('CHAT', 'Fetching server logs...');
    try {
        let url = '/api/debug/logs/snapshot?session_id=' + encodeURIComponent(sessionId);
        if (debugFetchSince) url += '&since=' + encodeURIComponent(debugFetchSince);
        const resp = await fetch(url);
        if (!resp.ok) {
            debugLog('CHAT', 'Fetch failed: ' + resp.status + ' ' + (await resp.text()));
            return;
        }
        const logs = await resp.json();
        if (logs.length === 0) {
            debugLog('CHAT', 'No server logs yet (App Insights ingestion takes 2-5 min)');
        } else {
            // Track latest timestamp to avoid duplicates on next Fetch
            let maxTs = debugFetchSince || '';
            for (const log of logs) {
                addDebugLogEntry(log);
                if (log.timestamp && log.timestamp > maxTs) maxTs = log.timestamp;
            }
            if (maxTs) debugFetchSince = maxTs;
        }
    } catch (e) {
        debugLog('CHAT', 'Fetch error: ' + e.message);
    } finally {
        debugFetching = false;
    }
}

function toggleDebugPanel() {
    debugPanelOpen = !debugPanelOpen;
    const panel = document.getElementById('debugPanel');
    const btn = document.getElementById('debugToggleBtn');

    if (debugPanelOpen) {
        panel.style.display = 'flex';
        document.body.classList.add('debug-open');
        btn.classList.add('active');
        // Restore height from sessionStorage
        const savedHeight = sessionStorage.getItem('debugPanelHeight');
        if (savedHeight) panel.style.height = savedHeight;
        // Don't auto-connect SSE — user clicks Fetch for server logs
    } else {
        panel.style.display = 'none';
        document.body.classList.remove('debug-open');
        btn.classList.remove('active');
        disconnectDebugSSE();
    }
}

function connectDebugSSE() {
    if (debugEventSource) return;
    debugEventSource = new EventSource('/api/debug/logs?session_id=' + encodeURIComponent(sessionId));
    debugEventSource.onmessage = function(event) {
        try {
            const log = JSON.parse(event.data);
            addDebugLogEntry(log);
        } catch { /* skip malformed */ }
    };
    debugEventSource.onerror = function() {
        // Silently reconnect — SSE auto-reconnects
    };
}

function disconnectDebugSSE() {
    if (debugEventSource) {
        debugEventSource.close();
        debugEventSource = null;
    }
}

function addDebugLogEntry(log) {
    debugLogs.push(log);
    const container = document.getElementById('debugLogs');

    const entry = document.createElement('div');
    const source = (log.source || 'unknown').toLowerCase();
    const msg = (log.message || '').toLowerCase();

    // Classify by AppRoleName first, then by message content for chat-app traces
    let sourceClass = 'source-chat';
    let sourceLabel = 'CHAT';
    if (source.includes('mcp') || source.includes('salesforce-mcp')) {
        sourceClass = 'source-mcp'; sourceLabel = 'MCP';
    } else if (source.includes('apim')) {
        sourceClass = 'source-apim'; sourceLabel = 'APIM';
    } else if (msg.includes('memory_call') || msg.includes('memory_search')) {
        sourceClass = 'source-memory'; sourceLabel = 'MEM';
    } else if (msg.includes('tool_call ') || msg.includes('tool=') || msg.includes('mcp_call') || msg.includes('mcp_list_tools')) {
        sourceClass = 'source-mcp'; sourceLabel = 'MCP';
    } else if (msg.includes('agent_call') || msg.includes('agent_output') || msg.includes('agent_response') || msg.includes('responses "http')) {
        sourceClass = 'source-foundry'; sourceLabel = 'AGNT';
    }

    entry.className = 'debug-log-entry ' + sourceClass;
    entry.dataset.source = sourceLabel.toLowerCase();
    entry.dataset.message = (log.message || '').toLowerCase();

    let ts = '';
    if (log.timestamp) {
        const d = new Date(log.timestamp);
        ts = d.toLocaleTimeString('en-GB', { hour12: false }) + '.' + String(d.getMilliseconds()).padStart(3, '0');
    }
    entry.innerHTML =
        '<span class="debug-log-ts">' + ts + '</span>' +
        '<span class="debug-log-source">' + sourceLabel + '</span>' +
        '<span>' + escapeHtml(log.message || JSON.stringify(log)) + '</span>';

    // Apply current filters
    const search = (document.getElementById('debugSearch').value || '').toLowerCase();
    const sourceMatch = debugFilter === 'all' || entry.dataset.source === debugFilter;
    const searchMatch = !search || entry.dataset.message.includes(search);
    entry.classList.toggle('debug-log-hidden', !(sourceMatch && searchMatch));
    container.appendChild(entry);

    if (debugAutoScroll) {
        container.scrollTop = container.scrollHeight;
    }
}

function setDebugFilter(filter, tabBtn) {
    debugFilter = filter;
    // Update tab UI
    document.querySelectorAll('.debug-tab').forEach(t => t.classList.remove('active'));
    if (tabBtn) tabBtn.classList.add('active');
    filterDebugLogs();
}

function filterDebugLogs() {
    const search = (document.getElementById('debugSearch').value || '').toLowerCase();
    const entries = document.querySelectorAll('.debug-log-entry');
    entries.forEach(entry => {
        const sourceMatch = debugFilter === 'all' || entry.dataset.source === debugFilter;
        const searchMatch = !search || entry.dataset.message.includes(search) || entry.textContent.toLowerCase().includes(search);
        entry.classList.toggle('debug-log-hidden', !(sourceMatch && searchMatch));
    });
}

function toggleDebugAutoScroll() {
    debugAutoScroll = !debugAutoScroll;
    document.getElementById('debugAutoScrollBtn').classList.toggle('active', debugAutoScroll);
}

function clearDebugLogs() {
    debugLogs = [];
    document.getElementById('debugLogs').innerHTML = '';
    debugFetchSince = new Date().toISOString(); // Only fetch logs after this point
}

function initDebugResize() {
    const handle = document.getElementById('debugResizeHandle');
    const panel = document.getElementById('debugPanel');
    let startY, startHeight;

    handle.addEventListener('mousedown', function(e) {
        startY = e.clientY;
        startHeight = panel.offsetHeight;
        document.addEventListener('mousemove', onResize);
        document.addEventListener('mouseup', stopResize);
        e.preventDefault();
    });

    function onResize(e) {
        const newHeight = Math.max(100, Math.min(startHeight + (startY - e.clientY), window.innerHeight - 100));
        panel.style.height = newHeight + 'px';
        document.body.style.setProperty('--debug-height', newHeight + 'px');
    }

    function stopResize() {
        document.removeEventListener('mousemove', onResize);
        document.removeEventListener('mouseup', stopResize);
        sessionStorage.setItem('debugPanelHeight', panel.style.height);
    }
}

// ---------------------------------------------------------------------------
// Interactive approval UI
// ---------------------------------------------------------------------------

function showApprovalUI(approvalIds) {
    pendingApprovalIds = approvalIds;
    const container = document.getElementById('chatContainer');

    const div = document.createElement('div');
    div.className = 'message approval';
    div.id = 'approvalCard';

    let toolsHtml = '';
    for (const a of approvalIds) {
        let argsDisplay = '';
        if (a.arguments) {
            try {
                const parsed = typeof a.arguments === 'string' ? JSON.parse(a.arguments) : a.arguments;
                argsDisplay = JSON.stringify(parsed, null, 2);
            } catch { argsDisplay = String(a.arguments); }
        }

        toolsHtml +=
            '<div class="approval-tool">' +
                '<div class="approval-tool-name">' + escapeHtml(a.name) + '</div>' +
                (argsDisplay ? '<pre class="tool-call-code">' + escapeHtml(argsDisplay) + '</pre>' : '') +
            '</div>';
    }

    div.innerHTML =
        '<div class="message-avatar approval-avatar">?</div>' +
        '<div class="message-content approval-content">' +
            '<div class="approval-header">' +
                '<span class="approval-badge">Requires approval</span>' +
                '<span class="approval-count">' + approvalIds.length + ' tool' + (approvalIds.length !== 1 ? 's' : '') + '</span>' +
            '</div>' +
            toolsHtml +
            '<div class="approval-actions">' +
                '<button class="approval-btn approve-btn" onclick="handleApprovalClick(true)">Approve</button>' +
                '<button class="approval-btn deny-btn" onclick="handleApprovalClick(false)">Deny</button>' +
            '</div>' +
        '</div>';

    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

async function handleApprovalClick(approve) {
    const card = document.getElementById('approvalCard');
    if (!card || !pendingApprovalIds) return;

    const actions = card.querySelector('.approval-actions');
    const badge = card.querySelector('.approval-badge');
    if (actions) {
        actions.innerHTML = '<span class="approval-decision ' + (approve ? 'approved' : 'denied') + '">' +
            (approve ? 'Approved' : 'Denied') + '</span>';
    }
    if (badge) {
        badge.className = 'approval-badge ' + (approve ? 'badge-approved' : 'badge-denied');
        badge.textContent = approve ? 'Approved' : 'Denied';
    }
    card.removeAttribute('id');

    const ids = pendingApprovalIds.map(a => a.id);
    pendingApprovalIds = null;

    await submitApproval(ids, approve);
}

async function submitApproval(approvalIds, approve) {
    setLoading(true);

    try {
        const token = await getAccessToken();
        const resp = await fetch('/api/chat/approve', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(Object.assign({
                access_token: token,
                previous_response_id: lastResponseId,
                approval_ids: approvalIds,
                approve: approve,
            }, selectedAgent && selectedAgent.name ? { agent_name: selectedAgent.name } : {},
               selectedAgent && selectedAgent.project_endpoint ? { project_endpoint: selectedAgent.project_endpoint } : {})),
        });

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            addSystemMessage('Approval error: ' + (err.detail || resp.statusText));
            setLoading(false);
            return;
        }

        const data = await resp.json();
        lastResponseId = data.response_id;

        // Track token usage from approval response
        if (data.usage) {
            totalInputTokens += data.usage.input_tokens || 0;
            totalOutputTokens += data.usage.output_tokens || 0;
            totalTokens += data.usage.total_tokens || 0;
            debugLog('CHAT', 'Approval tokens: in=' + data.usage.input_tokens + ' out=' + data.usage.output_tokens + ' total=' + data.usage.total_tokens);
            updateTokenStats();
        }

        handleResponse(data);
    } catch (err) {
        addSystemMessage('Approval error: ' + err.message);
    }

    setLoading(false);
}

// ---------------------------------------------------------------------------
// OAuth consent UI (first call per user per connection)
// ---------------------------------------------------------------------------

function toolSystemLabel(system) {
    if (system === 'salesforce') return 'Salesforce · ';
    if (system === 'servicenow') return 'ServiceNow · ';
    if (system === 'unknown') return 'Unknown system · ';
    return '';
}

function consentSystemName(label) {
    const l = (label || '').toLowerCase();
    if (l.includes('salesforce')) return 'Salesforce';
    if (l.includes('servicenow')) return 'ServiceNow';
    if (l.includes('toolbox')) return 'Salesforce / ServiceNow';
    return label || 'the connected system';
}

function showConsentUI(consents, originalMessage) {
    const container = document.getElementById('chatContainer');
    const div = document.createElement('div');
    div.className = 'message approval';

    let linksHtml = '';
    for (const c of consents) {
        const link = c.consent_link || '';
        if (!/^https:\/\//i.test(link)) continue;
        linksHtml +=
            '<div class="approval-tool">' +
                '<a class="approval-btn approve-btn consent-link" href="' + escapeAttr(link) +
                '" target="_blank" rel="noopener noreferrer">Sign in to ' +
                escapeHtml(consentSystemName(c.server_label)) + '</a>' +
            '</div>';
    }

    div.innerHTML =
        '<div class="message-avatar approval-avatar">?</div>' +
        '<div class="message-content approval-content">' +
            '<div class="approval-header">' +
                '<span class="approval-badge">Sign-in required</span>' +
            '</div>' +
            '<div>One-time consent so the agent can act as you. Complete the sign-in in the new tab, then continue.</div>' +
            linksHtml +
            '<div class="approval-actions">' +
                '<button class="approval-btn approve-btn consent-continue">I\'ve signed in \u2014 continue</button>' +
            '</div>' +
        '</div>';

    div.querySelector('.consent-continue').addEventListener('click', () => {
        const actions = div.querySelector('.approval-actions');
        if (actions) actions.innerHTML = '<span class="approval-decision approved">Continuing\u2026</span>';
        debugLog('CHAT', 'Consent completed, re-sending original message');
        sendMessage(originalMessage);
    }, { once: true });

    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

// ---------------------------------------------------------------------------
// Keyboard shortcuts
// ---------------------------------------------------------------------------

function handleGlobalKeyDown(event) {
    // Ctrl+Shift+N — New Chat
    if (event.ctrlKey && event.shiftKey && event.key === 'N') {
        event.preventDefault();
        if (currentAccount) startNewChat();
    }
    // Ctrl+Shift+T — Toggle tool panel
    if (event.ctrlKey && event.shiftKey && event.key === 'T') {
        event.preventDefault();
        if (currentAccount) toggleToolPanel();
    }
    // Ctrl+Shift+D — Toggle debug panel
    if (event.ctrlKey && event.shiftKey && event.key === 'D') {
        event.preventDefault();
        if (currentAccount) toggleDebugPanel();
    }
}

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------

function addMessage(role, text, usage) {
    const container = document.getElementById('chatContainer');
    const avatar = role === 'user' ? 'U' : 'A';

    // Render markdown for assistant messages, plain text for user
    let content;
    if (role === 'assistant' && window.marked) {
        content = marked.parse(text);
    } else {
        content = escapeHtml(text);
    }

    let tokenHtml = '';
    if (role === 'assistant' && usage) {
        tokenHtml = '<div class="message-tokens">' +
            '<span title="Input tokens">in: ' + formatTokenCount(usage.input_tokens) + '</span>' +
            '<span title="Output tokens">out: ' + formatTokenCount(usage.output_tokens) + '</span>' +
            '<span title="Total tokens for this turn">total: ' + formatTokenCount(usage.total_tokens) + '</span>' +
            '</div>';
    }

    const div = document.createElement('div');
    div.className = 'message ' + role;
    div.innerHTML =
        '<div class="message-avatar">' + avatar + '</div>' +
        '<div class="message-content">' + content + tokenHtml + '</div>';
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

function escapeAttr(text) {
    return String(text).replace(/&/g, '&amp;').replace(/'/g, '&#39;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
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
