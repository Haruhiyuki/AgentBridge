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
      const records = await requestJson(`/api/v1/audit${paramsFrom([
        ["action", "audit-action"],
        ["actor_id", "audit-actor"],
        ["session_id", "audit-session"],
        ["q", "audit-query"],
        ["limit", "audit-limit"],
      ])}`);
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
        <label class="compact">
          Actor ID
          <input id="actor-id" value="admin-ui">
        </label>
        <label class="compact">
          Actor Roles
          <input id="actor-roles" value="admin">
        </label>
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
        <table aria-label="Sessions">
          <thead>
            <tr>
              <th>Name</th>
              <th>Code</th>
              <th>Status</th>
              <th>Agent</th>
              <th>Workspace</th>
            </tr>
          </thead>
          <tbody id="sessions"></tbody>
        </table>
      </div>
      <pre id="session-json">{}</pre>
      <pre id="error" class="danger"></pre>
    </section>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    let projects = [];
    let selectedProjectId = "";
    let selectedSessionId = "";

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

    function renderProjects() {
      const rows = projects.map((project) => {
        const tr = document.createElement("tr");
        tr.dataset.projectId = project.id;
        tr.className = project.id === selectedProjectId ? "selected" : "";
        for (const value of [project.name, project.slug, project.status, project.default_agent]) {
          appendCell(tr, value);
        }
        tr.addEventListener("click", () => selectProject(project.id));
        return tr;
      });
      $("projects").replaceChildren(...rows);
    }

    function renderWorkspaces(workspaces) {
      const rows = workspaces.map((workspace) => {
        const tr = document.createElement("tr");
        for (const value of [
          workspace.path,
          workspace.allowed_root,
          workspace.machine_id,
          workspace.type,
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

    function renderSessions(sessions) {
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
      }
    }

    async function loadProjects() {
      setStatus("Loading projects");
      projects = await requestJson("/api/v1/projects");
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

    async function refreshSelectedProject() {
      const project = projects.find((item) => item.id === selectedProjectId);
      setText("selected-project", project ? project.slug : "-");
      $("project-json").textContent = JSON.stringify(project || {}, null, 2);
      if (!project) {
        renderWorkspaces([]);
        renderSessions([]);
        return;
      }
      const encoded = encodeURIComponent(project.id);
      const [workspaces, sessions] = await Promise.all([
        requestJson(`/api/v1/projects/${encoded}/workspaces`),
        requestJson(`/api/v1/sessions?project_id=${encoded}`),
      ]);
      renderWorkspaces(workspaces);
      renderSessions(sessions);
    }

    async function selectProject(projectId) {
      selectedProjectId = projectId;
      selectedSessionId = "";
      $("session-json").textContent = "{}";
      renderProjects();
      await refreshSelectedProject();
      setStatus(`Selected ${projectId}`);
    }

    function selectSession(session) {
      selectedSessionId = session.id;
      $("session-json").textContent = JSON.stringify(session, null, 2);
      document.querySelectorAll("#sessions tr").forEach((row) => {
        row.classList.toggle("selected", row.dataset.sessionId === selectedSessionId);
      });
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
    $("create-project").addEventListener("click", () => run(createProject));
    $("add-workspace").addEventListener("click", () => run(addWorkspace));
    $("create-session").addEventListener("click", () => run(createSession));
    $("close-session").addEventListener("click", () => run(closeSession));
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
      $("selected").textContent = JSON.stringify(interaction, null, 2);
      $("create-session-id").value = interaction.session_id;
      $("answer-text").value = interaction.answer || "";
      document.querySelectorAll("#interactions tr").forEach((row) => {
        row.classList.toggle("selected", row.dataset.interactionId === interactionId);
      });
      setStatus(`Selected ${interactionId}`);
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
      </div>
      <pre id="backend">{}</pre>
      <pre id="error" class="danger"></pre>
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
      setText("running", status.running);
      setText("tracked", status.tracked_sessions);
      setText("runs", status.run_count);
      setText("observed-count", status.last_observed_count);
      setText("exits", status.reported_exit_count);
      setText("losses", status.reported_lost_count);
      setText("auto-restart", status.auto_restart_on_lost);
      setText("attempts", status.auto_restart_attempt_count);
      $("backend").textContent = JSON.stringify(status.backend_supervision || {}, null, 2);
      $("error").textContent = status.last_error || "";
    }

    function renderObserved(observed) {
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

    async function run(action) {
      try {
        await action();
      } catch (error) {
        setStatus(error.message);
      }
    }

    $("refresh").addEventListener("click", () => run(refresh));
    $("run-once").addEventListener("click", () => run(runOnce));
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
        <button id="save-device" class="primary" type="button">Create / Rotate</button>
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
          New Device Key
          <input
            id="device-key"
            type="password"
            autocomplete="new-password"
            placeholder="optional generated key"
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
      "session_events_ws",
      "rendered_events_ws",
      "terminal_ws",
      "bot_gateway_ws",
    ].join(",");
    let selectedDeviceId = "";

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

    function renderDevices(devices) {
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
      $("device-id").value = device.device_id;
      $("display-name").value = device.display_name || "";
      $("allowed-scopes").value = (device.allowed_scopes || []).join(",");
      $("device-key").value = "";
      $("selected").textContent = JSON.stringify(device, null, 2);
      document.querySelectorAll("#devices tr").forEach((row) => {
        row.classList.toggle("selected", row.dataset.deviceId === selectedDeviceId);
      });
    }

    function newDevice() {
      selectedDeviceId = "";
      $("device-id").value = "";
      $("display-name").value = "";
      $("allowed-scopes").value = defaultScopes;
      $("device-key").value = "";
      $("selected").textContent = "{}";
      $("generated-key").textContent = "{}";
      document.querySelectorAll("#devices tr").forEach((row) => row.classList.remove("selected"));
      setStatus("New device identity");
    }

    async function loadDevices() {
      setStatus("Loading");
      const devices = await requestJson(devicesUrl());
      renderDevices(devices);
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
      $("device-key").value = "";
      selectedDeviceId = saved.device_id;
      await loadDevices();
      setStatus(`Saved ${saved.device_id}`);
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
    $("include-revoked").addEventListener("change", () => run(loadDevices));
    $("new-device").addEventListener("click", newDevice);
    $("save-device").addEventListener("click", () => run(upsertDevice));
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
      </div>
      <pre id="worker">{}</pre>
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

    async function refreshRecords() {
      setStatus("Loading");
      const records = await requestJson(`/api/v1/bot-gateway/deliveries${deliveryQuery()}`);
      renderRecords(records);
    }

    async function refreshWorker() {
      const worker = await requestJson("/api/v1/bot-gateway/retry-worker");
      renderWorker(worker);
      const limits = await requestJson("/api/v1/bot-gateway/rate-limits");
      $("rate-limits").textContent = JSON.stringify(limits, null, 2);
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
    Promise.all([refreshRecords(), refreshWorker()]).catch((error) => {
      setStatus(error.message);
    });
  </script>
</body>
</html>
"""
