const state = {
  currentView: "overview",
  selectedConversationId: null,
  conversations: [],
  agents: [],
  concerns: [],
  providers: [],
  usage: [],
  audit: [],
  agentActivity: [],
  backgroundJobs: [],
  backgroundStatus: null,
  documents: []
};

const viewMeta = {
  overview: ["Overview", "Current workload, system state and issues needing attention."],
  conversations: ["Conversations", "Helpdesk-style queue for operator threads and replies."],
  agents: ["Agents", "Specialist roster, authority and runtime state."],
  background: ["Background", "Persistent agents maintaining documentation, knowledge and work quality."],
  concerns: ["Concerns", "Evidence, security and consistency issues raised by BOUND."],
  providers: ["Providers", "Model availability, routing and observed efficiency."],
  activity: ["Activity", "Audited tool activity and execution outcomes."]
};

const byId = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {})
    },
    ...options
  });

  const text = await response.text();
  let data = null;

  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text };
    }
  }

  if (!response.ok) {
    const detail = data && data.detail;
    const message = typeof detail === "string"
      ? detail
      : JSON.stringify(detail || data || "Request failed");
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }

  return data;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDate(value) {
  if (!value) return "Unknown";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(date);
}

function shortId(value) {
  return value ? String(value).slice(0, 8) : "";
}

function showNotice(message) {
  const notice = byId("globalNotice");
  notice.textContent = message;
  notice.classList.remove("is-hidden");
}

function clearNotice() {
  const notice = byId("globalNotice");
  notice.textContent = "";
  notice.classList.add("is-hidden");
}

function setView(view) {
  state.currentView = view;

  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle("is-active", active);
    if (active) {
      button.setAttribute("aria-current", "page");
    } else {
      button.removeAttribute("aria-current");
    }
  });

  document.querySelectorAll("[data-view-panel]").forEach((panel) => {
    panel.classList.toggle("is-active", panel.dataset.viewPanel === view);
  });

  const meta = viewMeta[view];
  byId("viewTitle").textContent = meta[0];
  byId("viewSubtitle").textContent = meta[1];
}

function statusPill(label, kind) {
  const safeKind = kind || "muted";
  return '<span class="state-pill is-' + escapeHtml(safeKind) + '">' +
    escapeHtml(label) +
    "</span>";
}

async function loadHealth() {
  try {
    const health = await api("/health");
    byId("coreStatusDot").className = "status-dot is-online";
    byId("coreStatusText").textContent = "Core online";
    byId("summaryCore").textContent = (health.ai && health.ai.state) || "online";
    byId("summaryCoreMeta").textContent = health.hostname || "BOUND Core";
    byId("buildMeta").textContent = health.hostname || "BOUND Core";
  } catch (error) {
    byId("coreStatusDot").className = "status-dot is-warning";
    byId("coreStatusText").textContent = "Core check failed";
    byId("summaryCore").textContent = "Unavailable";
    byId("summaryCoreMeta").textContent = error.message;
  }
}

async function loadConversations() {
  const data = await api("/conversations?limit=100");
  state.conversations = data.conversations || [];

  byId("conversationBadge").textContent = state.conversations.length;
  byId("conversationQueueCount").textContent =
    state.conversations.length + " thread" + (state.conversations.length === 1 ? "" : "s");

  renderConversationQueue();
  renderOverviewConversations();
}

function renderConversationQueue() {
  const queue = byId("conversationQueue");

  if (!state.conversations.length) {
    queue.innerHTML = '<div class="empty-state">No conversations yet.</div>';
    return;
  }

  queue.innerHTML = state.conversations.map((item) => {
    const active = item.id === state.selectedConversationId ? " is-active" : "";
    return '<button class="queue-item' + active + '" type="button" data-conversation-id="' +
      escapeHtml(item.id) + '">' +
      "<strong>" + escapeHtml(item.title || "Untitled conversation") + "</strong>" +
      '<span class="list-meta">Updated ' + escapeHtml(formatDate(item.updated_at)) + "</span>" +
      "</button>";
  }).join("");
}

function renderOverviewConversations() {
  const rows = byId("overviewConversationRows");

  if (!state.conversations.length) {
    rows.innerHTML = '<tr><td colspan="3">No conversations yet.</td></tr>';
    return;
  }

  rows.innerHTML = state.conversations.slice(0, 6).map((item) => {
    return "<tr>" +
      '<td><button class="text-button" type="button" data-open-conversation="' +
      escapeHtml(item.id) + '">' +
      escapeHtml(item.title || "Untitled conversation") +
      "</button></td>" +
      "<td>" + escapeHtml(formatDate(item.updated_at)) + "</td>" +
      "<td>" + escapeHtml(formatDate(item.created_at)) + "</td>" +
      "</tr>";
  }).join("");
}

async function openConversation(id) {
  clearNotice();

  const data = await api("/conversations/" + encodeURIComponent(id));
  state.selectedConversationId = id;
  renderConversationQueue();

  byId("conversationEmpty").classList.add("is-hidden");
  byId("conversationDetail").classList.remove("is-hidden");
  byId("conversationId").textContent = "#" + shortId(id);
  byId("conversationTitle").textContent =
    (data.conversation && data.conversation.title) || "Conversation";
  byId("conversationMeta").textContent =
    "Updated " + formatDate(data.conversation && data.conversation.updated_at);

  const messages = data.messages || [];
  const container = byId("conversationMessages");

  if (!messages.length) {
    container.innerHTML = '<div class="empty-state">No messages in this conversation.</div>';
  } else {
    container.innerHTML = messages.map((message) => {
      const userClass = message.role === "user" ? " user" : "";
      return '<article class="message' + userClass + '">' +
        '<span class="message-role">' + escapeHtml(message.role) + "</span>" +
        '<div class="message-content">' + escapeHtml(message.content) + "</div>" +
        "</article>";
    }).join("");
  }

  container.scrollTop = container.scrollHeight;
  await loadConversationActivity(id);
}

function activityStatusKind(status) {
  if (status === "success") return "good";
  if (status === "success_with_concerns") return "warning";
  if (status === "running") return "warning";
  if (status === "blocked" || status === "failed") return "danger";
  return "muted";
}

function renderAgentActivity() {
  const list = byId("teamActivity");
  const runs = state.agentActivity || [];

  byId("teamRunCount").textContent =
    runs.length + " run" + (runs.length === 1 ? "" : "s");

  if (!runs.length) {
    list.innerHTML =
      '<div class="empty-state">No agents have worked on this conversation yet.</div>';
    return;
  }

  list.innerHTML = runs.map((run) => {
    const output = run.output_json || {};
    const summary = output.summary || run.error_message || "";
    const questions = Array.isArray(output.questions) ? output.questions : [];

    const questionMarkup = questions.length
      ? '<ul class="agent-activity-questions">' +
        questions.map((item) => {
          const question = typeof item === "object" && item !== null
            ? item.question
            : item;
          return "<li>" + escapeHtml(question || "") + "</li>";
        }).join("") +
        "</ul>"
      : "";

    const providerMeta = [run.provider, run.model]
      .filter(Boolean)
      .join(" · ");

    return '<article class="agent-activity-card">' +
      '<div class="agent-activity-topline">' +
        "<div>" +
          '<div class="agent-activity-name">' + escapeHtml(run.agent_id) + "</div>" +
          '<div class="agent-activity-meta">' +
            escapeHtml(run.trigger_type || "agent") +
            (providerMeta ? " · " + escapeHtml(providerMeta) : "") +
          "</div>" +
        "</div>" +
        statusPill(run.status || "unknown", activityStatusKind(run.status)) +
      "</div>" +
      (summary
        ? '<div class="agent-activity-summary">' + escapeHtml(summary) + "</div>"
        : "") +
      questionMarkup +
    "</article>";
  }).join("");

  list.scrollTop = list.scrollHeight;
}

async function loadConversationActivity(id, quiet) {
  try {
    const data = await api(
      "/conversations/" + encodeURIComponent(id) + "/agent-activity"
    );

    if (state.selectedConversationId !== id) {
      return;
    }

    state.agentActivity = data.runs || [];
    renderAgentActivity();
  } catch (error) {
    if (!quiet) {
      if (error.status === 404) {
        showNotice(
          "Agent activity endpoint is not available in the running backend. " +
          "The UI files are newer than the loaded BOUND process. Restart BOUND after pulling."
        );
      } else {
        showNotice("Agent activity could not be loaded: " + error.message);
      }
    }
  }
}

async function loadAgents() {
  const data = await api("/agents");
  state.agents = data.agents || [];

  byId("agentBadge").textContent = state.agents.length;
  byId("summaryAgents").textContent = state.agents.length;

  const enabled = state.agents.filter((agent) => agent.enabled).length;
  byId("summaryAgentsMeta").textContent = enabled + " enabled";

  renderAgents();
}

function renderAgents() {
  const list = byId("agentList");

  if (!state.agents.length) {
    list.innerHTML = '<div class="empty-state">No agents registered.</div>';
    return;
  }

  list.innerHTML = state.agents.map((agent) => {
    const enabledPill = agent.enabled
      ? statusPill("Enabled", "good")
      : statusPill("Disabled", "muted");

    return '<article class="agent-row">' +
      "<div>" +
        '<div class="agent-name">' + escapeHtml(agent.name) + "</div>" +
        '<div class="agent-meta">' +
          escapeHtml(agent.authority) + " · " +
          escapeHtml(agent.mode) + " · " +
          (agent.persistent ? "persistent" : "ephemeral") +
        "</div>" +
      "</div>" +
      "<div>" +
        "<p>" + escapeHtml(agent.purpose) + "</p>" +
        '<p class="agent-meta">' + escapeHtml(agent.persona) + "</p>" +
      "</div>" +
      '<div class="agent-controls">' +
        enabledPill +
        '<button class="button button-secondary" type="button" data-agent-toggle="' +
          escapeHtml(agent.id) +
          '" data-agent-enabled="' + (agent.enabled ? "true" : "false") + '">' +
          (agent.enabled ? "Disable" : "Enable") +
        "</button>" +
      "</div>" +
    "</article>";
  }).join("");
}

async function loadBackground() {
  const results = await Promise.all([
    api("/agents/background/status"),
    api("/agents/background/jobs?limit=100"),
    api("/documents?environment_id=school&limit=50")
  ]);

  state.backgroundStatus = results[0] || {};
  state.backgroundJobs = results[1].jobs || [];
  state.documents = results[2].documents || [];

  const active = state.backgroundJobs.filter((job) =>
    ["queued", "running"].includes(job.status)
  ).length;

  byId("backgroundBadge").textContent = active;
  renderBackground();
}

function renderBackground() {
  const status = state.backgroundStatus || {};
  const enabled = Boolean(status.enabled);
  const statusEl = byId("backgroundState");
  const toggle = byId("backgroundToggleButton");

  statusEl.textContent = enabled ? "Running" : "Paused";
  statusEl.className = "state-pill " + (enabled ? "is-good" : "is-muted");
  toggle.textContent = enabled ? "Pause" : "Resume";
  toggle.dataset.backgroundEnabled = enabled ? "true" : "false";

  const rows = byId("backgroundJobRows");

  if (!state.backgroundJobs.length) {
    rows.innerHTML = '<tr><td colspan="5">No background jobs yet.</td></tr>';
  } else {
    rows.innerHTML = state.backgroundJobs.map((job) => {
      return "<tr>" +
        "<td>" + escapeHtml(formatDate(job.created_at)) + "</td>" +
        "<td>" + escapeHtml(job.agent_id || "") + "</td>" +
        "<td>" + escapeHtml(job.job_type || "") + "</td>" +
        "<td>" + statusPill(job.status || "unknown", activityStatusKind(job.status)) + "</td>" +
        "<td>" + escapeHtml(job.attempts || 0) + "/" + escapeHtml(job.max_attempts || 0) + "</td>" +
      "</tr>";
    }).join("");
  }

  const docs = byId("documentList");

  if (!state.documents.length) {
    docs.innerHTML = '<div class="empty-state">No generated documentation yet.</div>';
  } else {
    docs.innerHTML = state.documents.map((doc) => {
      return '<article class="list-item">' +
        "<div>" +
          '<div class="list-title">' + escapeHtml(doc.title || "Untitled document") + "</div>" +
          '<div class="list-meta">' +
            escapeHtml(doc.category || "") + " · v" +
            escapeHtml(doc.version || 1) + " · " +
            escapeHtml(formatDate(doc.updated_at)) +
          "</div>" +
        "</div>" +
        statusPill(doc.status || "draft", doc.status === "published" ? "good" : "muted") +
      "</article>";
    }).join("");
  }
}

async function toggleBackground() {
  const button = byId("backgroundToggleButton");
  const enabled = button.dataset.backgroundEnabled === "true";

  button.disabled = true;

  try {
    await api("/agents/background/enabled", {
      method: "POST",
      body: JSON.stringify({
        enabled: !enabled
      })
    });

    await loadBackground();
  } catch (error) {
    showNotice("Background worker update failed: " + error.message);
  } finally {
    button.disabled = false;
  }
}

async function loadConcerns() {
  const data = await api("/agents/concerns?limit=100");
  state.concerns = data.concerns || [];

  byId("concernBadge").textContent = state.concerns.length;
  byId("summaryConcerns").textContent = state.concerns.length;
  byId("overviewBadge").textContent = state.concerns.length;

  renderConcerns();
  renderOverviewConcerns();
}

function concernMarkup(item) {
  const severity = item.severity || "info";

  return '<article class="concern-row">' +
    '<span class="severity-pill ' + escapeHtml(severity) + '">' + escapeHtml(severity) + "</span>" +
    "<div>" +
      '<div class="concern-title">' + escapeHtml(item.category || "Concern") + "</div>" +
      "<p>" + escapeHtml(item.message || "") + "</p>" +
      '<div class="concern-meta">' +
        escapeHtml(item.source_agent_id || "system") + " · " + escapeHtml(formatDate(item.created_at)) +
      "</div>" +
    "</div>" +
    '<span class="ticket-id">#' + escapeHtml(shortId(item.id)) + "</span>" +
  "</article>";
}

function renderConcerns() {
  const list = byId("concernList");

  if (!state.concerns.length) {
    list.innerHTML = '<div class="empty-state">No open concerns. Suspiciously peaceful.</div>';
    return;
  }

  list.innerHTML = state.concerns.map(concernMarkup).join("");
}

function renderOverviewConcerns() {
  const list = byId("overviewConcerns");

  if (!state.concerns.length) {
    list.innerHTML = '<div class="empty-state">No open concerns.</div>';
    return;
  }

  list.innerHTML = state.concerns.slice(0, 5).map(concernMarkup).join("");
}

async function loadProviders() {
  const results = await Promise.all([
    api("/ai/providers"),
    api("/ai/usage?days=30")
  ]);

  const providerData = results[0];
  const usageData = results[1];

  state.providers = providerData.providers || [];
  state.usage = usageData.providers || [];

  const available = state.providers.filter((item) => item.available).length;
  byId("summaryProviders").textContent = available + "/" + state.providers.length;
  byId("summaryProvidersMeta").textContent = "Available";

  renderProviders();
  renderOverviewProviders();
  renderUsage();
}

function providerMarkup(item, index) {
  const kind = item.available ? "good" : "warning";
  const stateText = item.available ? "Available" : "Unavailable";

  return '<div class="provider-row">' +
    '<div class="provider-details">' +
      '<div class="provider-name">' + (index + 1) + ". " + escapeHtml(item.provider) + "</div>" +
      '<div class="provider-meta">' + escapeHtml(item.model || "No model selected") + "</div>" +
      (item.reason ? '<div class="provider-meta">' + escapeHtml(item.reason) + "</div>" : "") +
    "</div>" +
    statusPill(stateText, kind) +
  "</div>";
}

function renderProviders() {
  const list = byId("providerList");

  if (!state.providers.length) {
    list.innerHTML = '<div class="empty-state">No provider configuration found.</div>';
    return;
  }

  list.innerHTML = state.providers.map(providerMarkup).join("");
}

function renderOverviewProviders() {
  const list = byId("overviewProviders");

  if (!state.providers.length) {
    list.innerHTML = '<div class="empty-state">No providers configured.</div>';
    return;
  }

  list.innerHTML = state.providers.map((item, index) => {
    return '<div class="list-item">' +
      "<div>" +
        '<div class="list-title">' + (index + 1) + ". " + escapeHtml(item.provider) + "</div>" +
        '<div class="list-meta">' + escapeHtml(item.model || "No model") + "</div>" +
      "</div>" +
      statusPill(item.available ? "Ready" : "Unavailable", item.available ? "good" : "warning") +
    "</div>";
  }).join("");
}

function renderUsage() {
  const list = byId("usageSummary");

  if (!state.usage.length) {
    list.innerHTML = '<div class="empty-state">No usage data yet.</div>';
    return;
  }

  list.innerHTML = state.usage.map((item) => {
    const spend = Number(item.shadow_spend_usd || 0).toFixed(4);
    const useful =
      item.useful_percent === null || item.useful_percent === undefined
        ? "Unrated"
        : item.useful_percent + "% useful";

    return '<div class="metric-row">' +
      "<div>" +
        '<div class="provider-name">' + escapeHtml(item.provider) + "</div>" +
        '<div class="provider-meta">' + escapeHtml(useful) + " · " + escapeHtml(item.calls) + " calls</div>" +
      "</div>" +
      "<strong>$" + spend + "</strong>" +
    "</div>";
  }).join("");
}

async function loadAudit() {
  const data = await api("/audit?limit=100");
  state.audit = data.events || [];
  renderAudit();
}

function renderAudit() {
  const rows = byId("activityRows");

  if (!state.audit.length) {
    rows.innerHTML = '<tr><td colspan="6">No audit events yet.</td></tr>';
    return;
  }

  rows.innerHTML = state.audit.map((item) => {
    let kind = "muted";
    if (item.status === "success") kind = "good";
    if (["blocked", "failed"].includes(item.status)) kind = "danger";
    if (item.status === "approval_required") kind = "warning";

    return "<tr>" +
      "<td>" + escapeHtml(formatDate(item.timestamp)) + "</td>" +
      "<td>" + escapeHtml(item.tool_name) + "</td>" +
      "<td>" + statusPill(item.status, kind) + "</td>" +
      "<td>" + escapeHtml(item.risk || "") + "</td>" +
      "<td>" + escapeHtml(item.environment || "") + "</td>" +
      "<td>" + escapeHtml(item.target || "—") + "</td>" +
    "</tr>";
  }).join("");
}

async function refreshAll() {
  clearNotice();

  const jobs = [
    loadHealth(),
    loadConversations(),
    loadAgents(),
    loadBackground(),
    loadConcerns(),
    loadProviders(),
    loadAudit()
  ];

  const results = await Promise.allSettled(jobs);
  const failed = results.filter((result) => result.status === "rejected");

  if (failed.length) {
    showNotice(
      failed.length + " dashboard section" +
      (failed.length === 1 ? "" : "s") +
      " could not be refreshed."
    );
  }
}

async function submitChat(event) {
  event.preventDefault();

  if (!state.selectedConversationId) return;

  const input = byId("chatInput");
  const message = input.value.trim();
  if (!message) return;

  const button = byId("sendButton");
  button.disabled = true;
  input.setAttribute("aria-busy", "true");

  let poller = null;

  try {
    const conversationId = state.selectedConversationId;

    const request = api("/chat", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: conversationId,
        message: message,
        use_agents: true,
        environment_id: "school"
      })
    });

    poller = window.setInterval(() => {
      loadConversationActivity(
        conversationId,
        true
      );
    }, 1200);

    await request;

    input.value = "";
    await openConversation(conversationId);
    await loadConversations();
  } catch (error) {
    showNotice("Reply failed: " + error.message);
  } finally {
    if (poller !== null) {
      window.clearInterval(poller);
    }

    button.disabled = false;
    input.removeAttribute("aria-busy");
  }
}

async function createConversation(dialog) {
  const input = byId("newConversationInput");
  const message = input.value.trim();

  if (!message) return;

  const button = byId("createConversationButton");
  button.disabled = true;

  try {
    const result = await api("/chat", {
      method: "POST",
      body: JSON.stringify({
        message: message,
        use_agents: true,
        environment_id: "school"
      })
    });

    input.value = "";
    dialog.close();
    await loadConversations();
    setView("conversations");

    if (result.conversation_id) {
      await openConversation(result.conversation_id);
    }
  } catch (error) {
    showNotice("Conversation could not be created: " + error.message);
  } finally {
    button.disabled = false;
  }
}

async function toggleAgent(button) {
  const agentId = button.dataset.agentToggle;
  const currentlyEnabled = button.dataset.agentEnabled === "true";

  button.disabled = true;

  try {
    await api("/agents/" + encodeURIComponent(agentId) + "/enabled", {
      method: "POST",
      body: JSON.stringify({
        enabled: !currentlyEnabled
      })
    });

    await loadAgents();
  } catch (error) {
    showNotice("Agent update failed: " + error.message);
  } finally {
    button.disabled = false;
  }
}

document.addEventListener("click", async (event) => {
  const nav = event.target.closest("[data-view]");
  if (nav) {
    setView(nav.dataset.view);
    return;
  }

  const jump = event.target.closest("[data-jump]");
  if (jump) {
    setView(jump.dataset.jump);
    return;
  }

  const queueItem = event.target.closest("[data-conversation-id]");
  if (queueItem) {
    await openConversation(queueItem.dataset.conversationId);
    return;
  }

  const openButton = event.target.closest("[data-open-conversation]");
  if (openButton) {
    setView("conversations");
    await openConversation(openButton.dataset.openConversation);
    return;
  }

  const agentToggle = event.target.closest("[data-agent-toggle]");
  if (agentToggle) {
    await toggleAgent(agentToggle);
  }
});

byId("refreshButton").addEventListener("click", refreshAll);
byId("queueRefreshButton").addEventListener("click", loadConversations);
byId("backgroundToggleButton").addEventListener("click", toggleBackground);
byId("chatForm").addEventListener("submit", submitChat);

const dialog = byId("newConversationDialog");

byId("newConversationButton").addEventListener("click", () => {
  dialog.showModal();
  requestAnimationFrame(() => byId("newConversationInput").focus());
});

byId("newConversationForm").addEventListener("submit", async (event) => {
  const submitter = event.submitter;

  if (submitter && submitter.value === "cancel") {
    return;
  }

  event.preventDefault();
  await createConversation(dialog);
});

refreshAll();
