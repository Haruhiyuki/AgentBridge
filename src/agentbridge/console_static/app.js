import {
  createApp,
  reactive,
  ref,
  computed,
  onMounted,
} from "./vendor/vue.esm-browser.prod.js";

// 控制台以 admin 身份操作（写操作需要 actor 字段）。
const ADMIN_ACTOR = { id: "console:admin", roles: ["admin"] };

const state = reactive({
  route: location.hash.slice(1) || "/ops",
  authed: true,
  health: null,
  toast: null,
});

window.addEventListener("hashchange", () => {
  state.route = location.hash.slice(1) || "/ops";
});

function go(route) {
  location.hash = route;
}

function toast(message, isError = false) {
  state.toast = { message: String(message), isError };
  setTimeout(() => {
    if (state.toast && state.toast.message === String(message)) state.toast = null;
  }, 4200);
}

async function api(path, { method = "GET", body, params } = {}) {
  let url = "/api/v1" + path;
  if (params) url += "?" + new URLSearchParams(params).toString();
  const res = await fetch(url, {
    method,
    credentials: "include",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401 || res.status === 403) {
    state.authed = false;
    throw new Error("需要登录");
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    throw new Error((data && (data.message || data.detail)) || "HTTP " + res.status);
  }
  return data;
}

const AGENT_LABELS = { claude: "Claude", codex: "Codex", generic_tui: "通用终端" };
const STATUS_LABELS = {
  creating: "创建中",
  starting: "启动中",
  idle: "空闲",
  running: "运行中",
  waiting_interaction: "等待交互",
  human_controlled: "本地接管",
  suspended: "已挂起",
  recovering: "恢复中",
  error: "异常",
  closing: "关闭中",
  closed: "已关闭",
  archived: "已归档",
};
function statusBadgeClass(status) {
  if (["idle", "running"].includes(status)) return "badge green";
  if (["recovering", "waiting_interaction", "starting", "creating"].includes(status))
    return "badge amber";
  if (["error"].includes(status)) return "badge red";
  return "badge";
}

// ---------------- Operations: projects + sessions ----------------
const OperationsView = {
  setup() {
    const projects = ref([]);
    const selected = ref(null);
    const sessions = ref([]);
    const loading = ref(false);
    const showClosed = ref(false);

    async function loadProjects() {
      loading.value = true;
      try {
        projects.value = await api("/projects");
        if (!selected.value && projects.value.length) {
          await selectProject(projects.value[0]);
        } else if (selected.value) {
          await loadSessions();
        }
      } catch (e) {
        toast(e.message, true);
      } finally {
        loading.value = false;
      }
    }
    async function selectProject(p) {
      selected.value = p;
      await loadSessions();
    }
    async function loadSessions() {
      if (!selected.value) return;
      try {
        sessions.value = await api("/sessions", {
          params: { project_id: selected.value.id },
        });
      } catch (e) {
        toast(e.message, true);
      }
    }
    const visibleSessions = computed(() => {
      const dead = ["closed", "archived", "recovering", "error", "closing"];
      return showClosed.value
        ? sessions.value
        : sessions.value.filter((s) => !dead.includes(s.status));
    });
    const hiddenCount = computed(
      () => sessions.value.length - visibleSessions.value.length
    );

    async function newSession(agent) {
      try {
        await api("/sessions", {
          method: "POST",
          body: {
            actor: ADMIN_ACTOR,
            project_id: selected.value.id,
            name: AGENT_LABELS[agent] || agent,
            agent_type: agent,
            visibility: "group",
          },
        });
        toast("已新建 " + (AGENT_LABELS[agent] || agent) + " 会话");
        await loadSessions();
      } catch (e) {
        toast(e.message, true);
      }
    }
    async function closeSession(s) {
      try {
        await api("/sessions/" + s.id + "/close", {
          method: "POST",
          body: { actor: ADMIN_ACTOR },
        });
        toast("已关闭 [" + s.short_code + "]");
        await loadSessions();
      } catch (e) {
        toast(e.message, true);
      }
    }
    async function pruneDead() {
      const dead = sessions.value.filter((s) =>
        ["recovering", "error", "closing"].includes(s.status)
      );
      let n = 0;
      for (const s of dead) {
        try {
          await api("/sessions/" + s.id + "/close", {
            method: "POST",
            body: { actor: ADMIN_ACTOR },
          });
          n++;
        } catch (e) {
          /* skip */
        }
      }
      toast("已清理 " + n + " 个不可用会话");
      await loadSessions();
    }

    onMounted(loadProjects);

    return {
      projects,
      selected,
      sessions,
      visibleSessions,
      hiddenCount,
      loading,
      showClosed,
      selectProject,
      loadProjects,
      loadSessions,
      newSession,
      closeSession,
      pruneDead,
      AGENT_LABELS,
      STATUS_LABELS,
      statusBadgeClass,
    };
  },
  template: `
  <div>
    <div class="page-head">
      <h1>运维</h1>
      <span class="sub">项目 / 会话 / agent 实时管理</span>
      <span class="spacer" style="flex:1"></span>
      <button @click="loadProjects" :disabled="loading">
        <span v-if="loading" class="spin"></span><span v-else>刷新</span>
      </button>
    </div>

    <div class="panel" style="margin-bottom:16px">
      <h2>项目（{{ projects.length }}）</h2>
      <div class="card-row" v-if="projects.length">
        <div v-for="p in projects" :key="p.id"
             class="card" :class="{selected: selected && selected.id===p.id}"
             @click="selectProject(p)">
          <div class="title">{{ p.name }}</div>
          <div class="meta">{{ p.slug }} · 默认 {{ AGENT_LABELS[p.default_agent]||p.default_agent }}
            · 上限 {{ p.max_active_sessions }} 会话</div>
        </div>
      </div>
      <div v-else class="empty">还没有项目。</div>
    </div>

    <div class="panel" v-if="selected">
      <h2 style="display:flex;align-items:center;gap:10px">
        {{ selected.name }} · 会话
        <span class="spacer" style="flex:1"></span>
        <label class="muted" style="font-size:12px;text-transform:none;font-weight:400">
          <input type="checkbox" v-model="showClosed" style="width:auto;margin:0 4px 0 0" />显示全部
        </label>
      </h2>
      <div class="btn-row" style="margin-bottom:12px">
        <button class="primary" @click="newSession('claude')">+ Claude 会话</button>
        <button @click="newSession('codex')">+ Codex 会话</button>
        <button class="danger" @click="pruneDead" v-if="hiddenCount>0">清理不可用会话</button>
      </div>
      <table v-if="visibleSessions.length">
        <thead>
          <tr><th>会话</th><th>终端标题</th><th>Agent</th><th>状态</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="s in visibleSessions" :key="s.id">
            <td><code>{{ s.short_code }}</code></td>
            <td>{{ s.terminal_title || s.name }}</td>
            <td><span class="badge accent">{{ AGENT_LABELS[s.agent_type]||s.agent_type }}</span></td>
            <td><span :class="statusBadgeClass(s.status)">{{ STATUS_LABELS[s.status]||s.status }}</span></td>
            <td>
              <button class="danger" @click="closeSession(s)"
                      v-if="!['closed','archived'].includes(s.status)">关闭</button>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty">该项目暂无可用会话。</div>
      <div class="muted" style="font-size:12px;margin-top:10px" v-if="hiddenCount>0 && !showClosed">
        另有 {{ hiddenCount }} 个已关闭/恢复中会话已隐藏。
      </div>
    </div>
  </div>`,
};

// ---------------- stub views (后续增量填充) ----------------
function stub(title, note) {
  return {
    template: `<div><div class="page-head"><h1>${title}</h1></div>
      <div class="panel"><div class="empty">${note}<br/>
      当前可在 <a href="/admin" target="_blank">旧版 /admin</a> 操作；本页将在后续增量上线。</div></div></div>`,
  };
}
const GovernanceView = stub("治理", "访问策略 / 角色 / 审批策略 / 设备证书");
const AuditView = stub("审计", "审计链 / 语义事件检索");
const SystemView = stub("系统", "健康 / 就绪 / 终端生命周期 / Bot 投递");

const ROUTES = [
  { path: "/ops", label: "运维", comp: OperationsView },
  { path: "/governance", label: "治理", comp: GovernanceView },
  { path: "/audit", label: "审计", comp: AuditView },
  { path: "/system", label: "系统", comp: SystemView },
];

// ---------------- login ----------------
const LoginView = {
  setup() {
    const token = ref("");
    function submit() {
      if (!token.value.trim()) return;
      // 跳转到 /console?admin_token=… 由服务端设置 HttpOnly cookie 后重定向回来。
      location.href = "/console?admin_token=" + encodeURIComponent(token.value.trim());
    }
    return { token, submit };
  },
  template: `
  <div class="login">
    <div class="box">
      <h1>AgentBridge 控制台</h1>
      <p>请输入管理令牌（AGENTBRIDGE_ADMIN_TOKEN / API_TOKEN）解锁。</p>
      <input type="password" v-model="token" placeholder="管理令牌"
             @keyup.enter="submit" autofocus />
      <button class="primary" style="width:100%" @click="submit">进入控制台</button>
    </div>
  </div>`,
};

// ---------------- app shell ----------------
const App = {
  setup() {
    async function loadHealth() {
      try {
        state.health = await api("/health");
      } catch (e) {
        state.health = null;
      }
    }
    onMounted(() => {
      loadHealth();
      setInterval(loadHealth, 15000);
    });
    const current = computed(
      () => ROUTES.find((r) => state.route.startsWith(r.path)) || ROUTES[0]
    );
    return { state, ROUTES, current, go };
  },
  template: `
  <div v-if="!state.authed"><login-view /></div>
  <div v-else class="app">
    <div class="topbar">
      <div class="brand"><span class="dot"></span>AgentBridge 控制台</div>
      <nav class="nav">
        <a v-for="r in ROUTES" :key="r.path" :href="'#'+r.path"
           :class="{active: current.path===r.path}">{{ r.label }}</a>
      </nav>
      <span class="spacer"></span>
      <div class="health" :class="state.health ? 'ok' : 'bad'">
        <span class="dot"></span>
        <span v-if="state.health">{{ state.health.storage }} · {{ state.health.sessions }} 会话</span>
        <span v-else>离线</span>
      </div>
    </div>
    <div class="main">
      <component :is="current.comp" :key="current.path" />
    </div>
    <div v-if="state.toast" class="toast" :class="{error: state.toast.isError}">
      {{ state.toast.message }}
    </div>
  </div>`,
  components: { LoginView },
};

const app = createApp(App);
app.component("login-view", LoginView);
ROUTES.forEach((r) => app.component(r.comp.name || r.path, r.comp));
app.mount("#app");
