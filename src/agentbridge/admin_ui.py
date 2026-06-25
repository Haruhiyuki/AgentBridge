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
    <a href="/admin/terminal-lifecycle">
      <strong>Terminal Lifecycle</strong>
      <span>Monitor status, backend supervision, run once</span>
    </a>
    <a href="/admin/bot-delivery">
      <strong>Bot Delivery</strong>
      <span>Records, retry worker, due retry, rate limits</span>
    </a>
  </main>
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
      <a href="/admin/terminal-lifecycle">Terminal Lifecycle</a>
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
      <a href="/admin/terminal-lifecycle">Terminal Lifecycle</a>
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
      <a href="/admin/terminal-lifecycle">Terminal Lifecycle</a>
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
