ADMIN_HOME_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentBridge Admin</title>
  <style>
    :root {
      --bg: #f7f8fa;
      --panel: #ffffff;
      --line: #d6dde6;
      --text: #17202a;
      --muted: #5b6878;
      --accent: #0f766e;
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
      max-width: 980px;
      margin: 0 auto;
      padding: 24px 16px;
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    a {
      display: grid;
      gap: 8px;
      min-height: 116px;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      color: inherit;
      text-decoration: none;
    }
    a:focus, a:hover {
      border-color: var(--accent);
      outline: none;
    }
    strong {
      font-size: 16px;
      font-weight: 700;
    }
    span {
      color: var(--muted);
    }
    @media (max-width: 760px) {
      main { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>AgentBridge Admin</h1>
  </header>
  <main>
    <a href="/admin/access-policy">
      <strong>Access Policy</strong>
      <span>Rules, simulation, save, delete</span>
    </a>
    <a href="/admin/system">
      <strong>System Health</strong>
      <span>Service health, lifecycle monitor, retry worker</span>
    </a>
    <a href="/admin/projects">
      <strong>Projects & Sessions</strong>
      <span>Project inventory, workspaces, session lifecycle</span>
    </a>
    <a href="/admin/interactions">
      <strong>Interactions</strong>
      <span>Questions, approvals, votes, cancellations</span>
    </a>
    <a href="/admin/audit">
      <strong>Audit & Events</strong>
      <span>Audit chain filters, event search, replay, live tail</span>
    </a>
    <a href="/admin/terminal-lifecycle">
      <strong>Terminal Lifecycle</strong>
      <span>Monitor status, backend supervision, run once</span>
    </a>
    <a href="/admin/device-identities">
      <strong>Device Identities</strong>
      <span>Managed keys, rotation, revocation</span>
    </a>
    <a href="/admin/bot-delivery">
      <strong>Bot Delivery</strong>
      <span>Records, retry worker, due retry, rate limits</span>
    </a>
  </main>
</body>
</html>
"""


SYSTEM_HEALTH_ADMIN_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentBridge System Health</title>
  <style>
    :root {
      --bg: #f7f8fa;
      --panel: #ffffff;
      --panel-muted: #f0f3f7;
      --line: #d6dde6;
      --text: #17202a;
      --muted: #5b6878;
      --accent: #0f766e;
      --danger: #b42318;
      --warn: #a15c00;
      --ok: #087443;
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
      min-height: 56px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 10px 20px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 650;
      letter-spacing: 0;
    }
    nav {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    nav a {
      color: var(--muted);
      text-decoration: none;
      font-weight: 650;
    }
    nav a:hover { color: var(--accent); }
    main {
      display: grid;
      grid-template-columns: minmax(360px, .8fr) minmax(520px, 1.2fr);
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
    button {
      min-height: 34px;
      padding: 6px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      cursor: pointer;
      font: inherit;
      font-weight: 650;
    }
    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
    }
    .status {
      flex: 1;
      min-width: 180px;
      color: var(--muted);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      padding: 14px;
    }
    .metric {
      min-height: 78px;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }
    .metric span {
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .metric strong {
      display: block;
      margin-top: 6px;
      font-size: 22px;
      font-weight: 700;
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
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    th {
      color: var(--muted);
      background: #f9fafb;
      font-size: 12px;
      font-weight: 700;
    }
    .ok { color: var(--ok); font-weight: 700; }
    .warn { color: var(--warn); font-weight: 700; }
    .fail { color: var(--danger); font-weight: 700; }
    .actions th, .actions td {
      white-space: normal;
      vertical-align: top;
    }
    pre {
      margin: 0;
      padding: 10px;
      min-height: 150px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfe;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      overflow: auto;
    }
    @media (max-width: 980px) {
      main { grid-template-columns: 1fr; }
      section { border-right: 0; border-bottom: 1px solid var(--line); }
      header { align-items: flex-start; flex-direction: column; }
    }
    @media (max-width: 560px) {
      .metrics { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>AgentBridge System Health</h1>
    <nav>
      <a href="/admin">Admin</a>
      <a href="/admin/system">System Health</a>
      <a href="/admin/projects">Projects & Sessions</a>
      <a href="/admin/access-policy">Access Policy</a>
      <a href="/admin/interactions">Interactions</a>
      <a href="/admin/audit">Audit & Events</a>
      <a href="/admin/terminal-lifecycle">Terminal Lifecycle</a>
      <a href="/admin/device-identities">Device Identities</a>
      <a href="/admin/bot-delivery">Bot Delivery</a>
    </nav>
  </header>
  <main>
    <section>
      <div class="toolbar">
        <button id="refresh" class="primary" type="button">Refresh</button>
        <button id="system-export-json" type="button">Export JSON</button>
        <div class="status" id="status">Ready</div>
      </div>
      <div class="metrics">
        <div class="metric"><span>Health</span><strong id="health-status">-</strong></div>
        <div class="metric"><span>Readiness</span><strong id="readiness-status">-</strong></div>
        <div class="metric">
          <span>Readiness Warnings</span><strong id="readiness-warnings">-</strong>
        </div>
        <div class="metric">
          <span>Readiness Failures</span><strong id="readiness-failures">-</strong>
        </div>
        <div class="metric"><span>Storage</span><strong id="storage">-</strong></div>
        <div class="metric"><span>Projects</span><strong id="projects">-</strong></div>
        <div class="metric"><span>Sessions</span><strong id="sessions">-</strong></div>
        <div class="metric"><span>Lifecycle Running</span><strong id="lifecycle">-</strong></div>
        <div class="metric"><span>Tracked Terms</span><strong id="tracked">-</strong></div>
        <div class="metric"><span>Retry Worker</span><strong id="retry-worker">-</strong></div>
        <div class="metric"><span>Cert Scan</span><strong id="cert-scan-worker">-</strong></div>
        <div class="metric"><span>Bot Platforms</span><strong id="bot-platforms">-</strong></div>
        <div class="metric"><span>Rate Policies</span><strong id="rate-policies">-</strong></div>
      </div>
      <table class="actions" aria-label="Readiness action items">
        <thead>
          <tr>
            <th>Status</th>
            <th>Check</th>
            <th>Next Step</th>
          </tr>
        </thead>
        <tbody id="readiness-actions"></tbody>
      </table>
    </section>
    <section>
      <div class="toolbar">
        <div class="status">Endpoint checks</div>
      </div>
      <table aria-label="System endpoint checks">
        <thead>
          <tr>
            <th>Endpoint</th>
            <th>Status</th>
            <th>HTTP</th>
          </tr>
        </thead>
        <tbody id="checks"></tbody>
      </table>
      <pre id="details">{}</pre>
    </section>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    const checks = [
      ["Control Health", "/api/v1/health"],
      ["Readiness", "/api/v1/readiness"],
      ["Terminal Lifecycle", "/api/v1/terminal/lifecycle-monitor"],
      ["Bot Retry Worker", "/api/v1/bot-gateway/retry-worker"],
      ["Certificate Scan Worker", "/api/v1/device-identities/certificates/scan-worker"],
      ["Bot Capabilities", "/api/v1/bot-gateway/capabilities"],
      ["Bot Rate Limits", "/api/v1/bot-gateway/rate-limits"],
      ["Device Identities", "/api/v1/device-identities?include_revoked=true"],
    ];
    let latestResults = [];

    function setStatus(text) {
      $("status").textContent = text;
    }

    function setText(id, value) {
      $(id).textContent = String(value ?? "-");
    }

    async function readEndpoint(name, url) {
      const response = await fetch(url, {headers: {"content-type": "application/json"}});
      let data = null;
      try {
        data = await response.json();
      } catch {
        data = {message: response.statusText};
      }
      return {name, url, ok: response.ok, status: response.status, data};
    }

    function renderChecks(results) {
      const rows = results.map((result) => {
        const tr = document.createElement("tr");
        for (const value of [
          result.name,
          result.ok ? "ok" : "failed",
          result.status,
        ]) {
          const td = document.createElement("td");
          td.textContent = String(value);
          if (value === "ok") td.className = "ok";
          if (value === "failed") td.className = "fail";
          tr.appendChild(td);
        }
        return tr;
      });
      $("checks").replaceChildren(...rows);
      $("details").textContent = JSON.stringify(results, null, 2);
    }

    function readinessActionChecks(readiness) {
      const checks = Array.isArray(readiness.checks) ? readiness.checks : [];
      return checks
        .filter((check) => check.status && check.status !== "pass")
        .sort((left, right) => {
          const rank = {fail: 0, warn: 1};
          return (rank[left.status] ?? 2) - (rank[right.status] ?? 2);
        });
    }

    function renderReadinessActions(readiness) {
      const actionable = readinessActionChecks(readiness);
      const rows = actionable.map((check) => {
        const tr = document.createElement("tr");
        const values = [
          {text: check.status, className: check.status},
          {text: check.id || "-"},
          {text: check.next_step || check.summary || "-"},
        ];
        for (const value of values) {
          const td = document.createElement("td");
          td.textContent = String(value.text ?? "-");
          if (value.className) td.className = value.className;
          tr.appendChild(td);
        }
        return tr;
      });
      if (!rows.length) {
        const tr = document.createElement("tr");
        const td = document.createElement("td");
        td.colSpan = 3;
        td.className = "ok";
        td.textContent = "All readiness checks passed";
        tr.appendChild(td);
        rows.push(tr);
      }
      $("readiness-actions").replaceChildren(...rows);
    }

    function systemEndpointStatus(results) {
      const failures = results.filter((result) => !result.ok).length;
      return failures ? `${failures} endpoint checks failed` : "All checks passed";
    }

    function systemHealthExportPayload(results) {
      const byName = Object.fromEntries(results.map((result) => [result.name, result]));
      const readiness = byName["Readiness"]?.data || {};
      return {
        schema_version: "agentbridge.admin_system_health_export.v1",
        exported_at: new Date().toISOString(),
        status: systemEndpointStatus(results),
        endpoints: results,
        readiness_actions: readinessActionChecks(readiness).map((check) => ({
          status: check.status || "unknown",
          category: check.category || "unknown",
          id: check.id || "unknown",
          summary: check.summary || "",
          next_step: check.next_step || "",
          evidence: check.evidence || {},
        })),
      };
    }

    function downloadSystemHealthJson() {
      if (!latestResults.length) {
        setStatus("Refresh before export");
        return;
      }
      const payload = systemHealthExportPayload(latestResults);
      const blob = new Blob(
        [JSON.stringify(payload, null, 2) + "\n"],
        {type: "application/json"},
      );
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "agentbridge-system-health.json";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
      setStatus("System health JSON exported");
    }

    function renderMetrics(results) {
      const byName = Object.fromEntries(results.map((result) => [result.name, result]));
      const health = byName["Control Health"]?.data || {};
      const readiness = byName["Readiness"]?.data || {};
      const lifecycle = byName["Terminal Lifecycle"]?.data || {};
      const worker = byName["Bot Retry Worker"]?.data || {};
      const certificateWorker = byName["Certificate Scan Worker"]?.data || {};
      const botCapabilities = byName["Bot Capabilities"]?.data || {};
      const rateLimits = byName["Bot Rate Limits"]?.data || {};
      const platforms = botCapabilities.capabilities || [];
      const policies = rateLimits.policies || [];
      const readinessCounts = readiness.summary?.counts || {};
      setText("health-status", health.status);
      setText("readiness-status", readiness.status);
      setText("readiness-warnings", readinessCounts.warn);
      setText("readiness-failures", readinessCounts.fail);
      setText("storage", health.storage);
      setText("projects", health.projects);
      setText("sessions", health.sessions);
      setText("lifecycle", lifecycle.running);
      setText("tracked", lifecycle.tracked_sessions);
      setText("retry-worker", worker.enabled ? (worker.running ? "running" : "idle") : "off");
      setText(
        "cert-scan-worker",
        certificateWorker.enabled
          ? (certificateWorker.running ? "running" : "idle")
          : "off",
      );
      setText("bot-platforms", platforms.length);
      setText("rate-policies", policies.length);
      renderReadinessActions(readiness);
    }

    async function refresh() {
      setStatus("Loading");
      const results = await Promise.all(
        checks.map(([name, url]) => readEndpoint(name, url)),
      );
      latestResults = results;
      renderChecks(results);
      renderMetrics(results);
      setStatus(systemEndpointStatus(results));
    }

    $("refresh").addEventListener("click", () => {
      refresh().catch((error) => setStatus(error.message));
    });
    $("system-export-json").addEventListener("click", downloadSystemHealthJson);
    refresh().catch((error) => setStatus(error.message));
  </script>
</body>
</html>
"""


ADMIN_AUTH_REQUIRED_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentBridge Admin Login</title>
  <style>
    :root {
      --bg: #f7f8fa;
      --panel: #ffffff;
      --line: #d6dde6;
      --text: #17202a;
      --muted: #5b6878;
      --accent: #0f766e;
      --danger: #b42318;
    }
    * { box-sizing: border-box; }
    body {
      min-height: 100vh;
      margin: 0;
      display: grid;
      place-items: center;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
    }
    main {
      width: min(420px, calc(100vw - 32px));
      padding: 24px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
    }
    h1 {
      margin: 0 0 8px;
      font-size: 18px;
      font-weight: 650;
      letter-spacing: 0;
    }
    p {
      margin: 0 0 16px;
      color: var(--muted);
    }
    form {
      display: grid;
      gap: 12px;
    }
    label {
      display: grid;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    input {
      min-height: 38px;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      color: var(--text);
      font: inherit;
    }
    button {
      min-height: 38px;
      border: 1px solid var(--accent);
      border-radius: 6px;
      background: var(--accent);
      color: #fff;
      cursor: pointer;
      font: inherit;
      font-weight: 650;
    }
    .error {
      min-height: 20px;
      color: var(--danger);
      font-size: 12px;
      font-weight: 650;
    }
  </style>
</head>
<body>
  <main>
    <h1>AgentBridge Admin</h1>
    <p>Enter the configured admin token to unlock this browser session.</p>
    <form method="get">
      <label>
        Admin Token
        <input name="admin_token" type="password" autocomplete="current-password" autofocus>
      </label>
      <button type="submit">Unlock</button>
      <div class="error" id="error"></div>
    </form>
  </main>
</body>
</html>
"""


AUDIT_EVENTS_ADMIN_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentBridge Audit & Events</title>
  <style>
    :root {
      --bg: #f7f8fa;
      --panel: #ffffff;
      --panel-muted: #f0f3f7;
      --line: #d6dde6;
      --text: #17202a;
      --muted: #5b6878;
      --accent: #0f766e;
      --danger: #b42318;
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
      min-height: 56px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 10px 20px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 650;
      letter-spacing: 0;
    }
    nav {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    nav a {
      color: var(--muted);
      text-decoration: none;
      font-weight: 650;
    }
    nav a:hover { color: var(--accent); }
    main {
      display: grid;
      grid-template-columns: minmax(520px, 1fr) minmax(460px, 1fr);
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
    button, input, select {
      min-height: 34px;
      padding: 6px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      font: inherit;
    }
    button {
      cursor: pointer;
      font-weight: 650;
    }
    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
    }
    label {
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .compact { max-width: 180px; }
    .status {
      flex: 1;
      min-width: 180px;
      color: var(--muted);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .table-wrap {
      max-height: calc(50vh - 56px);
      overflow: auto;
      border-bottom: 1px solid var(--line);
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
      z-index: 1;
      color: var(--muted);
      background: #f9fafb;
      font-size: 12px;
      font-weight: 700;
    }
    tr { cursor: pointer; }
    tr.selected td { background: #e8f5f3; }
    pre {
      margin: 14px;
      min-height: 180px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fbfcfe;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      overflow: auto;
    }
    .danger { color: var(--danger); }
    @media (max-width: 980px) {
      main { grid-template-columns: 1fr; }
      section { border-right: 0; border-bottom: 1px solid var(--line); }
      .compact { max-width: none; }
      header { align-items: flex-start; flex-direction: column; }
    }
  </style>
</head>
<body>
  <header>
    <h1>AgentBridge Audit & Events</h1>
    <nav>
      <a href="/admin">Admin</a>
      <a href="/admin/system">System Health</a>
      <a href="/admin/projects">Projects & Sessions</a>
      <a href="/admin/interactions">Interactions</a>
      <a href="/admin/access-policy">Access Policy</a>
      <a href="/admin/terminal-lifecycle">Terminal Lifecycle</a>
      <a href="/admin/device-identities">Device Identities</a>
      <a href="/admin/bot-delivery">Bot Delivery</a>
    </nav>
  </header>
  <main>
    <section>
      <div class="toolbar">
        <button id="audit-refresh" type="button">Refresh Audit</button>
        <button id="audit-export-json" type="button">Export JSON</button>
        <button id="audit-export-csv" type="button">Export CSV</button>
        <button id="audit-export-archive" type="button">Export Archive</button>
        <label class="compact">
          Action
          <input id="audit-action" autocomplete="off" placeholder="session.created">
        </label>
        <label class="compact">
          Actor
          <input id="audit-actor" autocomplete="off" placeholder="usr_1">
        </label>
        <label class="compact">
          Session
          <input id="audit-session" autocomplete="off" placeholder="optional">
        </label>
        <label class="compact">
          Query
          <input id="audit-query" autocomplete="off" placeholder="details text">
        </label>
        <label class="compact">
          Details Field
          <input id="audit-details-field" autocomplete="off" placeholder="workspace_id">
        </label>
        <label class="compact">
          Details Value
          <input id="audit-details-value" autocomplete="off" placeholder="exact value">
        </label>
        <label class="compact">
          From
          <input id="audit-created-from" type="datetime-local">
        </label>
        <label class="compact">
          To
          <input id="audit-created-to" type="datetime-local">
        </label>
        <label class="compact">
          Limit
          <input id="audit-limit" type="number" value="100" min="1" max="500">
        </label>
        <div class="status" id="audit-status">Ready</div>
      </div>
      <div class="table-wrap">
        <table aria-label="Audit records">
          <thead>
            <tr>
              <th>Action</th>
              <th>Actor</th>
              <th>Outcome</th>
              <th>Session</th>
              <th>Trace</th>
            </tr>
          </thead>
          <tbody id="audit-records"></tbody>
        </table>
      </div>
      <pre id="audit-selected">{}</pre>
    </section>
    <section>
      <div class="toolbar">
        <button id="event-refresh" type="button">Replay Session</button>
        <button id="event-search" type="button">Search Events</button>
        <button id="event-live-connect" class="primary" type="button">Connect Live</button>
        <button id="event-live-disconnect" type="button" disabled>Disconnect</button>
        <label class="compact">
          Project ID
          <input id="event-project" autocomplete="off">
        </label>
        <label class="compact">
          Session ID
          <input id="event-session" autocomplete="off">
        </label>
        <label class="compact">
          Type
          <input id="event-type" autocomplete="off" placeholder="assistant.delta">
        </label>
        <label class="compact">
          Source
          <select id="event-source">
            <option value=""></option>
            <option value="control_plane">control_plane</option>
            <option value="terminal_agent">terminal_agent</option>
            <option value="bot_gateway">bot_gateway</option>
            <option value="admin_web">admin_web</option>
          </select>
        </label>
        <label class="compact">
          Trace
          <input id="event-trace" autocomplete="off" placeholder="optional">
        </label>
        <label class="compact">
          Turn
          <input id="event-turn" autocomplete="off" placeholder="optional">
        </label>
        <label class="compact">
          Interaction
          <input id="event-interaction" autocomplete="off" placeholder="optional">
        </label>
        <label class="compact">
          Query
          <input id="event-query" autocomplete="off" placeholder="payload text">
        </label>
        <label class="compact">
          Payload Field
          <input id="event-payload-field" autocomplete="off" placeholder="text">
        </label>
        <label class="compact">
          Payload Value
          <input id="event-payload-value" autocomplete="off" placeholder="exact value">
        </label>
        <label class="compact">
          From
          <input id="event-created-from" type="datetime-local">
        </label>
        <label class="compact">
          To
          <input id="event-created-to" type="datetime-local">
        </label>
        <label class="compact">
          After Seq
          <input id="event-after" type="number" min="0" placeholder="optional">
        </label>
        <label class="compact">
          Limit
          <input id="event-limit" type="number" value="100" min="1" max="500">
        </label>
        <div class="status" id="event-status">Ready</div>
      </div>
      <div class="table-wrap">
        <table aria-label="Semantic events">
          <thead>
            <tr>
              <th>Seq</th>
              <th>Type</th>
              <th>Source</th>
              <th>Session</th>
              <th>Trace</th>
            </tr>
          </thead>
          <tbody id="events"></tbody>
        </table>
      </div>
      <pre id="event-selected">{}</pre>
      <pre id="error" class="danger"></pre>
    </section>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    let selectedAuditId = "";
    let selectedEventId = "";
    let displayedEvents = [];
    let eventSocket = null;
    let latestEventSeq = 0;

    function setText(id, value) {
      $(id).textContent = String(value ?? "");
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

    function appendCell(row, value) {
      const td = document.createElement("td");
      td.textContent = String(value ?? "");
      td.title = String(value ?? "");
      row.appendChild(td);
    }

    function paramsFrom(fields) {
      const params = new URLSearchParams();
      for (const [name, id] of fields) {
        const value = $(id).value.trim();
        if (value) params.set(name, value);
      }
      const suffix = params.toString();
      return suffix ? `?${suffix}` : "";
    }

    function auditParams() {
      return paramsFrom([
        ["action", "audit-action"],
        ["actor_id", "audit-actor"],
        ["session_id", "audit-session"],
        ["q", "audit-query"],
        ["details_field", "audit-details-field"],
        ["details_value", "audit-details-value"],
        ["created_from", "audit-created-from"],
        ["created_to", "audit-created-to"],
        ["limit", "audit-limit"],
      ]);
    }

    function auditExportUrl(format) {
      const params = new URLSearchParams(auditParams());
      params.set("format", format);
      return `/api/v1/audit/export?${params.toString()}`;
    }

    function downloadAudit(format) {
      window.location.assign(auditExportUrl(format));
      setText("audit-status", `Exporting ${format.toUpperCase()}`);
    }

    function renderAudit(records) {
      const rows = records.map((record) => {
        const tr = document.createElement("tr");
        tr.dataset.auditId = record.id;
        tr.className = record.id === selectedAuditId ? "selected" : "";
        for (const value of [
          record.action,
          record.actor_id,
          record.outcome,
          record.session_id || "",
          record.trace_id,
        ]) {
          appendCell(tr, value);
        }
        tr.addEventListener("click", () => {
          selectedAuditId = record.id;
          $("audit-selected").textContent = JSON.stringify(record, null, 2);
          document.querySelectorAll("#audit-records tr").forEach((row) => {
            row.classList.toggle("selected", row.dataset.auditId === selectedAuditId);
          });
          if (record.project_id) $("event-project").value = record.project_id;
          if (record.session_id) $("event-session").value = record.session_id;
          if (record.trace_id) $("event-trace").value = record.trace_id;
        });
        return tr;
      });
      $("audit-records").replaceChildren(...rows);
      setText("audit-status", `${records.length} audit records`);
    }

    function eventLimitValue() {
      const value = Number.parseInt($("event-limit").value, 10);
      if (!Number.isFinite(value)) return 100;
      return Math.max(1, Math.min(value, 500));
    }

    function requestedAfterSeq() {
      const value = Number.parseInt($("event-after").value, 10);
      if (!Number.isFinite(value)) return 0;
      return Math.max(0, value);
    }

    function eventRow(event) {
        const tr = document.createElement("tr");
        tr.dataset.eventId = event.id;
        tr.className = event.id === selectedEventId ? "selected" : "";
        for (const value of [
          event.seq,
          event.type,
          event.source,
          event.session_id || "",
          event.trace_id,
        ]) {
          appendCell(tr, value);
        }
        tr.addEventListener("click", () => {
          selectedEventId = event.id;
          $("event-selected").textContent = JSON.stringify(event, null, 2);
          document.querySelectorAll("#events tr").forEach((row) => {
            row.classList.toggle("selected", row.dataset.eventId === selectedEventId);
          });
        });
        return tr;
    }

    function renderEventRows() {
      const rows = displayedEvents.map(eventRow);
      $("events").replaceChildren(...rows);
      setText("event-status", `${displayedEvents.length} events`);
    }

    function renderEvents(events) {
      displayedEvents = events;
      latestEventSeq = Math.max(0, ...events.map((event) => Number(event.seq) || 0));
      renderEventRows();
    }

    function appendLiveEvent(event) {
      latestEventSeq = Math.max(latestEventSeq, Number(event.seq) || 0);
      displayedEvents = [...displayedEvents, event].slice(-eventLimitValue());
      renderEventRows();
      const wrap = $("events").parentElement.parentElement;
      wrap.scrollTop = wrap.scrollHeight;
    }

    function eventStreamUrl(sessionId) {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const params = new URLSearchParams();
      params.set("after_seq", String(Math.max(latestEventSeq, requestedAfterSeq())));
      params.set("limit", String(eventLimitValue()));
      const path = `/api/v1/sessions/${encodeURIComponent(sessionId)}/events/ws`;
      return `${protocol}//${window.location.host}${path}?${params.toString()}`;
    }

    function setLiveControls(connected) {
      $("event-live-connect").disabled = connected;
      $("event-live-disconnect").disabled = !connected;
    }

    function disconnectEventsLive() {
      if (eventSocket) {
        const socket = eventSocket;
        eventSocket = null;
        socket.close(1000, "admin disconnect");
      }
      setLiveControls(false);
      setText("event-status", `${displayedEvents.length} events`);
    }

    function connectEventsLive() {
      const sessionId = $("event-session").value.trim();
      if (!sessionId) throw new Error("Session ID is required");
      disconnectEventsLive();
      const socket = new WebSocket(eventStreamUrl(sessionId));
      eventSocket = socket;
      setLiveControls(true);
      setText("event-status", "Connecting live");
      socket.addEventListener("open", () => {
        setText("event-status", `Live from seq ${Math.max(latestEventSeq, requestedAfterSeq())}`);
      });
      socket.addEventListener("message", (message) => {
        const frame = JSON.parse(message.data);
        if (frame.type === "semantic_event") {
          appendLiveEvent(frame.event);
          setText("event-status", `${displayedEvents.length} events, live`);
        } else if (frame.type === "error") {
          $("error").textContent = frame.error?.message || "WebSocket error";
          disconnectEventsLive();
        } else if (frame.type === "idle_timeout") {
          setText("event-status", `Live idle at seq ${frame.last_seq}`);
        }
      });
      socket.addEventListener("error", () => {
        $("error").textContent = "Event WebSocket error";
      });
      socket.addEventListener("close", () => {
        if (eventSocket === socket) eventSocket = null;
        setLiveControls(false);
      });
    }

    async function refreshAudit() {
      setText("audit-status", "Loading");
      const records = await requestJson(`/api/v1/audit${auditParams()}`);
      renderAudit(records);
    }

    async function refreshEvents() {
      const sessionId = $("event-session").value.trim();
      if (!sessionId) throw new Error("Session ID is required");
      setText("event-status", "Loading");
      const events = await requestJson(
        `/api/v1/sessions/${encodeURIComponent(sessionId)}/events${paramsFrom([
          ["after_seq", "event-after"],
          ["limit", "event-limit"],
        ])}`,
      );
      renderEvents(events);
    }

    async function searchEvents() {
      setText("event-status", "Searching");
      const events = await requestJson(`/api/v1/events${paramsFrom([
        ["project_id", "event-project"],
        ["session_id", "event-session"],
        ["event_type", "event-type"],
        ["source", "event-source"],
        ["trace_id", "event-trace"],
        ["turn_id", "event-turn"],
        ["interaction_id", "event-interaction"],
        ["q", "event-query"],
        ["payload_field", "event-payload-field"],
        ["payload_value", "event-payload-value"],
        ["created_from", "event-created-from"],
        ["created_to", "event-created-to"],
        ["limit", "event-limit"],
      ])}`);
      renderEvents(events);
      setText("event-status", `${events.length} events, newest first`);
    }

    async function run(action) {
      try {
        $("error").textContent = "";
        await action();
      } catch (error) {
        $("error").textContent = error.message;
      }
    }

    $("audit-refresh").addEventListener("click", () => run(refreshAudit));
    $("audit-export-json").addEventListener("click", () => run(() => downloadAudit("json")));
    $("audit-export-csv").addEventListener("click", () => run(() => downloadAudit("csv")));
    $("audit-export-archive").addEventListener("click", () => run(() => downloadAudit("archive")));
    $("event-refresh").addEventListener("click", () => run(refreshEvents));
    $("event-search").addEventListener("click", () => run(searchEvents));
    $("event-live-connect").addEventListener("click", () => run(connectEventsLive));
    $("event-live-disconnect").addEventListener("click", disconnectEventsLive);
    refreshAudit().catch((error) => {
      $("error").textContent = error.message;
      setText("audit-status", error.message);
    });
  </script>
</body>
</html>
"""


PROJECT_SESSION_ADMIN_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentBridge Projects & Sessions</title>
  <style>
    :root {
      --bg: #f7f8fa;
      --panel: #ffffff;
      --panel-muted: #f0f3f7;
      --line: #d6dde6;
      --text: #17202a;
      --muted: #5b6878;
      --accent: #0f766e;
      --danger: #b42318;
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
      min-height: 56px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 10px 20px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 650;
      letter-spacing: 0;
    }
    nav {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    nav a {
      color: var(--muted);
      text-decoration: none;
      font-weight: 650;
    }
    nav a:hover { color: var(--accent); }
    main {
      display: grid;
      grid-template-columns: minmax(460px, .95fr) minmax(520px, 1.05fr);
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
    button, input, select, textarea {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      font: inherit;
    }
    button {
      min-height: 34px;
      padding: 6px 10px;
      cursor: pointer;
      font-weight: 650;
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
    input, select {
      min-height: 34px;
      width: 100%;
      padding: 6px 8px;
    }
    textarea {
      min-height: 68px;
      width: 100%;
      padding: 8px;
      resize: vertical;
    }
    label {
      display: grid;
      gap: 5px;
      min-width: 0;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .compact { max-width: 180px; }
    .checkbox-field {
      display: flex;
      align-items: center;
      gap: 8px;
      min-height: 34px;
      padding-top: 18px;
    }
    .checkbox-field input {
      width: auto;
      min-height: auto;
      padding: 0;
    }
    .status {
      flex: 1;
      min-width: 180px;
      color: var(--muted);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .field-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      padding: 14px;
      border-bottom: 1px solid var(--line);
    }
    .wide { grid-column: 1 / -1; }
    .metrics {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      padding: 14px;
      border-bottom: 1px solid var(--line);
    }
    .metric {
      min-height: 76px;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }
    .metric span {
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .metric strong {
      display: block;
      margin-top: 6px;
      font-size: 22px;
      font-weight: 700;
    }
    .table-wrap {
      max-height: 42vh;
      overflow: auto;
      border-bottom: 1px solid var(--line);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    .ops-table {
      min-width: 980px;
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
      z-index: 1;
      color: var(--muted);
      background: #f9fafb;
      font-size: 12px;
      font-weight: 700;
    }
    tr {
      cursor: pointer;
    }
    tr.selected td {
      background: #e8f5f3;
    }
    pre {
      margin: 0 14px 14px;
      padding: 10px;
      min-height: 118px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fbfcfe;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      overflow: auto;
    }
    .danger { color: var(--danger); }
    @media (max-width: 980px) {
      main { grid-template-columns: 1fr; }
      section { border-right: 0; border-bottom: 1px solid var(--line); }
      .field-grid, .metrics { grid-template-columns: 1fr; }
      header { align-items: flex-start; flex-direction: column; }
      .compact { max-width: none; }
    }
  </style>
</head>
<body>
  <header>
    <h1>AgentBridge Projects & Sessions</h1>
    <nav>
      <a href="/admin">Admin</a>
      <a href="/admin/system">System Health</a>
      <a href="/admin/access-policy">Access Policy</a>
      <a href="/admin/interactions">Interactions</a>
      <a href="/admin/audit">Audit & Events</a>
      <a href="/admin/terminal-lifecycle">Terminal Lifecycle</a>
      <a href="/admin/device-identities">Device Identities</a>
      <a href="/admin/bot-delivery">Bot Delivery</a>
    </nav>
  </header>
  <main>
    <section>
      <div class="toolbar">
        <button id="refresh" type="button">Refresh</button>
        <button id="project-session-export-json" type="button">Export JSON</button>
        <label class="compact">
          Actor ID
          <input id="actor-id" value="admin-ui">
        </label>
        <label class="compact">
          Actor Roles
          <input id="actor-roles" value="admin">
        </label>
        <label class="compact">
          Chat Context
          <input id="binding-chat-context-id" autocomplete="off" placeholder="ctx_...">
        </label>
        <button id="binding-refresh" type="button">Load Bindings</button>
        <div class="status" id="status">Ready</div>
      </div>
      <div class="table-wrap">
        <table aria-label="Projects">
          <thead>
            <tr>
              <th>Name</th>
              <th>Slug</th>
              <th>Status</th>
              <th>Agent</th>
              <th>Max Sessions</th>
              <th>Max Running</th>
              <th>Max Queue</th>
              <th>Daily/User</th>
            </tr>
          </thead>
          <tbody id="projects"></tbody>
        </table>
      </div>
      <div class="field-grid">
        <label>
          Project Name
          <input id="project-name" autocomplete="off" placeholder="Backend">
        </label>
        <label>
          Slug
          <input id="project-slug" autocomplete="off" placeholder="optional">
        </label>
        <label>
          Default Agent
          <select id="project-agent">
            <option value="claude">claude</option>
            <option value="codex">codex</option>
            <option value="generic_tui">generic_tui</option>
          </select>
        </label>
        <label>
          Aliases
          <input id="project-aliases" autocomplete="off" placeholder="backend, api">
        </label>
        <label>
          Max Active Sessions
          <input id="project-max-active-sessions" type="number" min="0" step="1" value="10">
        </label>
        <label>
          Max Running Turns
          <input id="project-max-running-turns" type="number" min="0" step="1" value="4">
        </label>
        <label>
          Max Queued Turns
          <input id="project-max-queued-turns" type="number" min="0" step="1" value="100">
        </label>
        <label>
          Daily Turns/User
          <input id="project-daily-turns-per-user" type="number" min="0" step="1" value="50">
        </label>
        <label class="wide">
          Description
          <textarea id="project-description"></textarea>
        </label>
        <button class="primary wide" id="create-project" type="button">Create Project</button>
      </div>
      <pre id="project-json">{}</pre>
    </section>

    <section>
      <div class="metrics">
        <div class="metric">
          <span>Selected Project</span><strong id="selected-project">-</strong>
        </div>
        <div class="metric">
          <span>Workspaces</span><strong id="workspace-count">-</strong>
        </div>
        <div class="metric">
          <span>Sessions</span><strong id="session-count">-</strong>
        </div>
        <div class="metric">
          <span>Bindings</span><strong id="binding-count">-</strong>
        </div>
      </div>

      <div class="table-wrap">
        <table aria-label="Project Bindings">
          <thead>
            <tr>
              <th>Project</th>
              <th>Alias</th>
              <th>Default</th>
              <th>Binding</th>
            </tr>
          </thead>
          <tbody id="project-bindings"></tbody>
        </table>
      </div>

      <div class="field-grid">
        <label>
          Workspace Path
          <input id="workspace-path" autocomplete="off" placeholder="/path/to/project">
        </label>
        <label>
          Allowed Root
          <input id="workspace-root" autocomplete="off" placeholder="/path/to">
        </label>
        <label>
          Machine ID
          <input id="workspace-machine" autocomplete="off" value="local">
        </label>
        <label>
          Workspace Type
          <select id="workspace-type">
            <option value="shared">shared</option>
            <option value="exclusive">exclusive</option>
            <option value="git_worktree">git_worktree</option>
            <option value="ephemeral_copy">ephemeral_copy</option>
            <option value="read_only">read_only</option>
          </select>
        </label>
        <label class="checkbox-field">
          <input id="workspace-writable" type="checkbox" checked>
          <span>Writable</span>
        </label>
        <label>
          Max Write Sessions
          <input id="workspace-max-write-sessions" type="number" min="0" step="1" value="1">
        </label>
        <button class="primary wide" id="add-workspace" type="button">Add Workspace</button>
      </div>

      <div class="table-wrap">
        <table aria-label="Workspaces">
          <thead>
            <tr>
              <th>Path</th>
              <th>Root</th>
              <th>Machine</th>
              <th>Type</th>
              <th>Writable</th>
              <th>Max Write</th>
            </tr>
          </thead>
          <tbody id="workspaces"></tbody>
        </table>
      </div>

      <div class="field-grid">
        <label>
          Session Name
          <input id="session-name" autocomplete="off" value="AgentBridge Session">
        </label>
        <label>
          Workspace
          <select id="session-workspace-id"></select>
        </label>
        <label>
          Agent Type
          <select id="session-agent">
            <option value="claude">claude</option>
            <option value="codex">codex</option>
            <option value="generic_tui">generic_tui</option>
          </select>
        </label>
        <label>
          Visibility
          <select id="session-visibility">
            <option value="group">group</option>
            <option value="private">private</option>
            <option value="thread">thread</option>
            <option value="project">project</option>
            <option value="organization">organization</option>
          </select>
        </label>
        <button class="primary" id="create-session" type="button">Create Session</button>
        <button class="danger" id="close-session" type="button">Close Selected Session</button>
      </div>

      <div class="table-wrap">
        <table aria-label="Sessions" class="ops-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Code</th>
              <th>Status</th>
              <th>Agent</th>
              <th>Workspace</th>
              <th>Active Turn</th>
              <th>Queue</th>
              <th>Pending Approvals</th>
              <th>Lease</th>
            </tr>
          </thead>
          <tbody id="sessions"></tbody>
        </table>
      </div>

      <div class="toolbar">
        <button id="queue-refresh" type="button">Refresh Queue</button>
        <button id="queue-pause" type="button">Pause Queue</button>
        <button id="queue-resume" type="button">Resume Queue</button>
        <button class="danger" id="queue-clear" type="button">Clear Queue</button>
        <div class="status" id="queue-status">No session selected</div>
      </div>
      <div class="metrics">
        <div class="metric">
          <span>Queued Turns</span><strong id="queue-count">-</strong>
        </div>
        <div class="metric">
          <span>Queue State</span><strong id="queue-state">-</strong>
        </div>
        <div class="metric">
          <span>Queue Version</span><strong id="queue-version">-</strong>
        </div>
      </div>
      <div class="table-wrap">
        <table aria-label="Queued Turns">
          <thead>
            <tr>
              <th>Turn</th>
              <th>Actor</th>
              <th>Status</th>
              <th>Order</th>
              <th>Queued At</th>
            </tr>
          </thead>
          <tbody id="queue-turns"></tbody>
        </table>
      </div>
      <pre id="queue-json">{}</pre>
      <pre id="session-json">{}</pre>
      <pre id="error" class="danger"></pre>
    </section>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    let projects = [];
    let selectedProjectId = "";
    let selectedSessionId = "";
    let queueState = null;
    let projectsLoaded = false;
    let currentWorkspaces = [];
    let currentSessions = [];
    let currentProjectBindings = null;
    let sessionQueues = new Map();
    let sessionLeases = new Map();
    let sessionPendingApprovals = new Map();

    function csv(value) {
      return value.split(",").map((item) => item.trim()).filter(Boolean);
    }

    function actor() {
      return {
        id: $("actor-id").value.trim() || "admin-ui",
        roles: csv($("actor-roles").value || "admin"),
      };
    }

    function optional(value) {
      const trimmed = value.trim();
      return trimmed ? trimmed : null;
    }

    function readProjectMaxActiveSessions() {
      const parsed = Number.parseInt($("project-max-active-sessions").value || "10", 10);
      return Number.isFinite(parsed) ? Math.max(0, parsed) : 10;
    }

    function readProjectMaxRunningTurns() {
      const parsed = Number.parseInt($("project-max-running-turns").value || "4", 10);
      return Number.isFinite(parsed) ? Math.max(0, parsed) : 4;
    }

    function readProjectMaxQueuedTurns() {
      const parsed = Number.parseInt($("project-max-queued-turns").value || "100", 10);
      return Number.isFinite(parsed) ? Math.max(0, parsed) : 100;
    }

    function readProjectDailyTurnsPerUser() {
      const parsed = Number.parseInt($("project-daily-turns-per-user").value || "50", 10);
      return Number.isFinite(parsed) ? Math.max(0, parsed) : 50;
    }

    function setStatus(text) {
      $("status").textContent = text;
    }

    function setText(id, value) {
      $(id).textContent = String(value ?? "-");
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

    function appendCell(row, value) {
      const td = document.createElement("td");
      td.textContent = String(value ?? "");
      td.title = String(value ?? "");
      row.appendChild(td);
    }

    function formatSessionQueue(session) {
      const queue = sessionQueues.get(session.id);
      if (!queue) return session.queue_paused ? "paused" : "-";
      const count = (queue.turns || []).length;
      return `${count} ${queue.queue_paused ? "paused" : "active"}`;
    }

    function formatSessionLease(session) {
      const lease = sessionLeases.get(session.id);
      if (!lease) return "none";
      return `${lease.owner_type}:${lease.owner_id} #${lease.epoch}`;
    }

    function formatSessionPendingApprovals(session) {
      const summary = sessionPendingApprovals.get(session.id);
      if (!summary) return "-";
      const total = summary.pending + summary.partially_approved;
      if (!total) return "0";
      return `${total} open`;
    }

    function sessionDetail(session) {
      return {
        ...session,
        queue: sessionQueues.get(session.id) || null,
        lease: sessionLeases.get(session.id) || null,
        pending_approvals: sessionPendingApprovals.get(session.id) || null,
      };
    }

    function renderProjects() {
      const rows = projects.map((project) => {
        const tr = document.createElement("tr");
        tr.dataset.projectId = project.id;
        tr.className = project.id === selectedProjectId ? "selected" : "";
        for (const value of [
          project.name,
          project.slug,
          project.status,
          project.default_agent,
          project.max_active_sessions,
          project.max_running_turns,
          project.max_queued_turns,
          project.daily_turns_per_user,
        ]) {
          appendCell(tr, value);
        }
        tr.addEventListener("click", () => selectProject(project.id));
        return tr;
      });
      $("projects").replaceChildren(...rows);
    }

    function renderWorkspaces(workspaces) {
      currentWorkspaces = workspaces;
      const rows = workspaces.map((workspace) => {
        const tr = document.createElement("tr");
        for (const value of [
          workspace.path,
          workspace.allowed_root,
          workspace.machine_id,
          workspace.type,
          workspace.is_writable ? "writable" : "read-only",
          workspace.max_write_sessions,
        ]) {
          appendCell(tr, value);
        }
        return tr;
      });
      $("workspaces").replaceChildren(...rows);
      const options = workspaces.map((workspace) => {
        const option = document.createElement("option");
        option.value = workspace.id;
        option.textContent = `${workspace.id} ${workspace.path}`;
        return option;
      });
      if (options.length === 0) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "Add a workspace first";
        options.push(option);
      }
      $("session-workspace-id").replaceChildren(...options);
      setText("workspace-count", workspaces.length);
    }

    function renderProjectBindings(bindingState) {
      currentProjectBindings = bindingState;
      if (!bindingState) {
        setText("binding-count", "-");
        $("project-bindings").replaceChildren();
        return;
      }
      const projectsById = bindingState.projects || {};
      const rows = (bindingState.bindings || []).map((binding) => {
        const tr = document.createElement("tr");
        const project = projectsById[binding.project_id] || {};
        for (const value of [
          project.slug || binding.project_id,
          binding.alias_in_chat || "-",
          binding.is_default ? "default" : "-",
          binding.id,
        ]) {
          appendCell(tr, value);
        }
        if (binding.project_id === selectedProjectId) {
          tr.className = "selected";
        }
        return tr;
      });
      $("project-bindings").replaceChildren(...rows);
      setText("binding-count", rows.length);
    }

    function renderSessions(sessions) {
      currentSessions = sessions;
      const rows = sessions.map((session) => {
        const tr = document.createElement("tr");
        tr.dataset.sessionId = session.id;
        tr.className = session.id === selectedSessionId ? "selected" : "";
        for (const value of [
          session.name,
          session.short_code,
          session.status,
          session.agent_type,
          session.workspace_id,
          session.active_turn_id || "-",
          formatSessionQueue(session),
          formatSessionPendingApprovals(session),
          formatSessionLease(session),
        ]) {
          appendCell(tr, value);
        }
        tr.addEventListener("click", () => selectSession(session));
        return tr;
      });
      $("sessions").replaceChildren(...rows);
      setText("session-count", sessions.length);
      if (selectedSessionId && !sessions.some((session) => session.id === selectedSessionId)) {
        selectedSessionId = "";
        $("session-json").textContent = "{}";
        renderQueue(null);
      }
    }

    async function refreshSessionOperations(sessions) {
      sessionQueues = new Map();
      sessionLeases = new Map();
      sessionPendingApprovals = new Map();
      await Promise.all(sessions.map(async (session) => {
        const encodedSession = encodeURIComponent(session.id);
        const [
          queue,
          lease,
          pendingInteractions,
          partiallyApprovedInteractions,
        ] = await Promise.all([
          requestJson(`/api/v1/sessions/${encodedSession}/queue`),
          requestJson(`/api/v1/sessions/${encodedSession}/lease`),
          requestJson(`/api/v1/interactions?session_id=${encodedSession}&status=pending`),
          requestJson(
            `/api/v1/interactions?session_id=${encodedSession}&status=partially_approved`,
          ),
        ]);
        const pendingApprovals = pendingInteractions.filter(
          (interaction) => interaction.type === "approval",
        );
        const partiallyApprovedApprovals = partiallyApprovedInteractions.filter(
          (interaction) => interaction.type === "approval",
        );
        sessionQueues.set(session.id, queue);
        sessionLeases.set(session.id, lease);
        sessionPendingApprovals.set(session.id, {
          pending: pendingApprovals.length,
          partially_approved: partiallyApprovedApprovals.length,
        });
      }));
    }

    function renderQueue(queue) {
      queueState = queue;
      if (!queue) {
        setText("queue-count", "-");
        setText("queue-state", "-");
        setText("queue-version", "-");
        $("queue-status").textContent = selectedSessionId
          ? "Queue not loaded"
          : "No session selected";
        $("queue-turns").replaceChildren();
        $("queue-json").textContent = "{}";
        return;
      }
      const turns = queue.turns || [];
      setText("queue-count", turns.length);
      setText("queue-state", queue.queue_paused ? "paused" : "active");
      setText("queue-version", queue.queue_version);
      $("queue-status").textContent = `${turns.length} queued`;
      const rows = turns.map((turn) => {
        const tr = document.createElement("tr");
        for (const value of [
          turn.id,
          turn.actor_id,
          turn.status,
          turn.queue_order,
          turn.queued_at,
        ]) {
          appendCell(tr, value);
        }
        return tr;
      });
      $("queue-turns").replaceChildren(...rows);
      $("queue-json").textContent = JSON.stringify(queue, null, 2);
    }

    function mapObject(map) {
      return Object.fromEntries(Array.from(map.entries()));
    }

    function projectSessionExportPayload() {
      const selectedProject = projects.find((project) => project.id === selectedProjectId) || null;
      return {
        schema_version: "agentbridge.admin_project_session_export.v1",
        exported_at: new Date().toISOString(),
        selected_project_id: selectedProjectId || null,
        selected_session_id: selectedSessionId || null,
        project_count: projects.length,
        workspace_count: currentWorkspaces.length,
        session_count: currentSessions.length,
        chat_context_id: $("binding-chat-context-id").value.trim() || null,
        projects,
        selected_project: selectedProject,
        project_bindings: currentProjectBindings,
        workspaces: currentWorkspaces,
        sessions: currentSessions.map(sessionDetail),
        queues_by_session: mapObject(sessionQueues),
        leases_by_session: mapObject(sessionLeases),
        pending_approvals_by_session: mapObject(sessionPendingApprovals),
        selected_queue: queueState,
      };
    }

    function downloadProjectSessionJson() {
      if (!projectsLoaded) {
        setStatus("Refresh before export");
        return;
      }
      const payload = projectSessionExportPayload();
      const blob = new Blob(
        [JSON.stringify(payload, null, 2) + "\n"],
        {type: "application/json"},
      );
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "agentbridge-projects-sessions.json";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
      setStatus("Project/session JSON exported");
    }

    function readWorkspaceMaxWriteSessions() {
      const parsed = Number.parseInt($("workspace-max-write-sessions").value || "0", 10);
      return Number.isFinite(parsed) ? parsed : 0;
    }

    function syncWorkspaceWritePolicy() {
      const readOnly = $("workspace-type").value === "read_only";
      $("workspace-writable").checked = !readOnly;
      $("workspace-writable").disabled = readOnly;
      if (readOnly) {
        $("workspace-max-write-sessions").value = "0";
        return;
      }
      if (readWorkspaceMaxWriteSessions() < 1) {
        $("workspace-max-write-sessions").value = "1";
      }
    }

    async function loadProjects() {
      setStatus("Loading projects");
      projects = await requestJson("/api/v1/projects");
      projectsLoaded = true;
      if (selectedProjectId && !projects.some((project) => project.id === selectedProjectId)) {
        selectedProjectId = "";
      }
      if (!selectedProjectId && projects.length > 0) {
        selectedProjectId = projects[0].id;
      }
      renderProjects();
      await refreshSelectedProject();
      setStatus(`${projects.length} projects`);
    }

    async function loadProjectBindings() {
      const chatContextId = $("binding-chat-context-id").value.trim();
      if (!chatContextId) {
        renderProjectBindings(null);
        setStatus("Enter a chat context ID to load bindings");
        return null;
      }
      const bindingState = await requestJson(
        `/api/v1/chat-spaces/${encodeURIComponent(chatContextId)}/project-bindings`,
      );
      renderProjectBindings(bindingState);
      setStatus(`${bindingState.bindings.length} project bindings`);
      return bindingState;
    }

    async function refreshSelectedProject() {
      const project = projects.find((item) => item.id === selectedProjectId);
      setText("selected-project", project ? project.slug : "-");
      $("project-json").textContent = JSON.stringify(project || {}, null, 2);
      if (!project) {
        sessionQueues = new Map();
        sessionLeases = new Map();
        sessionPendingApprovals = new Map();
        renderWorkspaces([]);
        renderSessions([]);
        return;
      }
      const encoded = encodeURIComponent(project.id);
      const [workspaces, sessions] = await Promise.all([
        requestJson(`/api/v1/projects/${encoded}/workspaces`),
        requestJson(`/api/v1/sessions?project_id=${encoded}`),
      ]);
      await refreshSessionOperations(sessions);
      renderWorkspaces(workspaces);
      if (currentProjectBindings) {
        renderProjectBindings(currentProjectBindings);
      }
      renderSessions(sessions);
      const selectedSession = currentSessions.find((session) => session.id === selectedSessionId);
      if (selectedSession) {
        $("session-json").textContent = JSON.stringify(sessionDetail(selectedSession), null, 2);
      }
      if (selectedSessionId) {
        renderQueue(sessionQueues.get(selectedSessionId) || null);
      } else {
        renderQueue(null);
      }
    }

    async function selectProject(projectId) {
      selectedProjectId = projectId;
      selectedSessionId = "";
      $("session-json").textContent = "{}";
      renderQueue(null);
      renderProjects();
      await refreshSelectedProject();
      setStatus(`Selected ${projectId}`);
    }

    function selectSession(session) {
      selectedSessionId = session.id;
      $("session-json").textContent = JSON.stringify(sessionDetail(session), null, 2);
      document.querySelectorAll("#sessions tr").forEach((row) => {
        row.classList.toggle("selected", row.dataset.sessionId === selectedSessionId);
      });
      loadQueue().catch((error) => {
        $("error").textContent = error.message;
        setStatus(error.message);
      });
    }

    async function loadQueue() {
      if (!selectedSessionId) {
        renderQueue(null);
        return null;
      }
      const queue = await requestJson(
        `/api/v1/sessions/${encodeURIComponent(selectedSessionId)}/queue`,
      );
      sessionQueues.set(selectedSessionId, queue);
      renderQueue(queue);
      const selectedSession = currentSessions.find((session) => session.id === selectedSessionId);
      if (selectedSession) {
        $("session-json").textContent = JSON.stringify(sessionDetail(selectedSession), null, 2);
      }
      renderSessions(currentSessions);
      return queue;
    }

    async function setQueuePaused(paused) {
      if (!selectedSessionId) throw new Error("Select a session first");
      if (!queueState?.queue_version) await loadQueue();
      const action = paused ? "pause" : "resume";
      const result = await requestJson(
        `/api/v1/sessions/${encodeURIComponent(selectedSessionId)}/queue/${action}`,
        {
          method: "POST",
          body: JSON.stringify({
            actor: actor(),
            expected_queue_version: queueState.queue_version,
            trace_id: `admin-ui-queue-${action}`,
          }),
        },
      );
      await loadQueue();
      setStatus(`Queue ${result.queue_paused ? "paused" : "resumed"}`);
    }

    async function clearQueue() {
      if (!selectedSessionId) throw new Error("Select a session first");
      if (!queueState?.queue_version) await loadQueue();
      const turns = queueState.turns || [];
      const confirmed = window.confirm(`Clear ${turns.length} queued Turns?`);
      if (!confirmed) {
        setStatus("Queue clear cancelled");
        return;
      }
      const result = await requestJson(
        `/api/v1/sessions/${encodeURIComponent(selectedSessionId)}/queue/clear`,
        {
          method: "POST",
          body: JSON.stringify({
            actor: actor(),
            expected_queue_version: queueState.queue_version,
            confirm_count: turns.length,
            trace_id: "admin-ui-queue-clear",
          }),
        },
      );
      await loadQueue();
      setStatus(`Cleared ${result.count} queued Turns`);
    }

    async function createProject() {
      setStatus("Creating project");
      const project = await requestJson("/api/v1/projects", {
        method: "POST",
        body: JSON.stringify({
          actor: actor(),
          name: $("project-name").value.trim(),
          slug: optional($("project-slug").value),
          aliases: csv($("project-aliases").value),
          description: optional($("project-description").value),
          default_agent: $("project-agent").value,
          max_active_sessions: readProjectMaxActiveSessions(),
          max_running_turns: readProjectMaxRunningTurns(),
          max_queued_turns: readProjectMaxQueuedTurns(),
          daily_turns_per_user: readProjectDailyTurnsPerUser(),
          trace_id: "admin-ui-project-create",
        }),
      });
      selectedProjectId = project.id;
      await loadProjects();
      setStatus(`Created ${project.slug}`);
    }

    async function addWorkspace() {
      if (!selectedProjectId) throw new Error("Select a project first");
      setStatus("Adding workspace");
      const encoded = encodeURIComponent(selectedProjectId);
      const workspace = await requestJson(`/api/v1/projects/${encoded}/workspaces`, {
        method: "POST",
        body: JSON.stringify({
          actor: actor(),
          machine_id: $("workspace-machine").value.trim() || "local",
          path: $("workspace-path").value.trim(),
          allowed_root: $("workspace-root").value.trim(),
          workspace_type: $("workspace-type").value,
          is_writable: $("workspace-writable").checked,
          max_write_sessions: readWorkspaceMaxWriteSessions(),
          trace_id: "admin-ui-workspace-add",
        }),
      });
      await refreshSelectedProject();
      setStatus(`Added workspace ${workspace.id}`);
    }

    async function createSession() {
      if (!selectedProjectId) throw new Error("Select a project first");
      const workspaceId = $("session-workspace-id").value;
      if (!workspaceId) throw new Error("Add a workspace first");
      setStatus("Creating session");
      const session = await requestJson("/api/v1/sessions", {
        method: "POST",
        body: JSON.stringify({
          actor: actor(),
          project_id: selectedProjectId,
          workspace_id: workspaceId,
          name: $("session-name").value.trim() || "AgentBridge Session",
          agent_type: $("session-agent").value,
          visibility: $("session-visibility").value,
          trace_id: "admin-ui-session-create",
        }),
      });
      selectedSessionId = session.id;
      $("session-json").textContent = JSON.stringify(session, null, 2);
      await refreshSelectedProject();
      await loadQueue();
      setStatus(`Created session ${session.short_code}`);
    }

    async function closeSession() {
      if (!selectedSessionId) throw new Error("Select a session first");
      setStatus("Closing session");
      const session = await requestJson(
        `/api/v1/sessions/${encodeURIComponent(selectedSessionId)}/close`,
        {
          method: "POST",
          body: JSON.stringify(actor()),
        },
      );
      $("session-json").textContent = JSON.stringify(session, null, 2);
      await refreshSelectedProject();
      await loadQueue();
      setStatus(`Closed session ${session.short_code}`);
    }

    async function run(action) {
      try {
        $("error").textContent = "";
        await action();
      } catch (error) {
        $("error").textContent = error.message;
        setStatus(error.message);
      }
    }

    $("refresh").addEventListener("click", () => run(loadProjects));
    $("project-session-export-json").addEventListener("click", downloadProjectSessionJson);
    $("binding-refresh").addEventListener("click", () => run(loadProjectBindings));
    $("create-project").addEventListener("click", () => run(createProject));
    $("add-workspace").addEventListener("click", () => run(addWorkspace));
    $("workspace-type").addEventListener("change", syncWorkspaceWritePolicy);
    $("create-session").addEventListener("click", () => run(createSession));
    $("close-session").addEventListener("click", () => run(closeSession));
    $("queue-refresh").addEventListener("click", () => run(loadQueue));
    $("queue-pause").addEventListener("click", () => run(() => setQueuePaused(true)));
    $("queue-resume").addEventListener("click", () => run(() => setQueuePaused(false)));
    $("queue-clear").addEventListener("click", () => run(clearQueue));
    syncWorkspaceWritePolicy();
    loadProjects().catch((error) => {
      $("error").textContent = error.message;
      setStatus(error.message);
    });
  </script>
</body>
</html>
"""


INTERACTION_ADMIN_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentBridge Interactions</title>
  <style>
    :root {
      --bg: #f7f8fa;
      --panel: #ffffff;
      --panel-muted: #f0f3f7;
      --line: #d6dde6;
      --text: #17202a;
      --muted: #5b6878;
      --accent: #0f766e;
      --danger: #b42318;
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
      min-height: 56px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 10px 20px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 650;
      letter-spacing: 0;
    }
    nav {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    nav a {
      color: var(--muted);
      text-decoration: none;
      font-weight: 650;
    }
    nav a:hover { color: var(--accent); }
    main {
      display: grid;
      grid-template-columns: minmax(500px, 1.05fr) minmax(440px, .95fr);
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
    button, input, select, textarea {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      font: inherit;
    }
    button {
      min-height: 34px;
      padding: 6px 10px;
      cursor: pointer;
      font-weight: 650;
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
    input, select {
      min-height: 34px;
      width: 100%;
      padding: 6px 8px;
    }
    textarea {
      min-height: 68px;
      width: 100%;
      padding: 8px;
      resize: vertical;
    }
    label {
      display: grid;
      gap: 5px;
      min-width: 0;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .compact { max-width: 180px; }
    .wide { grid-column: 1 / -1; }
    .status {
      flex: 1;
      min-width: 180px;
      color: var(--muted);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .field-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      padding: 14px;
      border-bottom: 1px solid var(--line);
    }
    .table-wrap {
      max-height: calc(100vh - 112px);
      overflow: auto;
      border-bottom: 1px solid var(--line);
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
      z-index: 1;
      color: var(--muted);
      background: #f9fafb;
      font-size: 12px;
      font-weight: 700;
    }
    tr { cursor: pointer; }
    tr.selected td { background: #e8f5f3; }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 0 8px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #fff;
      font-size: 12px;
      font-weight: 700;
    }
    .pending { color: #915930; }
    .resolved { color: var(--accent); }
    .cancelled, .expired { color: var(--danger); }
    pre {
      margin: 0 14px 14px;
      padding: 10px;
      min-height: 148px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fbfcfe;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      overflow: auto;
    }
    .danger-text { color: var(--danger); }
    @media (max-width: 980px) {
      main { grid-template-columns: 1fr; }
      section { border-right: 0; border-bottom: 1px solid var(--line); }
      .field-grid { grid-template-columns: 1fr; }
      header { align-items: flex-start; flex-direction: column; }
      .compact { max-width: none; }
    }
  </style>
</head>
<body>
  <header>
    <h1>AgentBridge Interactions</h1>
    <nav>
      <a href="/admin">Admin</a>
      <a href="/admin/system">System Health</a>
      <a href="/admin/projects">Projects & Sessions</a>
      <a href="/admin/access-policy">Access Policy</a>
      <a href="/admin/audit">Audit & Events</a>
      <a href="/admin/terminal-lifecycle">Terminal Lifecycle</a>
      <a href="/admin/device-identities">Device Identities</a>
      <a href="/admin/bot-delivery">Bot Delivery</a>
    </nav>
  </header>
  <main>
    <section>
      <div class="toolbar">
        <button id="refresh" type="button">Refresh</button>
        <button id="interaction-export-json" type="button">Export JSON</button>
        <label class="compact">
          Session ID
          <input id="filter-session-id" autocomplete="off" placeholder="optional">
        </label>
        <label class="compact">
          Status
          <select id="filter-status">
            <option value="">All</option>
            <option value="pending">pending</option>
            <option value="partially_approved">partially_approved</option>
            <option value="resolved">resolved</option>
            <option value="expired">expired</option>
            <option value="cancelled">cancelled</option>
          </select>
        </label>
        <div class="status" id="status">Ready</div>
      </div>
      <div class="table-wrap">
        <table aria-label="Interactions">
          <thead>
            <tr>
              <th>Status</th>
              <th>Type</th>
              <th>Risk</th>
              <th>Session</th>
              <th>Votes</th>
              <th>Expires</th>
            </tr>
          </thead>
          <tbody id="interactions"></tbody>
        </table>
      </div>
      <pre id="selected">{}</pre>
      <pre id="error" class="danger-text"></pre>
    </section>

    <section>
      <div class="toolbar">
        <label class="compact">
          Actor ID
          <input id="actor-id" value="admin-ui">
        </label>
        <label class="compact">
          Actor Roles
          <input id="actor-roles" value="admin">
        </label>
        <label class="compact">
          Chat Context
          <input id="chat-context-id" autocomplete="off" placeholder="optional">
        </label>
      </div>

      <div class="field-grid">
        <label>
          New Interaction Session ID
          <input id="create-session-id" autocomplete="off">
        </label>
        <label>
          Type
          <select id="create-type">
            <option value="question">question</option>
            <option value="approval">approval</option>
            <option value="plan">plan</option>
          </select>
        </label>
        <label>
          Risk
          <select id="create-risk">
            <option value="low">low</option>
            <option value="medium">medium</option>
            <option value="high">high</option>
            <option value="critical">critical</option>
          </select>
        </label>
        <label>
          Required Votes
          <input id="create-required-votes" type="number" min="1" placeholder="policy">
        </label>
        <label>
          TTL Seconds
          <input id="create-ttl" type="number" min="1" placeholder="optional">
        </label>
        <label>
          Turn ID
          <input id="create-turn-id" autocomplete="off" placeholder="optional">
        </label>
        <label class="wide">
          Options
          <input id="create-options" autocomplete="off" placeholder="option A, option B">
        </label>
        <label class="wide">
          Prompt
          <textarea id="create-prompt"></textarea>
        </label>
        <button class="primary wide" id="create-interaction" type="button">
          Create Interaction
        </button>
      </div>

      <div class="field-grid">
        <label class="wide">
          Answer
          <textarea id="answer-text"></textarea>
        </label>
        <button class="primary" id="answer" type="button">Answer</button>
        <button class="danger" id="cancel" type="button">Cancel</button>
        <label class="wide">
          Vote / Cancel Reason
          <input id="reason" autocomplete="off">
        </label>
        <button class="primary" id="approve" type="button">Approve</button>
        <button class="danger" id="deny" type="button">Deny</button>
      </div>
    </section>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    let selectedInteractionId = "";
    let currentInteractions = [];
    let selectedInteraction = null;
    let interactionsLoaded = false;

    function csv(value) {
      return value.split(",").map((item) => item.trim()).filter(Boolean);
    }

    function actor() {
      return {
        id: $("actor-id").value.trim() || "admin-ui",
        roles: csv($("actor-roles").value || "admin"),
      };
    }

    function optional(value) {
      const trimmed = value.trim();
      return trimmed ? trimmed : null;
    }

    function optionalNumber(value) {
      const trimmed = value.trim();
      if (!trimmed) return null;
      const parsed = Number.parseInt(trimmed, 10);
      return Number.isFinite(parsed) ? parsed : null;
    }

    function setStatus(text) {
      $("status").textContent = text;
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

    function querySuffix() {
      const params = new URLSearchParams();
      const sessionId = $("filter-session-id").value.trim();
      const status = $("filter-status").value;
      if (sessionId) params.set("session_id", sessionId);
      if (status) params.set("status", status);
      const suffix = params.toString();
      return suffix ? `?${suffix}` : "";
    }

    function appendCell(row, value) {
      const td = document.createElement("td");
      td.textContent = String(value ?? "");
      td.title = String(value ?? "");
      row.appendChild(td);
    }

    function appendStatusCell(row, interaction) {
      const td = document.createElement("td");
      const span = document.createElement("span");
      span.className = `pill ${interaction.status}`;
      span.textContent = interaction.status;
      td.appendChild(span);
      row.appendChild(td);
    }

    function voteSummary(interaction) {
      const votes = interaction.votes || {};
      const approvals = Object.values(votes).filter(Boolean).length;
      return `${approvals}/${interaction.required_votes}`;
    }

    function renderInteractions(interactions) {
      currentInteractions = interactions;
      const rows = interactions.map((interaction) => {
        const tr = document.createElement("tr");
        tr.dataset.interactionId = interaction.id;
        tr.className = interaction.id === selectedInteractionId ? "selected" : "";
        appendStatusCell(tr, interaction);
        for (const value of [
          interaction.type,
          interaction.risk_level,
          interaction.session_id,
          voteSummary(interaction),
          interaction.expires_at || "",
        ]) {
          appendCell(tr, value);
        }
        tr.addEventListener("click", () => selectInteraction(interaction.id));
        return tr;
      });
      $("interactions").replaceChildren(...rows);
      setStatus(`${interactions.length} interactions`);
    }

    async function refreshInteractions() {
      setStatus("Loading");
      const interactions = await requestJson(`/api/v1/interactions${querySuffix()}`);
      interactionsLoaded = true;
      renderInteractions(interactions);
      if (
        selectedInteractionId &&
        interactions.some((interaction) => interaction.id === selectedInteractionId)
      ) {
        await selectInteraction(selectedInteractionId);
      }
    }

    async function selectInteraction(interactionId) {
      selectedInteractionId = interactionId;
      const interaction = await requestJson(
        `/api/v1/interactions/${encodeURIComponent(interactionId)}`,
      );
      selectedInteraction = interaction;
      $("selected").textContent = JSON.stringify(interaction, null, 2);
      $("create-session-id").value = interaction.session_id;
      $("answer-text").value = interaction.answer || "";
      document.querySelectorAll("#interactions tr").forEach((row) => {
        row.classList.toggle("selected", row.dataset.interactionId === interactionId);
      });
      setStatus(`Selected ${interactionId}`);
    }

    function interactionExportPayload() {
      return {
        schema_version: "agentbridge.admin_interaction_export.v1",
        exported_at: new Date().toISOString(),
        filters: {
          session_id: $("filter-session-id").value.trim() || null,
          status: $("filter-status").value || null,
        },
        actor: actor(),
        chat_context_id: optional($("chat-context-id").value),
        interaction_count: currentInteractions.length,
        selected_interaction_id: selectedInteractionId || null,
        interactions: currentInteractions,
        selected_interaction: selectedInteraction,
      };
    }

    function downloadInteractionJson() {
      if (!interactionsLoaded) {
        setStatus("Refresh before export");
        return;
      }
      const payload = interactionExportPayload();
      const blob = new Blob(
        [JSON.stringify(payload, null, 2) + "\n"],
        {type: "application/json"},
      );
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "agentbridge-interactions.json";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
      setStatus("Interaction JSON exported");
    }

    function interactionPayload() {
      const payload = {
        actor: actor(),
        type: $("create-type").value,
        prompt: $("create-prompt").value.trim(),
        turn_id: optional($("create-turn-id").value),
        options: csv($("create-options").value),
        risk_level: $("create-risk").value,
        required_votes: optionalNumber($("create-required-votes").value),
        ttl_seconds: optionalNumber($("create-ttl").value),
        chat_context_id: optional($("chat-context-id").value),
        trace_id: "admin-ui-interaction-create",
      };
      Object.keys(payload).forEach((key) => {
        if (payload[key] === null) delete payload[key];
      });
      return payload;
    }

    async function createInteraction() {
      const sessionId = $("create-session-id").value.trim();
      if (!sessionId) throw new Error("Session ID is required");
      setStatus("Creating interaction");
      const interaction = await requestJson(
        `/api/v1/sessions/${encodeURIComponent(sessionId)}/interactions`,
        {
          method: "POST",
          body: JSON.stringify(interactionPayload()),
        },
      );
      selectedInteractionId = interaction.id;
      selectedInteraction = interaction;
      $("selected").textContent = JSON.stringify(interaction, null, 2);
      await refreshInteractions();
      setStatus(`Created ${interaction.id}`);
    }

    async function answerInteraction() {
      if (!selectedInteractionId) throw new Error("Select an interaction first");
      const interaction = await requestJson(
        `/api/v1/interactions/${encodeURIComponent(selectedInteractionId)}/answer`,
        {
          method: "POST",
          body: JSON.stringify({
            actor: actor(),
            answer: $("answer-text").value.trim(),
            chat_context_id: optional($("chat-context-id").value),
            trace_id: "admin-ui-interaction-answer",
          }),
        },
      );
      selectedInteraction = interaction;
      $("selected").textContent = JSON.stringify(interaction, null, 2);
      await refreshInteractions();
      setStatus(`Answered ${interaction.id}`);
    }

    async function voteInteraction(approve) {
      if (!selectedInteractionId) throw new Error("Select an interaction first");
      const interaction = await requestJson(
        `/api/v1/interactions/${encodeURIComponent(selectedInteractionId)}/vote`,
        {
          method: "POST",
          body: JSON.stringify({
            actor: actor(),
            approve,
            reason: optional($("reason").value),
            chat_context_id: optional($("chat-context-id").value),
            trace_id: approve ? "admin-ui-approval-approve" : "admin-ui-approval-deny",
          }),
        },
      );
      selectedInteraction = interaction;
      $("selected").textContent = JSON.stringify(interaction, null, 2);
      await refreshInteractions();
      setStatus(`${approve ? "Approved" : "Denied"} ${interaction.id}`);
    }

    async function cancelInteraction() {
      if (!selectedInteractionId) throw new Error("Select an interaction first");
      const interaction = await requestJson(
        `/api/v1/interactions/${encodeURIComponent(selectedInteractionId)}/cancel`,
        {
          method: "POST",
          body: JSON.stringify({
            actor: actor(),
            reason: optional($("reason").value),
            chat_context_id: optional($("chat-context-id").value),
            trace_id: "admin-ui-interaction-cancel",
          }),
        },
      );
      selectedInteraction = interaction;
      $("selected").textContent = JSON.stringify(interaction, null, 2);
      await refreshInteractions();
      setStatus(`Cancelled ${interaction.id}`);
    }

    async function run(action) {
      try {
        $("error").textContent = "";
        await action();
      } catch (error) {
        $("error").textContent = error.message;
        setStatus(error.message);
      }
    }

    $("refresh").addEventListener("click", () => run(refreshInteractions));
    $("interaction-export-json").addEventListener("click", downloadInteractionJson);
    $("filter-status").addEventListener("change", () => run(refreshInteractions));
    $("create-interaction").addEventListener("click", () => run(createInteraction));
    $("answer").addEventListener("click", () => run(answerInteraction));
    $("approve").addEventListener("click", () => run(() => voteInteraction(true)));
    $("deny").addEventListener("click", () => run(() => voteInteraction(false)));
    $("cancel").addEventListener("click", () => run(cancelInteraction));
    refreshInteractions().catch((error) => {
      $("error").textContent = error.message;
      setStatus(error.message);
    });
  </script>
</body>
</html>
"""


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
    nav {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    nav a {
      color: var(--muted);
      text-decoration: none;
      font-weight: 650;
    }
    nav a:hover { color: var(--accent); }
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
    <nav>
      <a href="/admin">Admin</a>
      <a href="/admin/system">System Health</a>
      <a href="/admin/projects">Projects & Sessions</a>
      <a href="/admin/interactions">Interactions</a>
      <a href="/admin/audit">Audit & Events</a>
      <a href="/admin/terminal-lifecycle">Terminal Lifecycle</a>
      <a href="/admin/device-identities">Device Identities</a>
      <a href="/admin/bot-delivery">Bot Delivery</a>
    </nav>
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


TERMINAL_LIFECYCLE_ADMIN_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentBridge Terminal Lifecycle</title>
  <style>
    :root {
      --bg: #f7f8fa;
      --panel: #ffffff;
      --panel-muted: #f0f3f7;
      --line: #d6dde6;
      --text: #17202a;
      --muted: #5b6878;
      --accent: #0f766e;
      --danger: #b42318;
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
      min-height: 56px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 10px 20px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 650;
      letter-spacing: 0;
    }
    nav {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    nav a {
      color: var(--muted);
      text-decoration: none;
      font-weight: 650;
    }
    nav a:hover { color: var(--accent); }
    main {
      display: grid;
      grid-template-columns: minmax(360px, .9fr) minmax(460px, 1.1fr);
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
    button, input, textarea {
      min-height: 34px;
      padding: 6px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      font: inherit;
    }
    textarea {
      resize: vertical;
      min-height: 132px;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    button {
      cursor: pointer;
      font-weight: 650;
    }
    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
    }
    label {
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .compact { max-width: 180px; }
    .status {
      flex: 1;
      min-width: 180px;
      color: var(--muted);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      padding: 14px;
    }
    .metric {
      min-height: 78px;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }
    .metric span {
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .metric strong {
      display: block;
      margin-top: 6px;
      font-size: 22px;
      font-weight: 700;
    }
    pre {
      margin: 0 14px 14px;
      padding: 10px;
      min-height: 160px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fbfcfe;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      overflow: auto;
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
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    th {
      color: var(--muted);
      background: #f9fafb;
      font-size: 12px;
      font-weight: 700;
    }
    .table-wrap {
      max-height: calc(100vh - 112px);
      overflow: auto;
    }
    .danger { color: var(--danger); }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; }
      section { border-right: 0; border-bottom: 1px solid var(--line); }
      .metrics { grid-template-columns: 1fr; }
      header { align-items: flex-start; flex-direction: column; }
    }
  </style>
</head>
<body>
  <header>
    <h1>AgentBridge Terminal Lifecycle</h1>
    <nav>
      <a href="/admin">Admin</a>
      <a href="/admin/system">System Health</a>
      <a href="/admin/projects">Projects & Sessions</a>
      <a href="/admin/access-policy">Access Policy</a>
      <a href="/admin/interactions">Interactions</a>
      <a href="/admin/audit">Audit & Events</a>
      <a href="/admin/device-identities">Device Identities</a>
      <a href="/admin/bot-delivery">Bot Delivery</a>
    </nav>
  </header>
  <main>
    <section>
      <div class="toolbar">
        <button id="refresh" type="button">Refresh</button>
        <button class="primary" id="run-once" type="button">Run Once</button>
        <button id="flush-outbox" type="button">Flush Outbox</button>
        <button id="lifecycle-export-json" type="button">Export JSON</button>
        <div class="status" id="status">Ready</div>
      </div>
      <div class="metrics">
        <div class="metric"><span>Running</span><strong id="running">-</strong></div>
        <div class="metric"><span>Tracked</span><strong id="tracked">-</strong></div>
        <div class="metric"><span>Runs</span><strong id="runs">-</strong></div>
        <div class="metric"><span>Last Observed</span><strong id="observed-count">-</strong></div>
        <div class="metric"><span>Reported Exits</span><strong id="exits">-</strong></div>
        <div class="metric"><span>Reported Losses</span><strong id="losses">-</strong></div>
        <div class="metric"><span>Auto Restart</span><strong id="auto-restart">-</strong></div>
        <div class="metric"><span>Restart Attempts</span><strong id="attempts">-</strong></div>
        <div class="metric"><span>Restart Blocks</span><strong id="blocks">-</strong></div>
        <div class="metric"><span>Command Allowlist</span><strong id="allowlist">-</strong></div>
        <div class="metric"><span>Outbox Pending</span><strong id="outbox-pending">-</strong></div>
        <div class="metric"><span>Outbox Flush</span><strong id="outbox-flush">-</strong></div>
        <div class="metric"><span>Outbox Error</span><strong id="outbox-error">-</strong></div>
      </div>
      <pre id="backend">{}</pre>
      <pre id="event-outbox">{}</pre>
      <pre id="error" class="danger"></pre>
    </section>
    <section>
      <div class="toolbar">
        <strong>Agent Launch Profiles</strong>
        <button id="probe-agents" type="button">Probe Versions</button>
        <button id="detect-adapters" type="button">Detect Adapters</button>
      </div>
      <div class="table-wrap">
        <table aria-label="Agent launch profiles">
          <thead>
            <tr>
              <th>Agent</th>
              <th>Command</th>
              <th>Source</th>
              <th>Executable</th>
              <th>Available</th>
            </tr>
          </thead>
          <tbody id="agent-profiles"></tbody>
        </table>
      </div>
      <pre id="agent-probe">{}</pre>
      <div class="table-wrap">
        <table aria-label="Agent adapter verification">
          <thead>
            <tr>
              <th>Agent</th>
              <th>Status</th>
              <th>Schema</th>
              <th>Provider Version</th>
              <th>Verification</th>
              <th>Capabilities</th>
            </tr>
          </thead>
          <tbody id="agent-adapter-summary"></tbody>
        </table>
      </div>
      <pre id="agent-adapters">{}</pre>
    </section>
    <section>
      <div class="toolbar">
        <label class="compact">
          Actor ID
          <input id="actor-id" value="admin-ui">
        </label>
        <label class="compact">
          Actor Roles
          <input id="actor-roles" value="admin">
        </label>
        <label class="compact">
          Trace ID
          <input id="trace-id" value="admin-ui-lifecycle-run-once">
        </label>
      </div>
      <div class="table-wrap">
        <table aria-label="Observed terminal sessions">
          <thead>
            <tr>
              <th>Session</th>
              <th>Started</th>
              <th>Running</th>
              <th>Exit</th>
              <th>Cursor</th>
            </tr>
          </thead>
          <tbody id="observed"></tbody>
        </table>
      </div>
    </section>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    let latestLifecycleStatus = null;
    let latestObserved = {};
    let latestAgentProbeProfiles = {};
    let latestAgentAdapters = {};

    function csv(value) {
      return value.split(",").map((item) => item.trim()).filter(Boolean);
    }

    function actor() {
      return {
        id: $("actor-id").value.trim() || "admin-ui",
        roles: csv($("actor-roles").value || "admin"),
      };
    }

    function setStatus(text) {
      $("status").textContent = text;
    }

    function setText(id, value) {
      $(id).textContent = String(value ?? "-");
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

    function renderStatus(status) {
      latestLifecycleStatus = status;
      setText("running", status.running);
      setText("tracked", status.tracked_sessions);
      setText("runs", status.run_count);
      setText("observed-count", status.last_observed_count);
      setText("exits", status.reported_exit_count);
      setText("losses", status.reported_lost_count);
      setText("auto-restart", status.auto_restart_on_lost);
      setText("attempts", status.auto_restart_attempt_count);
      setText("blocks", status.auto_restart_blocked_count);
      setText("allowlist", (status.auto_restart_command_allowlist || []).join(",") || "-");
      $("backend").textContent = JSON.stringify(status.backend_supervision || {}, null, 2);
      renderEventOutbox(status.event_outbox || {});
      $("error").textContent = status.last_error || status.auto_restart_last_block_reason || "";
      renderAgentProfiles(status.agent_launch_profiles || {});
    }

    function renderEventOutbox(eventOutbox) {
      setText("outbox-pending", eventOutbox.enabled ? eventOutbox.pending_count : "disabled");
      setText("outbox-flush", eventOutbox.last_flush_count ?? 0);
      setText("outbox-error", eventOutbox.last_flush_error || eventOutbox.read_error || "-");
      $("event-outbox").textContent = JSON.stringify(eventOutbox, null, 2);
    }

    function renderAgentProfiles(profiles) {
      const tbody = $("agent-profiles");
      const rows = Object.values(profiles).map((profile) => {
        const tr = document.createElement("tr");
        const executable = profile.executable_path || profile.executable || "";
        const availability = profile.available ? "yes" : (profile.unavailable_reason || "no");
        for (const value of [
          profile.agent_type,
          profile.command,
          profile.source,
          executable,
          availability,
        ]) {
          const td = document.createElement("td");
          td.textContent = String(value ?? "");
          tr.appendChild(td);
        }
        return tr;
      });
      tbody.replaceChildren(...rows);
    }

    function renderAgentAdapters(adapters) {
      const tbody = $("agent-adapter-summary");
      const rows = Object.values(adapters || {}).map((adapter) => {
        const gate = adapter.schema_gate || {};
        const provider = gate.provider_version_verification || {};
        const capabilities = adapter.capabilities || [];
        const providerVersion = provider.provider_version_text || provider.provider_version || "-";
        const verification = provider.status
          ? `${provider.status}${provider.reason ? `:${provider.reason}` : ""}`
          : "-";
        const tr = document.createElement("tr");
        for (const value of [
          adapter.agent_type,
          adapter.status,
          gate.schema_version || adapter.handshake_probe?.schema_version || "-",
          providerVersion,
          verification,
          capabilities.length,
        ]) {
          const td = document.createElement("td");
          td.textContent = String(value ?? "");
          tr.appendChild(td);
        }
        tr.title = adapter.next_step || gate.next_step || "";
        return tr;
      });
      tbody.replaceChildren(...rows);
    }

    function renderObserved(observed) {
      latestObserved = observed || {};
      const tbody = $("observed");
      const rows = Object.entries(observed || {}).map(([sessionId, item]) => {
        const tr = document.createElement("tr");
        for (const value of [
          sessionId,
          item.started,
          item.running,
          item.exit_code ?? "",
          item.output_cursor,
        ]) {
          const td = document.createElement("td");
          td.textContent = String(value);
          tr.appendChild(td);
        }
        return tr;
      });
      tbody.replaceChildren(...rows);
    }

    function terminalLifecycleExportPayload() {
      return {
        schema_version: "agentbridge.admin_terminal_lifecycle_export.v1",
        exported_at: new Date().toISOString(),
        monitor: latestLifecycleStatus || {},
        observed: latestObserved,
        agent_probe_profiles: latestAgentProbeProfiles,
        agent_adapters: latestAgentAdapters,
      };
    }

    function downloadTerminalLifecycleJson() {
      if (!latestLifecycleStatus) {
        setStatus("Refresh before export");
        return;
      }
      const payload = terminalLifecycleExportPayload();
      const blob = new Blob(
        [JSON.stringify(payload, null, 2) + "\n"],
        {type: "application/json"},
      );
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "agentbridge-terminal-lifecycle.json";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
      setStatus("Terminal lifecycle JSON exported");
    }

    async function refresh() {
      setStatus("Loading");
      const status = await requestJson("/api/v1/terminal/lifecycle-monitor");
      renderStatus(status);
      setStatus("Ready");
    }

    async function runOnce() {
      setStatus("Running");
      const payload = {
        actor: actor(),
        trace_id: $("trace-id").value.trim() || "admin-ui-lifecycle-run-once",
      };
      const result = await requestJson("/api/v1/terminal/lifecycle-monitor/run-once", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      renderStatus(result.monitor);
      renderObserved(result.observed);
      setStatus("Run complete");
    }

    async function flushOutbox() {
      setStatus("Flushing outbox");
      const payload = {
        actor: actor(),
        trace_id: $("trace-id").value.trim() || "admin-ui-terminal-event-outbox-flush",
      };
      const result = await requestJson("/api/v1/terminal/event-outbox/flush", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (latestLifecycleStatus) {
        latestLifecycleStatus = {
          ...latestLifecycleStatus,
          event_outbox: result.event_outbox || {},
        };
      }
      renderEventOutbox(result.event_outbox || {});
      setStatus(`Flushed ${result.flushed || 0} events`);
    }

    async function probeAgents() {
      setStatus("Probing");
      const payload = {
        actor: actor(),
        trace_id: "admin-ui-agent-launch-probe",
      };
      const result = await requestJson("/api/v1/terminal/agent-launch/probe", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      latestAgentProbeProfiles = result.profiles || {};
      $("agent-probe").textContent = JSON.stringify(latestAgentProbeProfiles, null, 2);
      setStatus("Probe complete");
    }

    async function detectAdapters() {
      setStatus("Detecting adapters");
      const payload = {
        actor: actor(),
        trace_id: "admin-ui-agent-adapter-detect",
      };
      const result = await requestJson("/api/v1/terminal/agent-adapters/detect", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const adapters = result.adapters || {};
      latestAgentAdapters = adapters;
      renderAgentAdapters(adapters);
      $("agent-adapters").textContent = JSON.stringify(adapters, null, 2);
      setStatus("Adapter detect complete");
    }

    async function run(action) {
      try {
        await action();
      } catch (error) {
        setStatus(error.message);
      }
    }

    $("refresh").addEventListener("click", () => run(refresh));
    $("run-once").addEventListener("click", () => run(runOnce));
    $("flush-outbox").addEventListener("click", () => run(flushOutbox));
    $("probe-agents").addEventListener("click", () => run(probeAgents));
    $("detect-adapters").addEventListener("click", () => run(detectAdapters));
    $("lifecycle-export-json").addEventListener("click", downloadTerminalLifecycleJson);
    refresh().catch((error) => setStatus(error.message));
  </script>
</body>
</html>
"""


DEVICE_IDENTITY_ADMIN_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentBridge Device Identities</title>
  <style>
    :root {
      --bg: #f7f8fa;
      --panel: #ffffff;
      --panel-muted: #f0f3f7;
      --line: #d6dde6;
      --text: #17202a;
      --muted: #5b6878;
      --accent: #0f766e;
      --danger: #b42318;
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
      min-height: 56px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 10px 20px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 650;
      letter-spacing: 0;
    }
    nav {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    nav a {
      color: var(--muted);
      text-decoration: none;
      font-weight: 650;
    }
    nav a:hover { color: var(--accent); }
    main {
      display: grid;
      grid-template-columns: minmax(480px, 1.1fr) minmax(420px, .9fr);
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
    button, input {
      min-height: 34px;
      padding: 6px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      font: inherit;
    }
    button {
      cursor: pointer;
      font-weight: 650;
    }
    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
    }
    button.danger {
      border-color: var(--danger);
      background: var(--danger);
      color: #fff;
    }
    label {
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .compact { max-width: 180px; }
    .status {
      flex: 1;
      min-width: 180px;
      color: var(--muted);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .field-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      padding: 14px;
    }
    .field-grid label.full {
      grid-column: 1 / -1;
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
      z-index: 1;
      color: var(--muted);
      background: #f9fafb;
      font-size: 12px;
      font-weight: 700;
    }
    tr { cursor: pointer; }
    tr.selected td { background: #e8f5f3; }
    .revoked { color: var(--danger); }
    .table-wrap {
      max-height: calc(100vh - 112px);
      overflow: auto;
    }
    pre {
      margin: 0 14px 14px;
      padding: 10px;
      min-height: 118px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fbfcfe;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      overflow: auto;
    }
    .danger-text { color: var(--danger); }
    @media (max-width: 980px) {
      main { grid-template-columns: 1fr; }
      section { border-right: 0; border-bottom: 1px solid var(--line); }
      .field-grid { grid-template-columns: 1fr; }
      .field-grid label.full { grid-column: auto; }
      header { align-items: flex-start; flex-direction: column; }
      .compact { max-width: none; }
    }
  </style>
</head>
<body>
  <header>
    <h1>AgentBridge Device Identities</h1>
    <nav>
      <a href="/admin">Admin</a>
      <a href="/admin/system">System Health</a>
      <a href="/admin/projects">Projects & Sessions</a>
      <a href="/admin/access-policy">Access Policy</a>
      <a href="/admin/interactions">Interactions</a>
      <a href="/admin/audit">Audit & Events</a>
      <a href="/admin/terminal-lifecycle">Terminal Lifecycle</a>
      <a href="/admin/bot-delivery">Bot Delivery</a>
    </nav>
  </header>
  <main>
    <section>
      <div class="toolbar">
        <button id="refresh" type="button">Refresh</button>
        <button id="device-export-json" type="button">Export JSON</button>
        <label class="compact">
          Include Revoked
          <input id="include-revoked" type="checkbox">
        </label>
        <div class="status" id="status">Ready</div>
      </div>
      <div class="table-wrap">
        <table aria-label="Device identities">
          <thead>
            <tr>
              <th>Device ID</th>
              <th>Name</th>
              <th>Status</th>
              <th>Scopes</th>
              <th>Resources</th>
              <th>Certs</th>
              <th>Cert Health</th>
              <th>Created</th>
              <th>Last Used</th>
            </tr>
          </thead>
          <tbody id="devices"></tbody>
        </table>
      </div>
    </section>
    <section>
      <div class="toolbar">
        <button id="new-device" type="button">New</button>
        <button id="save-device" class="primary" type="button">Save / Rotate Key</button>
        <button id="issue-certificate" type="button">Issue Certificate</button>
        <button id="renew-certificate" type="button">Renew Certificate</button>
        <button id="rotate-certificates" type="button">Rotate Certificates</button>
        <button id="scan-certificates" type="button">Scan Certificates</button>
        <button id="revoke-device" class="danger" type="button">Revoke</button>
      </div>
      <div class="field-grid">
        <label>
          Actor ID
          <input id="actor-id" value="admin-ui">
        </label>
        <label>
          Actor Roles
          <input id="actor-roles" value="admin">
        </label>
        <label>
          Auth Device ID
          <input id="auth-device-id" autocomplete="off">
        </label>
        <label>
          Auth Device Key
          <input id="auth-device-key" type="password" autocomplete="off">
        </label>
        <label>
          Device ID
          <input id="device-id" autocomplete="off">
        </label>
        <label>
          Display Name
          <input id="display-name" autocomplete="off">
        </label>
        <label class="full">
          Allowed Scopes
          <input id="allowed-scopes">
        </label>
        <label class="full">
          Allowed Resource IDs
          <input
            id="allowed-resource-ids"
            placeholder="project/session/interaction IDs, empty for all"
          >
        </label>
        <label class="full">
          Certificate Fingerprints
          <input id="certificate-fingerprints" placeholder="sha256 fingerprint allowlist">
        </label>
        <label class="full">
          Certificate Fingerprints To Add
          <input id="certificate-fingerprints-add" placeholder="new fingerprint(s)">
        </label>
        <label class="full">
          Certificate Fingerprints To Remove
          <input id="certificate-fingerprints-remove" placeholder="old fingerprint(s)">
        </label>
        <label>
          Certificate Validity Days
          <input id="certificate-validity-days" type="number" min="1" placeholder="default">
        </label>
        <label class="full">
          Certificate CSR PEM
          <textarea
            id="certificate-csr"
            placeholder="-----BEGIN CERTIFICATE REQUEST-----"
          ></textarea>
        </label>
        <label class="full">
          New Device Key
          <input
            id="device-key"
            type="password"
            autocomplete="new-password"
            placeholder="optional key rotation"
          >
        </label>
      </div>
      <pre id="generated-key">{}</pre>
      <pre id="selected">{}</pre>
      <pre id="error" class="danger-text"></pre>
    </section>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    const defaultScopes = [
      "http_api",
      "audit_read",
      "bot_gateway_read",
      "bot_gateway_manage",
      "onebot_event_ingest",
      "command_parse",
      "command_execute",
      "device_manage",
      "policy_read",
      "policy_manage",
      "group_role_read",
      "group_role_manage",
      "chat_context_manage",
      "project_read",
      "project_manage",
      "session_read",
      "session_manage",
      "session_send",
      "session_event_ingest",
      "interaction_read",
      "interaction_manage",
      "terminal_read",
      "terminal_control",
      "session_events_ws",
      "rendered_events_ws",
      "terminal_ws",
      "bot_gateway_ws",
    ].join(",");
    let selectedDeviceId = "";
    let currentDevices = [];
    let selectedDevice = null;
    let devicesLoaded = false;
    let latestDeviceOperation = null;

    function csv(value) {
      return value.split(",").map((item) => item.trim()).filter(Boolean);
    }

    function actor() {
      return {
        id: $("actor-id").value.trim() || "admin-ui",
        roles: csv($("actor-roles").value || "admin"),
      };
    }

    function authHeaders() {
      const headers = {"content-type": "application/json"};
      const deviceId = $("auth-device-id").value.trim();
      const deviceKey = $("auth-device-key").value.trim();
      if (deviceId && deviceKey) {
        headers["x-agentbridge-device-id"] = deviceId;
        headers["x-agentbridge-device-key"] = deviceKey;
      }
      return headers;
    }

    function setStatus(text) {
      $("status").textContent = text;
    }

    async function requestJson(url, options = {}) {
      const response = await fetch(url, {
        headers: authHeaders(),
        ...options,
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.message || data.detail || response.statusText);
      }
      return data;
    }

    function devicesUrl() {
      const params = new URLSearchParams();
      if ($("include-revoked").checked) params.set("include_revoked", "true");
      const suffix = params.toString();
      return `/api/v1/device-identities${suffix ? `?${suffix}` : ""}`;
    }

    function formatCertificateHealth(device) {
      const health = device.certificate_health || {};
      const status = health.status || "unknown";
      const parts = [status];
      if (health.expired_count) parts.push(`expired:${health.expired_count}`);
      if (health.expiring_count) parts.push(`expiring:${health.expiring_count}`);
      if (health.renewal_status) parts.push(`renewal:${health.renewal_status}`);
      if (health.renewal_due_count) parts.push(`renewal_due:${health.renewal_due_count}`);
      if (health.renewal_overdue_count) {
        parts.push(`renewal_overdue:${health.renewal_overdue_count}`);
      }
      if (health.untracked_certificate_count) {
        parts.push(`untracked:${health.untracked_certificate_count}`);
      }
      if (health.renewal_due_at) parts.push(`renew_by:${health.renewal_due_at}`);
      if (health.next_expires_at) parts.push(health.next_expires_at);
      return parts.join(" ");
    }

    function renderDevices(devices) {
      currentDevices = devices;
      selectedDevice = devices.find((device) => device.device_id === selectedDeviceId) || null;
      const rows = devices.map((device) => {
        const tr = document.createElement("tr");
        tr.dataset.deviceId = device.device_id;
        tr.className = [
          device.device_id === selectedDeviceId ? "selected" : "",
          device.status === "revoked" ? "revoked" : "",
        ].filter(Boolean).join(" ");
        for (const value of [
          device.device_id,
          device.display_name || "",
          device.status,
          (device.allowed_scopes || []).join(","),
          (device.allowed_resource_ids || []).join(",") || "all",
          (device.certificate_fingerprints || []).length,
          formatCertificateHealth(device),
          device.created_at || "",
          device.last_used_at || "",
        ]) {
          const td = document.createElement("td");
          td.textContent = String(value);
          td.title = String(value);
          tr.appendChild(td);
        }
        tr.addEventListener("click", () => selectDevice(device));
        return tr;
      });
      $("devices").replaceChildren(...rows);
      setStatus(`${devices.length} device identities`);
    }

    function selectDevice(device) {
      selectedDeviceId = device.device_id;
      selectedDevice = device;
      $("device-id").value = device.device_id;
      $("display-name").value = device.display_name || "";
      $("allowed-scopes").value = (device.allowed_scopes || []).join(",");
      $("allowed-resource-ids").value = (
        device.allowed_resource_ids || []
      ).join(",");
      $("certificate-fingerprints").value = (
        device.certificate_fingerprints || []
      ).join(",");
      $("certificate-fingerprints-add").value = "";
      $("certificate-fingerprints-remove").value = "";
      $("certificate-validity-days").value = "";
      $("certificate-csr").value = "";
      $("device-key").value = "";
      $("selected").textContent = JSON.stringify(device, null, 2);
      document.querySelectorAll("#devices tr").forEach((row) => {
        row.classList.toggle("selected", row.dataset.deviceId === selectedDeviceId);
      });
    }

    function newDevice() {
      selectedDeviceId = "";
      selectedDevice = null;
      $("device-id").value = "";
      $("display-name").value = "";
      $("allowed-scopes").value = defaultScopes;
      $("allowed-resource-ids").value = "";
      $("certificate-fingerprints").value = "";
      $("certificate-fingerprints-add").value = "";
      $("certificate-fingerprints-remove").value = "";
      $("certificate-validity-days").value = "";
      $("certificate-csr").value = "";
      $("device-key").value = "";
      $("selected").textContent = "{}";
      $("generated-key").textContent = "{}";
      document.querySelectorAll("#devices tr").forEach((row) => row.classList.remove("selected"));
      setStatus("New device identity");
    }

    async function loadDevices() {
      setStatus("Loading");
      const devices = await requestJson(devicesUrl());
      devicesLoaded = true;
      renderDevices(devices);
    }

    function redactSensitive(value) {
      if (Array.isArray(value)) {
        return value.map((item) => redactSensitive(item));
      }
      if (value && typeof value === "object") {
        return Object.fromEntries(Object.entries(value).map(([key, item]) => {
          const lowered = key.toLowerCase();
          if (
            lowered.includes("device_key") ||
            lowered.includes("private_key") ||
            lowered.includes("token") ||
            lowered.includes("csr_pem") ||
            lowered.includes("certificate_pem")
          ) {
            return [key, item ? "[redacted]" : item];
          }
          return [key, redactSensitive(item)];
        }));
      }
      return value;
    }

    function recordDeviceOperation(type, result) {
      latestDeviceOperation = {
        type,
        recorded_at: new Date().toISOString(),
        result: redactSensitive(result),
      };
    }

    function deviceIdentityExportPayload() {
      return {
        schema_version: "agentbridge.admin_device_identity_export.v1",
        exported_at: new Date().toISOString(),
        include_revoked: $("include-revoked").checked,
        actor: actor(),
        auth_device: {
          device_id: $("auth-device-id").value.trim() || null,
          device_key_provided: Boolean($("auth-device-key").value.trim()),
        },
        device_count: currentDevices.length,
        selected_device_id: selectedDeviceId || null,
        devices: currentDevices,
        selected_device: selectedDevice,
        latest_operation: latestDeviceOperation,
      };
    }

    function downloadDeviceIdentityJson() {
      if (!devicesLoaded) {
        setStatus("Refresh before export");
        return;
      }
      const payload = deviceIdentityExportPayload();
      const blob = new Blob(
        [JSON.stringify(payload, null, 2) + "\n"],
        {type: "application/json"},
      );
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "agentbridge-device-identities.json";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
      setStatus("Device identity JSON exported");
    }

    async function upsertDevice() {
      const deviceId = $("device-id").value.trim();
      if (!deviceId) throw new Error("Device ID is required");
      const payload = {
        actor: actor(),
        device_id: deviceId,
        display_name: $("display-name").value.trim() || null,
        device_key: $("device-key").value.trim() || null,
        allowed_scopes: csv($("allowed-scopes").value),
        allowed_resource_ids: csv($("allowed-resource-ids").value),
        certificate_fingerprints: csv($("certificate-fingerprints").value),
        trace_id: "admin-ui-device-upsert",
      };
      const saved = await requestJson("/api/v1/device-identities", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (saved.device_key) {
        $("auth-device-id").value = saved.device_id;
        $("auth-device-key").value = saved.device_key;
        $("generated-key").textContent = JSON.stringify({
          device_id: saved.device_id,
          device_key: saved.device_key,
        }, null, 2);
      }
      recordDeviceOperation("save_device", saved);
      $("device-key").value = "";
      selectedDeviceId = saved.device_id;
      await loadDevices();
      setStatus(`Saved ${saved.device_id}`);
    }

    async function issueDeviceCertificate() {
      const deviceId = $("device-id").value.trim();
      const csrPem = $("certificate-csr").value.trim();
      if (!deviceId) throw new Error("Device ID is required");
      if (!csrPem) throw new Error("Certificate CSR PEM is required");
      const validityDays = Number.parseInt($("certificate-validity-days").value || "", 10);
      const payload = {
        actor: actor(),
        csr_pem: csrPem,
        validity_days: Number.isFinite(validityDays) ? validityDays : null,
        trace_id: "admin-ui-device-cert-issue",
      };
      const issued = await requestJson(
        `/api/v1/device-identities/${encodeURIComponent(deviceId)}` +
          "/certificates/issue",
        {
          method: "POST",
          body: JSON.stringify(payload),
        },
      );
      $("certificate-fingerprints").value = (
        issued.device_identity.certificate_fingerprints || []
      ).join(",");
      $("certificate-csr").value = "";
      $("generated-key").textContent = JSON.stringify({
        device_id: issued.device_identity.device_id,
        certificate_fingerprint: issued.certificate_fingerprint,
        certificate_pem: issued.certificate_pem,
        ca_certificate_pem: issued.ca_certificate_pem,
        not_after: issued.not_after,
      }, null, 2);
      recordDeviceOperation("issue_certificate", issued);
      selectedDeviceId = issued.device_identity.device_id;
      await loadDevices();
      setStatus(`Issued certificate for ${issued.device_identity.device_id}`);
    }

    async function renewDeviceCertificate() {
      const deviceId = $("device-id").value.trim();
      const csrPem = $("certificate-csr").value.trim();
      if (!deviceId) throw new Error("Device ID is required");
      if (!csrPem) throw new Error("Certificate CSR PEM is required");
      const validityDays = Number.parseInt($("certificate-validity-days").value || "", 10);
      const payload = {
        actor: actor(),
        csr_pem: csrPem,
        validity_days: Number.isFinite(validityDays) ? validityDays : null,
        trace_id: "admin-ui-device-cert-renew",
      };
      const renewed = await requestJson(
        `/api/v1/device-identities/${encodeURIComponent(deviceId)}` +
          "/certificates/renew",
        {
          method: "POST",
          body: JSON.stringify(payload),
        },
      );
      $("certificate-fingerprints").value = (
        renewed.device_identity.certificate_fingerprints || []
      ).join(",");
      $("certificate-csr").value = "";
      $("generated-key").textContent = JSON.stringify({
        device_id: renewed.device_identity.device_id,
        certificate_fingerprint: renewed.certificate_fingerprint,
        replaced_certificate_fingerprints: renewed.replaced_certificate_fingerprints,
        certificate_pem: renewed.certificate_pem,
        ca_certificate_pem: renewed.ca_certificate_pem,
        not_after: renewed.not_after,
      }, null, 2);
      recordDeviceOperation("renew_certificate", renewed);
      selectedDeviceId = renewed.device_identity.device_id;
      await loadDevices();
      setStatus(`Renewed certificate for ${renewed.device_identity.device_id}`);
    }

    async function rotateDeviceCertificates() {
      const deviceId = $("device-id").value.trim();
      if (!deviceId) throw new Error("Device ID is required");
      const payload = {
        actor: actor(),
        add_fingerprints: csv($("certificate-fingerprints-add").value),
        remove_fingerprints: csv($("certificate-fingerprints-remove").value),
        trace_id: "admin-ui-device-cert-rotate",
      };
      const rotated = await requestJson(
        `/api/v1/device-identities/${encodeURIComponent(deviceId)}` +
          "/certificate-fingerprints/rotate",
        {
          method: "POST",
          body: JSON.stringify(payload),
        },
      );
      $("certificate-fingerprints").value = (
        rotated.certificate_fingerprints || []
      ).join(",");
      $("certificate-fingerprints-add").value = "";
      $("certificate-fingerprints-remove").value = "";
      recordDeviceOperation("rotate_certificates", rotated);
      selectedDeviceId = rotated.device_id;
      await loadDevices();
      setStatus(`Rotated certificates for ${rotated.device_id}`);
    }

    async function scanCertificates() {
      const result = await requestJson("/api/v1/device-identities/certificates/scan", {
        method: "POST",
        body: JSON.stringify({
          actor: actor(),
          include_revoked: $("include-revoked").checked,
          trace_id: "admin-ui-device-cert-scan",
        }),
      });
      recordDeviceOperation("scan_certificates", result);
      $("generated-key").textContent = JSON.stringify(result, null, 2);
      await loadDevices();
      setStatus(
        `Scanned ${result.total_device_count} devices; ` +
          `${result.action_required_count} need attention; ` +
          `${result.renewal_action_required_count || 0} need renewal`,
      );
    }

    async function revokeDevice() {
      const deviceId = $("device-id").value.trim();
      if (!deviceId) throw new Error("Device ID is required");
      const revoked = await requestJson(
        `/api/v1/device-identities/${encodeURIComponent(deviceId)}/revoke`,
        {
          method: "POST",
          body: JSON.stringify({actor: actor(), trace_id: "admin-ui-device-revoke"}),
        },
      );
      recordDeviceOperation("revoke_device", revoked);
      selectedDeviceId = revoked.device_id;
      $("include-revoked").checked = true;
      await loadDevices();
      setStatus(`Revoked ${revoked.device_id}`);
    }

    async function run(action) {
      try {
        $("error").textContent = "";
        await action();
      } catch (error) {
        $("error").textContent = error.message;
        setStatus(error.message);
      }
    }

    $("refresh").addEventListener("click", () => run(loadDevices));
    $("device-export-json").addEventListener("click", downloadDeviceIdentityJson);
    $("include-revoked").addEventListener("change", () => run(loadDevices));
    $("new-device").addEventListener("click", newDevice);
    $("save-device").addEventListener("click", () => run(upsertDevice));
    $("issue-certificate").addEventListener("click", () => run(issueDeviceCertificate));
    $("renew-certificate").addEventListener("click", () => run(renewDeviceCertificate));
    $("rotate-certificates").addEventListener(
      "click",
      () => run(rotateDeviceCertificates),
    );
    $("scan-certificates").addEventListener("click", () => run(scanCertificates));
    $("revoke-device").addEventListener("click", () => run(revokeDevice));
    $("allowed-scopes").value = defaultScopes;
    loadDevices().catch((error) => {
      $("error").textContent = error.message;
      setStatus(error.message);
    });
  </script>
</body>
</html>
"""


BOT_DELIVERY_ADMIN_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AgentBridge Bot Delivery</title>
  <style>
    :root {
      --bg: #f7f8fa;
      --panel: #ffffff;
      --panel-muted: #f0f3f7;
      --line: #d6dde6;
      --text: #17202a;
      --muted: #5b6878;
      --accent: #0f766e;
      --danger: #b42318;
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
      min-height: 56px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 10px 20px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 650;
      letter-spacing: 0;
    }
    nav {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    nav a {
      color: var(--muted);
      text-decoration: none;
      font-weight: 650;
    }
    nav a:hover { color: var(--accent); }
    main {
      display: grid;
      grid-template-columns: minmax(480px, 1.1fr) minmax(380px, .9fr);
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
    button, input, select {
      min-height: 34px;
      padding: 6px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      font: inherit;
    }
    button {
      cursor: pointer;
      font-weight: 650;
    }
    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
    }
    label {
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .compact { max-width: 180px; }
    .status {
      flex: 1;
      min-width: 180px;
      color: var(--muted);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
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
      color: var(--muted);
      background: #f9fafb;
      font-size: 12px;
      font-weight: 700;
      position: sticky;
      top: 0;
      z-index: 1;
    }
    tr {
      cursor: pointer;
    }
    tr.selected td {
      background: #e8f5f3;
    }
    .table-wrap {
      max-height: calc(100vh - 112px);
      overflow: auto;
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      padding: 14px;
    }
    .metric {
      min-height: 76px;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }
    .metric span {
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .metric strong {
      display: block;
      margin-top: 6px;
      font-size: 22px;
      font-weight: 700;
    }
    pre {
      margin: 0 14px 14px;
      padding: 10px;
      min-height: 154px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fbfcfe;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      overflow: auto;
    }
    textarea {
      width: 100%;
      min-height: 88px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px;
      font: 13px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .edit-field {
      flex: 1 1 260px;
      max-width: none;
    }
    .danger { color: var(--danger); }
    @media (max-width: 980px) {
      main { grid-template-columns: 1fr; }
      section { border-right: 0; border-bottom: 1px solid var(--line); }
      .metrics { grid-template-columns: 1fr; }
      header { align-items: flex-start; flex-direction: column; }
    }
  </style>
</head>
<body>
  <header>
    <h1>AgentBridge Bot Delivery</h1>
    <nav>
      <a href="/admin">Admin</a>
      <a href="/admin/system">System Health</a>
      <a href="/admin/projects">Projects & Sessions</a>
      <a href="/admin/access-policy">Access Policy</a>
      <a href="/admin/interactions">Interactions</a>
      <a href="/admin/audit">Audit & Events</a>
      <a href="/admin/terminal-lifecycle">Terminal Lifecycle</a>
      <a href="/admin/device-identities">Device Identities</a>
    </nav>
  </header>
  <main>
    <section>
      <div class="toolbar">
        <button id="refresh" type="button">Refresh</button>
        <button class="primary" id="retry-due" type="button">Retry Due</button>
        <label class="compact">
          Chat Context
          <input id="chat-context-id" placeholder="optional">
        </label>
        <label class="compact">
          Status
          <select id="status-filter">
            <option value="">All</option>
            <option value="sent">sent</option>
            <option value="failed">failed</option>
            <option value="retrying">retrying</option>
            <option value="skipped_duplicate">skipped_duplicate</option>
          </select>
        </label>
        <div class="status" id="status">Ready</div>
      </div>
      <div class="table-wrap">
        <table aria-label="Bot delivery records">
          <thead>
            <tr>
              <th>Status</th>
              <th>Platform</th>
              <th>Chat</th>
              <th>Event</th>
              <th>Attempts</th>
              <th>Next Retry</th>
            </tr>
          </thead>
          <tbody id="records"></tbody>
        </table>
      </div>
    </section>
    <section>
      <div class="toolbar">
        <button id="worker-refresh" type="button">Worker Status</button>
        <button class="primary" id="worker-run" type="button">Run Worker</button>
        <button id="command-registration-refresh" type="button">Command Results</button>
        <label class="compact">
          Limit
          <input id="limit" type="number" value="100">
        </label>
      </div>
      <div class="metrics">
        <div class="metric"><span>Worker Enabled</span><strong id="worker-enabled">-</strong></div>
        <div class="metric"><span>Worker Running</span><strong id="worker-running">-</strong></div>
        <div class="metric"><span>Run Count</span><strong id="worker-runs">-</strong></div>
        <div class="metric"><span>Last Records</span><strong id="worker-records">-</strong></div>
        <div class="metric">
          <span>Capability Platforms</span><strong id="cap-platforms">-</strong>
        </div>
        <div class="metric">
          <span>OneBot Delete</span><strong id="cap-onebot-delete">-</strong>
        </div>
        <div class="metric">
          <span>OneBot Edit</span><strong id="cap-onebot-edit">-</strong>
        </div>
        <div class="metric">
          <span>Command Results</span><strong id="command-registration-count">-</strong>
        </div>
        <div class="metric">
          <span>Command Failures</span><strong id="command-registration-failures">-</strong>
        </div>
        <div class="metric">
          <span>Last Command Status</span><strong id="command-registration-status">-</strong>
        </div>
      </div>
      <div class="toolbar">
        <button class="primary" id="edit-selected" type="button">Edit Selected</button>
        <button id="delete-selected" type="button">Delete Selected</button>
        <label class="edit-field">
          Message Text
          <textarea id="edit-text" placeholder="updated delivery text"></textarea>
        </label>
      </div>
      <pre id="worker">{}</pre>
      <pre id="capabilities">{}</pre>
      <pre id="command-registration-results">[]</pre>
      <pre id="rate-limits">{}</pre>
      <pre id="selected">{}</pre>
    </section>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    let selectedKey = "";

    function setStatus(text) {
      $("status").textContent = text;
    }

    function setText(id, value) {
      $(id).textContent = String(value ?? "-");
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

    function deliveryQuery() {
      const params = new URLSearchParams();
      const chat = $("chat-context-id").value.trim();
      const status = $("status-filter").value;
      if (chat) params.set("chat_context_id", chat);
      if (status) params.set("status", status);
      const suffix = params.toString();
      return suffix ? `?${suffix}` : "";
    }

    function renderRecords(records) {
      const rows = records.map((record) => {
        const tr = document.createElement("tr");
        tr.dataset.key = record.idempotency_key;
        tr.className = record.idempotency_key === selectedKey ? "selected" : "";
        for (const value of [
          record.status,
          record.platform,
          record.chat_context_id,
          `${record.event_seq}.${record.message_index}`,
          record.attempt_count,
          record.next_retry_at || "",
        ]) {
          const td = document.createElement("td");
          td.textContent = String(value);
          td.title = String(value);
          tr.appendChild(td);
        }
        tr.addEventListener("click", () => {
          selectedKey = record.idempotency_key;
          $("edit-text").value = record.text || "";
          $("selected").textContent = JSON.stringify(record, null, 2);
          document.querySelectorAll("#records tr").forEach((row) => {
            row.classList.toggle("selected", row.dataset.key === selectedKey);
          });
        });
        return tr;
      });
      $("records").replaceChildren(...rows);
      setStatus(`${records.length} records`);
    }

    function renderWorker(worker) {
      setText("worker-enabled", worker.enabled);
      setText("worker-running", worker.running);
      setText("worker-runs", worker.run_count);
      setText("worker-records", worker.last_record_count);
      $("worker").textContent = JSON.stringify(worker, null, 2);
    }

    function renderCapabilities(capabilities) {
      const platforms = capabilities.capabilities || [];
      const onebot = platforms.find((item) => item.platform === "onebot.v11") || {};
      setText("cap-platforms", platforms.length);
      setText("cap-onebot-delete", onebot.deleteMessage);
      setText("cap-onebot-edit", onebot.editMessage);
      $("capabilities").textContent = JSON.stringify(capabilities, null, 2);
    }

    function renderCommandRegistrations(events) {
      const results = events || [];
      const failed = results.filter((event) => {
        return (event.payload || {}).status === "failed";
      });
      const latestPayload = (results[0] || {}).payload || {};
      setText("command-registration-count", results.length);
      setText("command-registration-failures", failed.length);
      setText("command-registration-status", latestPayload.status);
      $("command-registration-results").textContent = JSON.stringify(results, null, 2);
    }

    async function refreshRecords() {
      setStatus("Loading");
      const records = await requestJson(`/api/v1/bot-gateway/deliveries${deliveryQuery()}`);
      renderRecords(records);
    }

    async function refreshWorker() {
      const worker = await requestJson("/api/v1/bot-gateway/retry-worker");
      renderWorker(worker);
      const capabilities = await requestJson("/api/v1/bot-gateway/capabilities");
      renderCapabilities(capabilities);
      await refreshCommandRegistrations();
      const limits = await requestJson("/api/v1/bot-gateway/rate-limits");
      $("rate-limits").textContent = JSON.stringify(limits, null, 2);
    }

    async function refreshCommandRegistrations() {
      const events = await requestJson(
        "/api/v1/events?event_type=bot.command_registration.result&limit=20",
      );
      renderCommandRegistrations(events);
    }

    async function retryDue() {
      setStatus("Retrying due failures");
      const payload = {
        chat_context_id: $("chat-context-id").value.trim() || null,
        limit: Number.parseInt($("limit").value || "100", 10),
      };
      const records = await requestJson("/api/v1/bot-gateway/retry-failed-deliveries", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setStatus(`Retried ${records.length} records`);
      await refreshRecords();
    }

    async function runWorker() {
      const payload = {
        chat_context_id: $("chat-context-id").value.trim() || null,
        limit: Number.parseInt($("limit").value || "100", 10),
      };
      const result = await requestJson("/api/v1/bot-gateway/retry-worker/run-once", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      renderWorker(result.worker);
      setStatus(`Worker retried ${result.records.length} records`);
      await refreshRecords();
    }

    async function editSelected() {
      if (!selectedKey) throw new Error("Select a delivery record");
      const updated = await requestJson("/api/v1/bot-gateway/deliveries/edit", {
        method: "POST",
        body: JSON.stringify({
          idempotency_key: selectedKey,
          text: $("edit-text").value,
          payload: {source: "admin"},
        }),
      });
      $("selected").textContent = JSON.stringify(updated, null, 2);
      setStatus(`Edited ${selectedKey}`);
      await refreshRecords();
    }

    async function deleteSelected() {
      if (!selectedKey) throw new Error("Select a delivery record");
      const deleted = await requestJson("/api/v1/bot-gateway/deliveries/delete", {
        method: "POST",
        body: JSON.stringify({
          idempotency_key: selectedKey,
          payload: {source: "admin"},
        }),
      });
      $("selected").textContent = JSON.stringify(deleted, null, 2);
      setStatus(`Deleted ${selectedKey}`);
      await refreshRecords();
    }

    async function run(action) {
      try {
        await action();
      } catch (error) {
        setStatus(error.message);
      }
    }

    $("refresh").addEventListener("click", () => run(refreshRecords));
    $("status-filter").addEventListener("change", () => run(refreshRecords));
    $("retry-due").addEventListener("click", () => run(retryDue));
    $("worker-refresh").addEventListener("click", () => run(refreshWorker));
    $("worker-run").addEventListener("click", () => run(runWorker));
    $("command-registration-refresh").addEventListener(
      "click",
      () => run(refreshCommandRegistrations),
    );
    $("edit-selected").addEventListener("click", () => run(editSelected));
    $("delete-selected").addEventListener("click", () => run(deleteSelected));
    Promise.all([refreshRecords(), refreshWorker()]).catch((error) => {
      setStatus(error.message);
    });
  </script>
</body>
</html>
"""
