const API_BASE = "/api/v1";
const STORAGE_KEYS = {
  accessToken: "slidesheet_access_token",
  refreshToken: "slidesheet_refresh_token",
  conversationId: "slidesheet_conversation_id",
};

const getToken = () => localStorage.getItem(STORAGE_KEYS.accessToken);
const getRefreshToken = () => localStorage.getItem(STORAGE_KEYS.refreshToken);

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const token = getToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const data = await response.json();
      if (data && data.detail) {
        message = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
      }
    } catch {
      // Keep generic message if body is not JSON.
    }
    throw new Error(message);
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

function saveTokens(tokens) {
  localStorage.setItem(STORAGE_KEYS.accessToken, tokens.access_token);
  localStorage.setItem(STORAGE_KEYS.refreshToken, tokens.refresh_token);
}

function clearAuth() {
  localStorage.removeItem(STORAGE_KEYS.accessToken);
  localStorage.removeItem(STORAGE_KEYS.refreshToken);
  localStorage.removeItem(STORAGE_KEYS.conversationId);
}

function appendMessage(role, text, citations = []) {
  const messages = document.getElementById("messages");
  const empty = messages.querySelector(".empty-state");
  if (empty) {
    empty.remove();
  }

  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  wrap.appendChild(bubble);

  if (citations.length) {
    const source = document.createElement("div");
    source.className = "citations";
    source.textContent = citations.map((c) => `${c.filename} (${c.source})`).join(" | ");
    wrap.appendChild(source);
  }

  messages.appendChild(wrap);
  messages.scrollTop = messages.scrollHeight;
}

async function initLoginPage() {
  const form = document.getElementById("authForm");
  const toggle = document.getElementById("toggleModeBtn");
  const statusEl = document.getElementById("authStatus");
  const modeEl = document.getElementById("authModeLabel");
  const orgField = document.getElementById("organizationName");

  let mode = "login";
  toggle.addEventListener("click", () => {
    mode = mode === "login" ? "register" : "login";
    const isRegister = mode === "register";
    modeEl.textContent = isRegister ? "Register" : "Login";
    toggle.textContent = isRegister ? "Switch to login" : "Switch to register";
    orgField.style.display = isRegister ? "block" : "none";
    statusEl.textContent = "";
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    statusEl.textContent = "";
    statusEl.className = "status";
    const email = document.getElementById("email").value.trim();
    const password = document.getElementById("password").value;
    const organizationName = document.getElementById("organizationName").value.trim();

    const payload = { email, password };
    if (mode === "register") {
      payload.organization_name = organizationName || "My Org";
    }

    try {
      const endpoint = mode === "register" ? "/auth/register" : "/auth/login";
      const tokens = await api(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      saveTokens(tokens);
      window.location.href = "/";
    } catch (error) {
      statusEl.textContent = error.message;
      statusEl.className = "status error";
    }
  });
}

async function ensureConversation() {
  const existing = localStorage.getItem(STORAGE_KEYS.conversationId);
  if (existing) {
    return existing;
  }
  const conversation = await api("/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: "Web Chat" }),
  });
  localStorage.setItem(STORAGE_KEYS.conversationId, conversation.id);
  return conversation.id;
}

async function loadDocuments() {
  const docList = document.getElementById("docList");
  docList.innerHTML = "";
  const documents = await api("/documents?limit=200&offset=0");

  if (!documents.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = "No documents uploaded yet.";
    docList.appendChild(empty);
    return;
  }

  for (const doc of documents) {
    const row = document.createElement("label");
    row.className = "doc-item";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = doc.id;
    checkbox.checked = doc.status === "ready";
    checkbox.disabled = doc.status !== "ready";
    const text = document.createElement("span");
    text.textContent = `${doc.filename} [${doc.status}]`;
    row.appendChild(checkbox);
    row.appendChild(text);
    docList.appendChild(row);
  }
}

function selectedDocumentIds() {
  return Array.from(document.querySelectorAll("#docList input[type='checkbox']:checked")).map(
    (el) => el.value,
  );
}

async function initAppPage() {
  if (!getToken()) {
    window.location.href = "/login";
    return;
  }

  const uploadStatus = document.getElementById("uploadStatus");
  const userBadge = document.getElementById("userBadge");
  const fileInput = document.getElementById("fileInput");
  const uploadBtn = document.getElementById("uploadBtn");
  const logoutBtn = document.getElementById("logoutBtn");
  const chatForm = document.getElementById("chatForm");
  const questionInput = document.getElementById("questionInput");

  try {
    const me = await api("/auth/me");
    userBadge.textContent = `${me.email} @ ${me.organization_name}`;
  } catch {
    clearAuth();
    window.location.href = "/login";
    return;
  }

  await loadDocuments();
  await ensureConversation();

  const messages = document.getElementById("messages");
  messages.innerHTML = '<div class="empty-state">Ask your first question.</div>';

  uploadBtn.addEventListener("click", async () => {
    const files = fileInput.files ? Array.from(fileInput.files) : [];
    if (!files.length) {
      uploadStatus.textContent = "Select at least one file.";
      uploadStatus.className = "status error";
      return;
    }

    uploadBtn.disabled = true;
    uploadStatus.className = "status";
    try {
      let uploaded = 0;
      for (const file of files) {
        const form = new FormData();
        form.append("file", file);
        await api("/documents", { method: "POST", body: form });
        uploaded += 1;
      }
      uploadStatus.textContent = `Uploaded ${uploaded} file(s).`;
      uploadStatus.className = "status ok";
      fileInput.value = "";
      await loadDocuments();
    } catch (error) {
      uploadStatus.textContent = error.message;
      uploadStatus.className = "status error";
    } finally {
      uploadBtn.disabled = false;
    }
  });

  chatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = questionInput.value.trim();
    if (!question) {
      return;
    }

    appendMessage("user", question);
    questionInput.value = "";

    try {
      const conversationId = await ensureConversation();
      const response = await api(`/conversations/${conversationId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          document_ids: selectedDocumentIds(),
        }),
      });
      appendMessage("assistant", response.answer, response.citations || []);
    } catch (error) {
      appendMessage("assistant", `Error: ${error.message}`);
    }
  });

  logoutBtn.addEventListener("click", async () => {
    try {
      const refresh = getRefreshToken();
      if (refresh) {
        await api("/auth/logout", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refresh }),
        });
      }
    } catch {
      // Logout should still clear local state even if server request fails.
    }
    clearAuth();
    window.location.href = "/login";
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  const page = document.body.dataset.page;
  if (page === "login") {
    await initLoginPage();
  } else {
    await initAppPage();
  }
});
