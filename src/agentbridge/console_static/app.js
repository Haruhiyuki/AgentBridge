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

    // —— 会话详情：队列 / 终端 / 写者租约 ——
    const detail = reactive({
      session: null,
      queue: null,
      lease: null,
      term: null,
      snapshot: "",
      loading: false,
    });
    function closeDetail() {
      detail.session = null;
    }
    async function openDetail(s) {
      detail.session = s;
      await loadDetail();
    }
    async function loadDetail() {
      if (!detail.session) return;
      const id = detail.session.id;
      detail.loading = true;
      try {
        // 各子资源独立容错：某项失败不影响其余展示。
        detail.queue = await api("/sessions/" + id + "/queue").catch(() => null);
        detail.lease = await api("/sessions/" + id + "/lease").catch(() => null);
        detail.term = await api("/sessions/" + id + "/terminal/status").catch(() => null);
        const snap = await api("/sessions/" + id + "/terminal/snapshot").catch(() => null);
        detail.snapshot = (snap && snap.snapshot) || "";
        // 同步会话最新状态（active_turn 等）。
        const fresh = sessions.value.find((x) => x.id === id);
        if (fresh) detail.session = fresh;
      } finally {
        detail.loading = false;
      }
    }
    async function refreshDetail() {
      await loadSessions();
      await loadDetail();
    }
    function qver() {
      return detail.queue && detail.queue.queue_version;
    }
    async function setPaused(paused) {
      try {
        await api(
          "/sessions/" + detail.session.id + "/queue/" + (paused ? "pause" : "resume"),
          { method: "POST", body: { actor: ADMIN_ACTOR, expected_queue_version: qver() } }
        );
        toast(paused ? "队列已暂停" : "队列已恢复");
        await loadDetail();
      } catch (e) {
        toast(e.message, true);
      }
    }
    async function clearQueue() {
      const n = (detail.queue && detail.queue.turns.length) || 0;
      if (!n) return;
      if (!confirm("确认清空队列中的 " + n + " 个排队任务？")) return;
      try {
        await api("/sessions/" + detail.session.id + "/queue/clear", {
          method: "POST",
          body: { actor: ADMIN_ACTOR, expected_queue_version: qver(), confirm_count: n },
        });
        toast("已清空 " + n + " 个排队任务");
        await loadDetail();
      } catch (e) {
        toast(e.message, true);
      }
    }
    async function moveUp(idx) {
      const turns = detail.queue.turns;
      if (idx <= 0) return;
      try {
        await api("/sessions/" + detail.session.id + "/queue/reorder", {
          method: "POST",
          body: {
            actor: ADMIN_ACTOR,
            turn_id: turns[idx].id,
            before_turn_id: turns[idx - 1].id,
            expected_queue_version: qver(),
          },
        });
        await loadDetail();
      } catch (e) {
        toast(e.message, true);
      }
    }
    async function releaseLease() {
      if (!detail.lease) return;
      if (!confirm("释放当前写者租约（" + leaseLabel(detail.lease) + "）？")) return;
      try {
        await api("/sessions/" + detail.session.id + "/lease/release", {
          method: "POST",
          body: { actor: ADMIN_ACTOR, epoch: detail.lease.epoch },
        });
        toast("租约已释放");
        await loadDetail();
      } catch (e) {
        toast(e.message, true);
      }
    }
    async function restartTerminal() {
      if (!confirm("重启该会话的终端？将沿用上次启动命令。")) return;
      try {
        await api("/sessions/" + detail.session.id + "/terminal/restart", {
          method: "POST",
          body: { actor: ADMIN_ACTOR },
        });
        toast("已请求重启终端");
        setTimeout(loadDetail, 800);
      } catch (e) {
        toast(e.message, true);
      }
    }
    function leaseLabel(l) {
      if (!l) return "无";
      return (l.owner_type || "?") + ":" + (l.owner_id || "?") + " · epoch " + l.epoch;
    }
    function turnPreview(t) {
      const p = (t.prompt || "").replace(/\s+/g, " ").trim();
      return p.length > 64 ? p.slice(0, 64) + "…" : p || "(空)";
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
      detail,
      openDetail,
      closeDetail,
      loadDetail,
      refreshDetail,
      setPaused,
      clearQueue,
      moveUp,
      releaseLease,
      restartTerminal,
      leaseLabel,
      turnPreview,
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
          <tr v-for="s in visibleSessions" :key="s.id"
              :class="{selected: detail.session && detail.session.id===s.id}">
            <td><code>{{ s.short_code }}</code></td>
            <td>{{ s.terminal_title || s.name }}</td>
            <td><span class="badge accent">{{ AGENT_LABELS[s.agent_type]||s.agent_type }}</span></td>
            <td><span :class="statusBadgeClass(s.status)">{{ STATUS_LABELS[s.status]||s.status }}</span></td>
            <td style="white-space:nowrap">
              <button @click="openDetail(s)">详情</button>
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

    <div class="panel" v-if="detail.session" style="margin-top:16px">
      <h2 style="display:flex;align-items:center;gap:10px">
        会话详情 · <code>{{ detail.session.short_code }}</code>
        <span :class="statusBadgeClass(detail.session.status)">{{ STATUS_LABELS[detail.session.status]||detail.session.status }}</span>
        <span class="spacer" style="flex:1"></span>
        <button @click="refreshDetail" :disabled="detail.loading">
          <span v-if="detail.loading" class="spin"></span><span v-else>刷新</span>
        </button>
        <button @click="closeDetail">收起</button>
      </h2>

      <div class="kv-grid">
        <div><span class="k">Agent</span><span class="badge accent">{{ AGENT_LABELS[detail.session.agent_type]||detail.session.agent_type }}</span></div>
        <div><span class="k">终端标题</span>{{ detail.session.terminal_title || detail.session.name || '—' }}</div>
        <div><span class="k">活动轮</span>{{ detail.session.active_turn_id ? '运行中 · '+detail.session.active_turn_id.slice(-6) : '空闲' }}</div>
        <div><span class="k">写者租约</span>
          <template v-if="detail.lease">{{ leaseLabel(detail.lease) }}
            <button class="danger" style="margin-left:8px;padding:2px 8px" @click="releaseLease">释放</button>
          </template>
          <span v-else class="muted">无（无人持有）</span>
        </div>
      </div>

      <h3 style="margin:18px 0 8px">队列
        <span v-if="detail.queue && detail.queue.queue_paused" class="badge amber">已暂停</span>
        <span class="muted" style="font-weight:400;font-size:12px" v-if="detail.queue">
          · 共 {{ detail.queue.turns.length }} 个排队 · 版本 {{ (detail.queue.queue_version||'').slice(0,8) }}</span>
      </h3>
      <div class="btn-row" style="margin-bottom:10px" v-if="detail.queue">
        <button v-if="!detail.queue.queue_paused" @click="setPaused(true)">暂停队列</button>
        <button class="primary" v-else @click="setPaused(false)">恢复队列</button>
        <button class="danger" @click="clearQueue" :disabled="!detail.queue.turns.length">清空队列</button>
      </div>
      <table v-if="detail.queue && detail.queue.turns.length">
        <thead><tr><th style="width:48px">#</th><th>排队任务</th><th>原因</th><th style="width:64px">操作</th></tr></thead>
        <tbody>
          <tr v-for="(t,idx) in detail.queue.turns" :key="t.id">
            <td>{{ idx+1 }}</td>
            <td>{{ turnPreview(t) }}</td>
            <td><span class="muted" style="font-size:12px">{{ t.queue_reason || '正常排队' }}</span></td>
            <td><button :disabled="idx===0" @click="moveUp(idx)" title="上移">↑</button></td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty" style="padding:14px">队列为空。</div>

      <h3 style="margin:18px 0 8px">终端
        <template v-if="detail.term">
          <span :class="detail.term.running ? 'badge green' : 'badge red'">{{ detail.term.running ? '运行中' : (detail.term.started ? '已退出' : '未启动') }}</span>
          <span class="muted" style="font-weight:400;font-size:12px">
            <template v-if="detail.term.pid"> · pid {{ detail.term.pid }}</template>
            <template v-if="detail.term.exit_code!==null && detail.term.exit_code!==undefined"> · 退出码 {{ detail.term.exit_code }}</template>
            <template v-if="detail.term.output_cursor"> · 游标 {{ detail.term.output_cursor }}</template>
          </span>
        </template>
      </h3>
      <div class="btn-row" style="margin-bottom:10px">
        <button @click="loadDetail">刷新快照</button>
        <button class="danger" @click="restartTerminal">重启终端</button>
      </div>
      <pre class="term-snap" v-if="detail.snapshot">{{ detail.snapshot }}</pre>
      <div v-else class="empty" style="padding:14px">暂无终端输出快照。</div>
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
const RISK_LEVELS = ["low", "medium", "high", "critical"];

const GovernanceView = {
  setup() {
    const tab = ref("policy");
    const TABS = [
      { id: "policy", label: "访问策略" },
      { id: "roles", label: "角色" },
      { id: "approval", label: "审批策略" },
      { id: "devices", label: "设备证书" },
    ];

    // ---- access policy ----
    const rules = ref([]);
    const blank = () => ({
      rule_id: "",
      effect: "allow",
      action: "*",
      resource_type: "*",
      resource_id: "",
      actor_ids: "",
      roles: "",
      priority: 100,
      enabled: true,
      description: "",
      chat_context_id: "",
    });
    const form = reactive(blank());
    const sim = reactive({
      target_actor_id: "",
      roles: "",
      action: "session.send",
      resource_type: "*",
      resource_id: "",
      result: null,
    });
    async function loadRules() {
      try {
        rules.value = await api("/access-policy/rules");
      } catch (e) {
        toast(e.message, true);
      }
    }
    function editRule(r) {
      Object.assign(form, {
        rule_id: r.id,
        effect: r.effect,
        action: r.action,
        resource_type: r.resource_type,
        resource_id: r.resource_id || "",
        actor_ids: (r.actor_ids || []).join(","),
        roles: (r.roles || []).join(","),
        priority: r.priority,
        enabled: r.enabled,
        description: r.description || "",
        chat_context_id: r.chat_context_id || "",
      });
    }
    function resetForm() {
      Object.assign(form, blank());
    }
    const csv = (s) =>
      s.split(",").map((x) => x.trim()).filter(Boolean);
    async function saveRule() {
      try {
        const body = {
          actor: ADMIN_ACTOR,
          rule_id: form.rule_id || null,
          effect: form.effect,
          action: form.action,
          resource_type: form.resource_type || "*",
          resource_id: form.resource_id || null,
          actor_ids: csv(form.actor_ids),
          roles: csv(form.roles),
          priority: Number(form.priority) || 100,
          enabled: form.enabled,
          description: form.description || null,
          chat_context_id: form.chat_context_id || null,
        };
        if (form.rule_id) {
          await api("/access-policy/rules/" + form.rule_id, { method: "PUT", body });
        } else {
          await api("/access-policy/rules", { method: "POST", body });
        }
        toast("规则已保存");
        resetForm();
        await loadRules();
      } catch (e) {
        toast(e.message, true);
      }
    }
    async function deleteRule(r) {
      try {
        await api("/access-policy/rules/" + r.id + "/delete", {
          method: "POST",
          body: { actor: ADMIN_ACTOR },
        });
        toast("规则已删除");
        await loadRules();
      } catch (e) {
        toast(e.message, true);
      }
    }
    async function simulate() {
      try {
        sim.result = await api("/access-policy/simulate", {
          method: "POST",
          body: {
            actor: ADMIN_ACTOR,
            target_actor: { id: sim.target_actor_id, roles: csv(sim.roles) },
            action: sim.action,
            resource_type: sim.resource_type || "*",
            resource_id: sim.resource_id || null,
          },
        });
      } catch (e) {
        toast(e.message, true);
      }
    }

    // ---- roles ----
    const roleCtx = ref("");
    const roleBindings = ref([]);
    const grant = reactive({ target_actor_id: "", roles: "" });
    async function loadRoles() {
      if (!roleCtx.value) return;
      try {
        roleBindings.value = await api(
          "/chat-contexts/" + roleCtx.value + "/roles"
        );
      } catch (e) {
        toast(e.message, true);
      }
    }
    async function grantRoles() {
      try {
        await api("/chat-contexts/" + roleCtx.value + "/roles/grant", {
          method: "POST",
          body: {
            actor: ADMIN_ACTOR,
            target_actor_id: grant.target_actor_id,
            roles: csv(grant.roles),
          },
        });
        toast("已授予角色");
        grant.target_actor_id = "";
        grant.roles = "";
        await loadRoles();
      } catch (e) {
        toast(e.message, true);
      }
    }
    async function revokeRoles(b) {
      try {
        await api("/chat-contexts/" + roleCtx.value + "/roles/revoke", {
          method: "POST",
          body: { actor: ADMIN_ACTOR, target_actor_id: b.actor_id, roles: b.roles },
        });
        toast("已撤销角色");
        await loadRoles();
      } catch (e) {
        toast(e.message, true);
      }
    }

    // ---- approval policy ----
    const apProjects = ref([]);
    const apProject = ref("");
    const quorum = reactive({ low: 1, medium: 1, high: 1, critical: 2 });
    async function loadApProjects() {
      try {
        apProjects.value = await api("/projects");
      } catch (e) {
        toast(e.message, true);
      }
    }
    async function loadApproval() {
      if (!apProject.value) return;
      try {
        const p = await api("/projects/" + apProject.value + "/approval-policy");
        const q = (p && p.quorum_by_risk) || {};
        RISK_LEVELS.forEach((r) => {
          quorum[r] = q[r] != null ? q[r] : quorum[r];
        });
      } catch (e) {
        toast(e.message, true);
      }
    }
    async function saveApproval() {
      try {
        await api("/projects/" + apProject.value + "/approval-policy", {
          method: "PUT",
          body: {
            actor: ADMIN_ACTOR,
            quorum_by_risk: {
              low: Number(quorum.low),
              medium: Number(quorum.medium),
              high: Number(quorum.high),
              critical: Number(quorum.critical),
            },
          },
        });
        toast("审批策略已保存");
      } catch (e) {
        toast(e.message, true);
      }
    }

    // ---- devices ----
    const devices = ref([]);
    const includeRevoked = ref(false);
    const dev = reactive({ device_id: "", display_name: "", scopes: "", newKey: "" });
    async function loadDevices() {
      try {
        devices.value = await api("/device-identities", {
          params: { include_revoked: includeRevoked.value },
        });
      } catch (e) {
        toast(e.message, true);
      }
    }
    async function createDevice() {
      try {
        const res = await api("/device-identities", {
          method: "POST",
          body: {
            actor: ADMIN_ACTOR,
            device_id: dev.device_id,
            display_name: dev.display_name || null,
            allowed_scopes: csv(dev.scopes),
          },
        });
        dev.newKey = res.device_key || "（未生成新密钥）";
        toast("设备已保存");
        await loadDevices();
      } catch (e) {
        toast(e.message, true);
      }
    }
    async function revokeDevice(d) {
      try {
        await api("/device-identities/" + d.device_id + "/revoke", {
          method: "POST",
          body: { actor: ADMIN_ACTOR },
        });
        toast("设备已撤销");
        await loadDevices();
      } catch (e) {
        toast(e.message, true);
      }
    }
    async function scanCerts() {
      try {
        const r = await api("/device-identities/certificates/scan", {
          method: "POST",
          body: { actor: ADMIN_ACTOR },
        });
        toast(
          "证书扫描：过期 " +
            (r.expired_count ?? 0) +
            " · 临期 " +
            (r.expiring_count ?? 0)
        );
      } catch (e) {
        toast(e.message, true);
      }
    }

    function onTab(t) {
      tab.value = t;
      if (t === "policy") loadRules();
      else if (t === "approval") loadApProjects();
      else if (t === "devices") loadDevices();
    }
    onMounted(loadRules);

    return {
      tab,
      TABS,
      onTab,
      rules,
      form,
      sim,
      loadRules,
      editRule,
      resetForm,
      saveRule,
      deleteRule,
      simulate,
      roleCtx,
      roleBindings,
      grant,
      loadRoles,
      grantRoles,
      revokeRoles,
      apProjects,
      apProject,
      quorum,
      loadApproval,
      saveApproval,
      RISK_LEVELS,
      devices,
      includeRevoked,
      dev,
      loadDevices,
      createDevice,
      revokeDevice,
      scanCerts,
    };
  },
  template: `
  <div>
    <div class="page-head"><h1>治理</h1><span class="sub">权限 / 角色 / 审批 / 设备</span></div>
    <div class="nav" style="margin-bottom:16px;flex:none">
      <a v-for="t in TABS" :key="t.id" href="javascript:void 0"
         :class="{active: tab===t.id}" @click="onTab(t.id)">{{ t.label }}</a>
    </div>

    <!-- 访问策略 -->
    <div v-if="tab==='policy'" class="grid" style="grid-template-columns:1.4fr 1fr">
      <div class="panel">
        <h2>策略规则（{{ rules.length }}）</h2>
        <table v-if="rules.length">
          <thead><tr><th>效果</th><th>动作</th><th>资源</th><th>角色/Actor</th><th>优先级</th><th></th></tr></thead>
          <tbody>
            <tr v-for="r in rules" :key="r.id" @click="editRule(r)" style="cursor:pointer">
              <td><span :class="r.effect==='deny'?'badge red':'badge green'">{{ r.effect }}</span></td>
              <td><code>{{ r.action }}</code></td>
              <td><code>{{ r.resource_type }}{{ r.resource_id?(':'+r.resource_id):'' }}</code></td>
              <td class="muted" style="font-size:12px">{{ (r.roles||[]).join(',') }} {{ (r.actor_ids||[]).join(',') }}</td>
              <td>{{ r.priority }}<span v-if="!r.enabled" class="badge" style="margin-left:6px">停用</span></td>
              <td><button class="danger" @click.stop="deleteRule(r)">删</button></td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty">还没有规则。deny 优先；无规则时回退 RBAC。</div>
      </div>
      <div>
        <div class="panel" style="margin-bottom:14px">
          <h2>{{ form.rule_id ? '编辑规则' : '新建规则' }}</h2>
          <select v-model="form.effect"><option value="allow">allow（允许）</option><option value="deny">deny（拒绝）</option></select>
          <input type="text" v-model="form.action" placeholder="动作 如 session.send 或 *" />
          <input type="text" v-model="form.resource_type" placeholder="资源类型 如 session 或 *" />
          <input type="text" v-model="form.resource_id" placeholder="资源 ID（可空）" />
          <input type="text" v-model="form.roles" placeholder="角色（逗号分隔，可空）" />
          <input type="text" v-model="form.actor_ids" placeholder="Actor IDs（逗号分隔，可空）" />
          <input type="text" v-model="form.priority" placeholder="优先级（小=优先）" />
          <input type="text" v-model="form.description" placeholder="说明（可空）" />
          <label class="muted" style="display:block;margin-bottom:10px">
            <input type="checkbox" v-model="form.enabled" style="width:auto;margin:0 6px 0 0" />启用
          </label>
          <div class="btn-row">
            <button class="primary" @click="saveRule">保存</button>
            <button @click="resetForm" v-if="form.rule_id">新建</button>
          </div>
        </div>
        <div class="panel">
          <h2>模拟器</h2>
          <input type="text" v-model="sim.target_actor_id" placeholder="目标 Actor ID" />
          <input type="text" v-model="sim.roles" placeholder="目标角色（逗号分隔）" />
          <input type="text" v-model="sim.action" placeholder="动作" />
          <input type="text" v-model="sim.resource_type" placeholder="资源类型" />
          <button class="primary" @click="simulate">模拟</button>
          <div v-if="sim.result" style="margin-top:12px">
            <span :class="sim.result.decision && sim.result.decision.allowed ? 'badge green':'badge red'">
              {{ sim.result.decision && sim.result.decision.allowed ? '允许' : '拒绝' }}
            </span>
            <pre style="white-space:pre-wrap;font-size:12px;color:var(--text-dim);margin-top:8px">{{ JSON.stringify(sim.result.decision, null, 2) }}</pre>
          </div>
        </div>
      </div>
    </div>

    <!-- 角色 -->
    <div v-if="tab==='roles'" class="panel">
      <h2>聊天级角色绑定</h2>
      <div class="btn-row" style="margin-bottom:12px">
        <input type="text" v-model="roleCtx" placeholder="Chat Context ID" style="margin:0;max-width:340px" />
        <button @click="loadRoles">加载</button>
      </div>
      <table v-if="roleBindings.length">
        <thead><tr><th>Actor</th><th>角色</th><th></th></tr></thead>
        <tbody>
          <tr v-for="b in roleBindings" :key="b.actor_id">
            <td><code>{{ b.actor_id }}</code></td>
            <td>{{ (b.roles||[]).join(', ') }}</td>
            <td><button class="danger" @click="revokeRoles(b)">撤销全部</button></td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty">输入 Chat Context ID 加载，或下方授予。</div>
      <h2 style="margin-top:18px">授予角色</h2>
      <div class="btn-row">
        <input type="text" v-model="grant.target_actor_id" placeholder="目标 Actor ID" style="margin:0" />
        <input type="text" v-model="grant.roles" placeholder="角色（逗号分隔）" style="margin:0" />
        <button class="primary" @click="grantRoles" :disabled="!roleCtx">授予</button>
      </div>
    </div>

    <!-- 审批策略 -->
    <div v-if="tab==='approval'" class="panel">
      <h2>项目审批 Quorum</h2>
      <div class="btn-row" style="margin-bottom:14px">
        <select v-model="apProject" @change="loadApproval" style="margin:0;max-width:340px">
          <option value="">选择项目…</option>
          <option v-for="p in apProjects" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select>
      </div>
      <div v-if="apProject" style="max-width:360px">
        <div v-for="r in RISK_LEVELS" :key="r" style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
          <span style="width:90px" class="muted">{{ r }}</span>
          <input type="text" v-model="quorum[r]" style="margin:0;width:90px" />
          <span class="muted" style="font-size:12px" v-if="r==='high'||r==='critical'">需高危审批人</span>
        </div>
        <button class="primary" @click="saveApproval" style="margin-top:8px">保存</button>
      </div>
    </div>

    <!-- 设备 -->
    <div v-if="tab==='devices'" class="grid" style="grid-template-columns:1.4fr 1fr">
      <div class="panel">
        <h2 style="display:flex;align-items:center">设备身份（{{ devices.length }}）
          <span style="flex:1"></span>
          <label class="muted" style="font-size:12px;text-transform:none;font-weight:400">
            <input type="checkbox" v-model="includeRevoked" @change="loadDevices" style="width:auto;margin:0 4px 0 0" />含已撤销</label>
        </h2>
        <table v-if="devices.length">
          <thead><tr><th>Device</th><th>状态</th><th>Scopes</th><th>证书</th><th></th></tr></thead>
          <tbody>
            <tr v-for="d in devices" :key="d.id">
              <td><code>{{ d.device_id }}</code><div class="muted" style="font-size:11px">{{ d.display_name }}</div></td>
              <td><span :class="d.status==='active'?'badge green':'badge red'">{{ d.status }}</span></td>
              <td class="muted" style="font-size:11px">{{ (d.allowed_scopes||[]).length }} 项</td>
              <td>{{ (d.certificate_fingerprints||[]).length }}</td>
              <td><button class="danger" @click="revokeDevice(d)" v-if="d.status==='active'">撤销</button></td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty">还没有设备身份。</div>
      </div>
      <div class="panel">
        <h2>新建设备</h2>
        <input type="text" v-model="dev.device_id" placeholder="Device ID" />
        <input type="text" v-model="dev.display_name" placeholder="显示名（可空）" />
        <input type="text" v-model="dev.scopes" placeholder="scopes（逗号分隔，如 session_read,terminal_read）" />
        <div class="btn-row">
          <button class="primary" @click="createDevice">创建/更新</button>
          <button @click="scanCerts">扫描证书健康</button>
        </div>
        <div v-if="dev.newKey" style="margin-top:12px">
          <div class="muted" style="font-size:12px">生成的 device key（仅显示一次）：</div>
          <code style="word-break:break-all;font-size:12px">{{ dev.newKey }}</code>
        </div>
      </div>
    </div>
  </div>`,
};

const OUTCOME_LABELS = { success: "成功", denied: "拒绝", failed: "失败" };
function outcomeBadge(o) {
  return o === "success" ? "badge green" : o === "denied" ? "badge amber" : "badge red";
}
function fmtTs(ts) {
  return (ts || "").replace("T", " ").slice(0, 19);
}
function jsonPreview(obj, n = 90) {
  let s;
  try {
    s = typeof obj === "string" ? obj : JSON.stringify(obj || {});
  } catch (e) {
    s = String(obj);
  }
  return s.length > n ? s.slice(0, n) + "…" : s;
}

const AuditView = {
  setup() {
    const tab = ref("audit");

    // —— 审计链 ——
    const aFilters = reactive({ action: "", actor_id: "", session_id: "", q: "", limit: 100 });
    const audits = ref([]);
    const aLoading = ref(false);
    async function loadAudit() {
      aLoading.value = true;
      try {
        const params = { limit: aFilters.limit };
        for (const k of ["action", "actor_id", "session_id", "q"])
          if (aFilters[k]) params[k] = aFilters[k];
        audits.value = await api("/audit", { params });
      } catch (e) {
        toast(e.message, true);
      } finally {
        aLoading.value = false;
      }
    }
    function resetAudit() {
      aFilters.action = aFilters.actor_id = aFilters.session_id = aFilters.q = "";
      loadAudit();
    }

    // —— 语义事件 ——
    const eFilters = reactive({ session_id: "", event_type: "", source: "", q: "", limit: 100 });
    const events = ref([]);
    const eLoading = ref(false);
    async function loadEvents() {
      eLoading.value = true;
      try {
        const params = { limit: eFilters.limit };
        for (const k of ["session_id", "event_type", "source", "q"])
          if (eFilters[k]) params[k] = eFilters[k];
        events.value = await api("/events", { params });
      } catch (e) {
        toast(e.message, true);
      } finally {
        eLoading.value = false;
      }
    }
    function resetEvents() {
      eFilters.session_id = eFilters.event_type = eFilters.source = eFilters.q = "";
      loadEvents();
    }

    function onTab(t) {
      tab.value = t;
      if (t === "events" && !events.value.length) loadEvents();
    }

    onMounted(loadAudit);

    return {
      tab,
      onTab,
      aFilters,
      audits,
      aLoading,
      loadAudit,
      resetAudit,
      eFilters,
      events,
      eLoading,
      loadEvents,
      resetEvents,
      OUTCOME_LABELS,
      outcomeBadge,
      fmtTs,
      jsonPreview,
    };
  },
  template: `
  <div>
    <div class="page-head">
      <h1>审计</h1>
      <span class="sub">审计链 / 语义事件检索</span>
    </div>
    <div class="nav" style="margin-bottom:16px;flex:none">
      <a href="javascript:void 0" :class="{active: tab==='audit'}" @click="onTab('audit')">审计链</a>
      <a href="javascript:void 0" :class="{active: tab==='events'}" @click="onTab('events')">语义事件</a>
    </div>

    <div v-show="tab==='audit'" class="panel">
      <div class="filter-row">
        <input type="text" v-model="aFilters.action" placeholder="动作 如 command.executed" />
        <input type="text" v-model="aFilters.actor_id" placeholder="操作者 如 onebot:138…" />
        <input type="text" v-model="aFilters.session_id" placeholder="会话 ID" />
        <input type="text" v-model="aFilters.q" placeholder="全文检索" @keyup.enter="loadAudit" />
        <button class="primary" @click="loadAudit" :disabled="aLoading">
          <span v-if="aLoading" class="spin"></span><span v-else>查询</span>
        </button>
        <button @click="resetAudit">重置</button>
      </div>
      <table v-if="audits.length">
        <thead><tr><th>时间</th><th>操作者</th><th>动作</th><th>结果</th><th>会话</th><th>详情</th></tr></thead>
        <tbody>
          <tr v-for="a in audits" :key="a.id">
            <td style="white-space:nowrap">{{ fmtTs(a.created_at) }}</td>
            <td><code>{{ a.actor_id }}</code></td>
            <td>{{ a.action }}</td>
            <td><span :class="outcomeBadge(a.outcome)">{{ OUTCOME_LABELS[a.outcome]||a.outcome }}</span></td>
            <td><code v-if="a.session_id">{{ a.session_id.slice(-6) }}</code><span v-else class="muted">—</span></td>
            <td class="mono-cell muted">{{ jsonPreview(a.details) }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty">无匹配审计记录。</div>
      <div class="muted" style="font-size:12px;margin-top:10px">共 {{ audits.length }} 条（最新在前，受 limit 限制）。</div>
    </div>

    <div v-show="tab==='events'" class="panel">
      <div class="filter-row">
        <input type="text" v-model="eFilters.session_id" placeholder="会话 ID" />
        <input type="text" v-model="eFilters.event_type" placeholder="事件类型 如 turn.completed" />
        <input type="text" v-model="eFilters.source" placeholder="来源 如 agent_adapter" />
        <input type="text" v-model="eFilters.q" placeholder="全文检索" @keyup.enter="loadEvents" />
        <button class="primary" @click="loadEvents" :disabled="eLoading">
          <span v-if="eLoading" class="spin"></span><span v-else>查询</span>
        </button>
        <button @click="resetEvents">重置</button>
      </div>
      <table v-if="events.length">
        <thead><tr><th>时间</th><th>seq</th><th>类型</th><th>来源</th><th>会话/轮</th><th>载荷</th></tr></thead>
        <tbody>
          <tr v-for="e in events" :key="e.id">
            <td style="white-space:nowrap">{{ fmtTs(e.created_at) }}</td>
            <td>{{ e.seq }}</td>
            <td><span class="badge accent">{{ e.type }}</span></td>
            <td class="muted">{{ e.source }}</td>
            <td><code v-if="e.session_id">{{ e.session_id.slice(-6) }}</code><span v-if="e.turn_id" class="muted"> · {{ e.turn_id.slice(-6) }}</span></td>
            <td class="mono-cell muted">{{ jsonPreview(e.payload) }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty">无匹配事件。</div>
      <div class="muted" style="font-size:12px;margin-top:10px">共 {{ events.length }} 条（最新在前，受 limit 限制）。</div>
    </div>
  </div>`,
};
const STATUS_BADGE = {
  pass: "badge green", ok: "badge green", healthy: "badge green", running: "badge green",
  warn: "badge amber", degraded: "badge amber",
  fail: "badge red", error: "badge red", unhealthy: "badge red",
};
function sbadge(s) {
  return STATUS_BADGE[s] || "badge";
}

const SystemView = {
  setup() {
    const health = ref(null);
    const readiness = ref(null);
    const lifecycle = ref(null);
    const retry = ref(null);
    const deliveries = ref([]);
    const loading = ref(false);
    const onlyIssues = ref(true);

    async function loadAll() {
      loading.value = true;
      try {
        health.value = await api("/health").catch(() => null);
        readiness.value = await api("/readiness").catch(() => null);
        lifecycle.value = await api("/terminal/lifecycle-monitor").catch(() => null);
        retry.value = await api("/bot-gateway/retry-worker").catch(() => null);
        deliveries.value = (await api("/bot-gateway/deliveries").catch(() => [])) || [];
      } finally {
        loading.value = false;
      }
    }
    const counts = computed(
      () => (readiness.value && readiness.value.summary && readiness.value.summary.counts) || {}
    );
    const checks = computed(() => {
      const all = (readiness.value && readiness.value.checks) || [];
      return onlyIssues.value ? all.filter((c) => c.status !== "pass") : all;
    });

    onMounted(loadAll);

    return {
      health,
      readiness,
      lifecycle,
      retry,
      deliveries,
      loading,
      onlyIssues,
      counts,
      checks,
      loadAll,
      sbadge,
      fmtTs,
    };
  },
  template: `
  <div>
    <div class="page-head">
      <h1>系统</h1>
      <span class="sub">健康 / 就绪 / 终端生命周期 / Bot 投递</span>
      <span class="spacer" style="flex:1"></span>
      <button @click="loadAll" :disabled="loading">
        <span v-if="loading" class="spin"></span><span v-else>刷新</span>
      </button>
    </div>

    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(240px,1fr));margin-bottom:16px">
      <div class="panel" v-if="health">
        <h2>健康 <span :class="sbadge(health.status)">{{ health.status }}</span></h2>
        <div class="kv-grid">
          <div><span class="k">存储</span>{{ health.storage }}</div>
          <div><span class="k">项目</span>{{ health.projects }}</div>
          <div><span class="k">会话</span>{{ health.sessions }}</div>
        </div>
      </div>

      <div class="panel" v-if="readiness">
        <h2>就绪 <span :class="sbadge(readiness.status)">{{ readiness.status }}</span></h2>
        <div class="kv-grid">
          <div><span class="k">通过</span><span class="badge green">{{ counts.pass||0 }}</span></div>
          <div><span class="k">告警</span><span class="badge amber">{{ counts.warn||0 }}</span></div>
          <div><span class="k">失败</span><span class="badge red">{{ counts.fail||0 }}</span></div>
        </div>
        <div class="muted" style="font-size:12px;margin-top:8px">检查于 {{ fmtTs(readiness.checked_at) }}</div>
      </div>

      <div class="panel" v-if="lifecycle">
        <h2>终端生命周期 <span :class="lifecycle.running?'badge green':'badge red'">{{ lifecycle.running?'监控中':'已停' }}</span></h2>
        <div class="kv-grid">
          <div><span class="k">跟踪会话</span>{{ lifecycle.tracked_sessions }}</div>
          <div><span class="k">轮询次数</span>{{ lifecycle.run_count }}</div>
          <div><span class="k">退出/丢失</span>{{ lifecycle.reported_exit_count }} / {{ lifecycle.reported_lost_count }}</div>
          <div><span class="k">自动重启</span>{{ lifecycle.auto_restart_on_lost?'开':'关' }}（{{ lifecycle.auto_restart_attempt_count }} 次）</div>
        </div>
        <div class="muted" style="font-size:12px;margin-top:8px" v-if="lifecycle.last_error">最近错误：{{ lifecycle.last_error }}</div>
      </div>

      <div class="panel" v-if="retry">
        <h2>Bot 投递 <span :class="retry.running?'badge green':(retry.enabled?'badge amber':'badge')">{{ retry.running?'重试中':(retry.enabled?'已启用':'未启用') }}</span></h2>
        <div class="kv-grid">
          <div><span class="k">投递记录</span>{{ deliveries.length }}</div>
          <div><span class="k">重试间隔</span>{{ retry.interval_seconds }}s</div>
          <div><span class="k">上次处理</span>{{ retry.last_record_count!=null?retry.last_record_count:'—' }}</div>
          <div><span class="k">上次运行</span>{{ retry.last_run_at?fmtTs(retry.last_run_at):'—' }}</div>
        </div>
        <div class="muted" style="font-size:12px;margin-top:8px" v-if="retry.last_error">最近错误：{{ retry.last_error }}</div>
      </div>
    </div>

    <div class="panel" v-if="readiness">
      <h2 style="display:flex;align-items:center;gap:10px">就绪检查明细
        <span class="spacer" style="flex:1"></span>
        <label class="muted" style="font-size:12px;text-transform:none;font-weight:400">
          <input type="checkbox" v-model="onlyIssues" style="width:auto;margin:0 4px 0 0" />仅看告警/失败
        </label>
      </h2>
      <table v-if="checks.length">
        <thead><tr><th>检查项</th><th>分类</th><th>状态</th><th>说明</th></tr></thead>
        <tbody>
          <tr v-for="c in checks" :key="c.id">
            <td><code>{{ c.id }}</code></td>
            <td class="muted">{{ c.category }}</td>
            <td><span :class="sbadge(c.status)">{{ c.status }}</span></td>
            <td class="muted" style="font-size:12px">{{ c.summary }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty">{{ onlyIssues ? '没有告警或失败项 🎉' : '暂无检查项。' }}</div>
    </div>
  </div>`,
};

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
app.mount("#app");
