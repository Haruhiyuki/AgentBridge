ACCESS_POLICY_ADMIN_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentBridge Access Policy</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --panel-muted: #f0f3f7;
      --line: #d6dde6;
      --text: #17202a;
      --muted: #5b6878;
      --accent: #0f766e;
      --danger: #b42318;
      --focus: #155eef;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
    }
    header {
      height: 56px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 0 20px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 650;
      letter-spacing: 0;
    }
    main {
      display: grid;
      grid-template-columns: minmax(420px, 1.05fr) minmax(440px, 0.95fr);
      min-height: calc(100vh - 56px);
    }
    section {
      min-width: 0;
      border-right: 1px solid var(--line);
      background: var(--panel);
    }
    section:last-child { border-right: 0; }
    .toolbar {
      min-height: 56px;
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 10px 14px;
      border-bottom: 1px solid var(--line);
      background: var(--panel-muted);
      flex-wrap: wrap;
    }
    button, select, input, textarea {
      font: inherit;
      border-radius: 6px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
    }
    button {
      min-height: 34px;
      padding: 0 12px;
      cursor: pointer;
      font-weight: 600;
    }
    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
    }
    button.danger {
      border-color: #f3b4ad;
      color: var(--danger);
    }
    button:disabled {
      cursor: not-allowed;
      opacity: .55;
    }
    input, select {
      min-height: 34px;
      padding: 6px 8px;
      width: 100%;
    }
    textarea {
      min-height: 76px;
      padding: 8px;
      width: 100%;
      resize: vertical;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
    }
    label {
      display: grid;
      gap: 5px;
      min-width: 0;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .field-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      padding: 14px;
    }
    .wide { grid-column: 1 / -1; }
    .table-wrap {
      overflow: auto;
      max-height: calc(100vh - 112px);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    th, td {
      padding: 9px 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    th {
      position: sticky;
      top: 0;
      background: #f9fafb;
      z-index: 1;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    tr {
      cursor: pointer;
    }
    tr.selected td {
      background: #e8f5f3;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      height: 22px;
      padding: 0 8px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: #fff;
      font-size: 12px;
      font-weight: 700;
    }
    .pill.allow { color: var(--accent); }
    .pill.deny { color: var(--danger); }
    .status {
      min-height: 34px;
      display: flex;
      align-items: center;
      padding: 0 10px;
      color: var(--muted);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      flex: 1;
      min-width: 160px;
    }
    .result {
      margin: 0 14px 14px;
      padding: 10px;
      min-height: 58px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fbfcfe;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      overflow: auto;
    }
    .compact { max-width: 160px; }
    @media (max-width: 920px) {
      header { height: auto; min-height: 56px; align-items: flex-start; padding: 12px 14px; }
      main { grid-template-columns: 1fr; }
      section { border-right: 0; border-bottom: 1px solid var(--line); }
      .field-grid { grid-template-columns: 1fr; }
      .compact { max-width: none; }
    }
  </style>
</head>
<body>
  <header>
    <h1>AgentBridge Access Policy</h1>
    <div class="status" id="status">Ready</div>
  </header>
  <main>
    <section>
      <div class="toolbar">
        <button id="refresh" type="button">Refresh</button>
        <button id="new-rule" type="button">New</button>
        <label class="compact">
          Enabled
          <select id="enabled-filter">
            <option value="">All</option>
            <option value="true">Enabled</option>
            <option value="false">Disabled</option>
          </select>
        </label>
      </div>
      <div class="table-wrap">
        <table aria-label="Access policy rules">
          <thead>
            <tr>
              <th style="width: 92px;">Effect</th>
              <th>Action</th>
              <th>Resource</th>
              <th>Actors</th>
              <th style="width: 76px;">Priority</th>
            </tr>
          </thead>
          <tbody id="rules"></tbody>
        </table>
      </div>
    </section>

    <section>
      <div class="toolbar">
        <button class="primary" id="simulate" type="button">Simulate</button>
        <button class="primary" id="save" type="button">Save</button>
        <button class="danger" id="delete" type="button">Delete</button>
      </div>

      <div class="field-grid">
        <label>
          Rule ID
          <input id="rule-id" autocomplete="off" placeholder="generated when empty">
        </label>
        <label>
          Effect
          <select id="effect">
            <option value="allow">allow</option>
            <option value="deny">deny</option>
          </select>
        </label>
        <label>
          Action
          <input id="action" autocomplete="off" placeholder="terminal.control">
        </label>
        <label>
          Resource Type
          <input id="resource-type" autocomplete="off" value="*">
        </label>
        <label>
          Resource ID
          <input id="resource-id" autocomplete="off">
        </label>
        <label>
          Priority
          <input id="priority" type="number" value="100">
        </label>
        <label>
          Actor IDs
          <input id="actor-ids" autocomplete="off" placeholder="usr_1, usr_2">
        </label>
        <label>
          Roles
          <input id="roles" autocomplete="off" placeholder="operator, maintainer">
        </label>
        <label>
          Enabled
          <select id="enabled">
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
        </label>
        <label>
          Chat Context ID
          <input id="chat-context-id" autocomplete="off">
        </label>
        <label class="wide">
          Attributes JSON
          <textarea id="attributes">{}</textarea>
        </label>
        <label class="wide">
          Description
          <textarea id="description"></textarea>
        </label>
      </div>

      <div class="toolbar">
        <label class="compact">
          Admin ID
          <input id="admin-id" value="admin-ui">
        </label>
        <label class="compact">
          Admin Roles
          <input id="admin-roles" value="admin">
        </label>
        <label class="compact">
          Target ID
          <input id="target-id" value="usr_operator">
        </label>
        <label class="compact">
          Target Roles
          <input id="target-roles" value="operator">
        </label>
      </div>
      <pre class="result" id="simulation">{}</pre>
    </section>
  </main>

  <script>
    const $ = (id) => document.getElementById(id);
    let selectedRuleId = "";

    function csv(value) {
      return value.split(",").map((item) => item.trim()).filter(Boolean);
    }

    function parseJsonField(id) {
      const raw = $(id).value.trim();
      return raw ? JSON.parse(raw) : {};
    }

    function actorFrom(prefix) {
      return {
        id: $(`${prefix}-id`).value.trim() || prefix,
        roles: csv($(`${prefix}-roles`).value || "admin"),
      };
    }

    function setStatus(text) {
      $("status").textContent = text;
    }

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }

    function renderRule(rule) {
      const tr = document.createElement("tr");
      tr.dataset.ruleId = rule.id;
      if (rule.id === selectedRuleId) tr.className = "selected";
      const resource =
        `${rule.resource_type || "*"}${rule.resource_id ? ":" + rule.resource_id : ""}`;
      const actors = [
        ...(rule.actor_ids || []),
        ...(rule.roles || []).map((role) => `role:${role}`),
      ].join(", ");
      const effect = escapeHtml(rule.effect);
      const effectClass = rule.effect === "deny" ? "deny" : "allow";
      const action = escapeHtml(rule.action);
      const resourceLabel = escapeHtml(resource);
      const actorsLabel = escapeHtml(actors);
      const priority = escapeHtml(rule.priority);
      tr.innerHTML = `
        <td><span class="pill ${effectClass}">${effect}</span></td>
        <td title="${action}">${action}</td>
        <td title="${resourceLabel}">${resourceLabel}</td>
        <td title="${actorsLabel}">${actorsLabel}</td>
        <td>${priority}</td>`;
      tr.addEventListener("click", () => selectRule(rule));
      return tr;
    }

    async function requestJson(url, options = {}) {
      const response = await fetch(url, {
        headers: {"content-type": "application/json"},
        ...options,
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.message || data.detail || response.statusText);
      }
      return data;
    }

    async function loadRules() {
      setStatus("Loading");
      const filter = $("enabled-filter").value;
      const suffix = filter ? `?enabled=${encodeURIComponent(filter)}` : "";
      const rules = await requestJson(`/api/v1/access-policy/rules${suffix}`);
      const tbody = $("rules");
      tbody.replaceChildren(...rules.map(renderRule));
      setStatus(`${rules.length} rules`);
    }

    function selectRule(rule) {
      selectedRuleId = rule.id;
      $("rule-id").value = rule.id || "";
      $("effect").value = rule.effect || "allow";
      $("action").value = rule.action || "";
      $("resource-type").value = rule.resource_type || "*";
      $("resource-id").value = rule.resource_id || "";
      $("priority").value = rule.priority ?? 100;
      $("actor-ids").value = (rule.actor_ids || []).join(", ");
      $("roles").value = (rule.roles || []).join(", ");
      $("enabled").value = String(rule.enabled !== false);
      $("chat-context-id").value = "";
      $("attributes").value = JSON.stringify(rule.attributes || {}, null, 2);
      $("description").value = rule.description || "";
      document.querySelectorAll("#rules tr").forEach((row) => {
        row.classList.toggle("selected", row.dataset.ruleId === selectedRuleId);
      });
    }

    function newRule() {
      selectedRuleId = "";
      $("rule-id").value = "";
      $("effect").value = "allow";
      $("action").value = "";
      $("resource-type").value = "*";
      $("resource-id").value = "";
      $("priority").value = "100";
      $("actor-ids").value = "";
      $("roles").value = "";
      $("enabled").value = "true";
      $("chat-context-id").value = "";
      $("attributes").value = "{}";
      $("description").value = "";
      $("simulation").textContent = "{}";
      document.querySelectorAll("#rules tr").forEach((row) => row.classList.remove("selected"));
      setStatus("New rule");
    }

    function rulePayload() {
      return {
        actor: actorFrom("admin"),
        rule_id: $("rule-id").value.trim() || null,
        effect: $("effect").value,
        action: $("action").value.trim(),
        resource_type: $("resource-type").value.trim() || "*",
        resource_id: $("resource-id").value.trim() || null,
        actor_ids: csv($("actor-ids").value),
        roles: csv($("roles").value),
        attributes: parseJsonField("attributes"),
        description: $("description").value.trim() || null,
        priority: Number.parseInt($("priority").value || "100", 10),
        enabled: $("enabled").value === "true",
        chat_context_id: $("chat-context-id").value.trim() || null,
        trace_id: "admin-ui-policy-save",
      };
    }

    async function simulatePolicy() {
      const payload = {
        actor: actorFrom("admin"),
        target_actor: actorFrom("target"),
        action: $("action").value.trim(),
        resource_type: $("resource-type").value.trim() || "*",
        resource_id: $("resource-id").value.trim() || null,
        attributes: parseJsonField("attributes"),
        chat_context_id: $("chat-context-id").value.trim() || null,
      };
      const result = await requestJson("/api/v1/access-policy/simulate", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      $("simulation").textContent = JSON.stringify(result, null, 2);
      setStatus(result.decision.allowed ? "Simulation allowed" : "Simulation denied");
      return result;
    }

    async function saveRule() {
      await simulatePolicy();
      const payload = rulePayload();
      const id = $("rule-id").value.trim();
      const url = id
        ? `/api/v1/access-policy/rules/${encodeURIComponent(id)}`
        : "/api/v1/access-policy/rules";
      const saved = await requestJson(url, {
        method: id ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      selectedRuleId = saved.id;
      await loadRules();
      setStatus(`Saved ${saved.id}`);
    }

    async function deleteRule() {
      const id = $("rule-id").value.trim();
      if (!id) return;
      await requestJson(`/api/v1/access-policy/rules/${encodeURIComponent(id)}/delete`, {
        method: "POST",
        body: JSON.stringify({actor: actorFrom("admin"), trace_id: "admin-ui-policy-delete"}),
      });
      newRule();
      await loadRules();
      setStatus(`Deleted ${id}`);
    }

    async function run(action) {
      try {
        await action();
      } catch (error) {
        setStatus(error.message);
      }
    }

    $("refresh").addEventListener("click", () => run(loadRules));
    $("enabled-filter").addEventListener("change", () => run(loadRules));
    $("new-rule").addEventListener("click", newRule);
    $("simulate").addEventListener("click", () => run(simulatePolicy));
    $("save").addEventListener("click", () => run(saveRule));
    $("delete").addEventListener("click", () => run(deleteRule));
    loadRules().catch((error) => setStatus(error.message));
  </script>
</body>
</html>
"""
