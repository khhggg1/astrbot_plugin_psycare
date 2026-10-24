const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);

const state = {
  data: null,
  groupId: null,
  busy: false,
};

const LEVEL_CLASS = {
  crisis: "high",
  high: "high",
  watch: "watch",
  mild: "watch",
  stable: "stable",
};

function unwrap(res) {
  // bridge 通常会把 {status:"ok", data} 解包，不同版本行为不完全一致，这里兜一层
  if (
    res &&
    typeof res === "object" &&
    res.data !== undefined &&
    (res.status === "ok" || res.code === 200)
  ) {
    return res.data;
  }
  return res;
}

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.remove("hidden");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => el.classList.add("hidden"), 2600);
}

function fmtTime(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function setStatus(text, ok) {
  const el = $("status");
  el.classList.toggle("off", !ok);
  el.textContent = "●";
  if (text) $("subtitle").textContent = text;
}

async function refresh() {
  try {
    state.data = unwrap(await bridge.apiGet("overview"));
    const groups = state.data.groups || [];
    if (!state.groupId && groups.length) state.groupId = groups[0].id;
    render();
    setStatus(
      `已绑定接收人：${state.data.alert_bound ? "是" : "否"} · 最近更新 ${fmtTime(state.data.generated_at)}`,
      true,
    );
  } catch (error) {
    setStatus(`加载失败：${error.message}`, false);
    toast(`加载失败：${error.message}`);
  }
}

function render() {
  const data = state.data;
  if (!data) return;

  const totals = data.totals || {};
  $("stats").replaceChildren(
    ...[
      ["群聊", totals.groups],
      ["成员", totals.members],
      ["今日消息", totals.today_messages],
      ["需关注", totals.risk_total],
      ["日报存档", totals.daily_reports],
    ].map(([label, value]) => {
      const box = document.createElement("div");
      box.className = "stat";
      const b = document.createElement("b");
      b.textContent = value ?? 0;
      const span = document.createElement("span");
      span.textContent = label;
      box.append(b, span);
      return box;
    }),
  );

  const list = $("groups");
  const groups = data.groups || [];
  if (!groups.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "还没有记录任何群。";
    list.replaceChildren(empty);
    return;
  }

  list.replaceChildren(
    ...groups.map((group) => {
      const item = document.createElement("div");
      item.className = "group-item" + (group.id === state.groupId ? " active" : "");
      item.addEventListener("click", () => {
        state.groupId = group.id;
        render();
      });

      const name = document.createElement("div");
      name.className = "name";
      const title = document.createElement("span");
      title.textContent = group.name;
      const dot = document.createElement("span");
      dot.className = "dot " + (group.enabled && group.activated ? "on" : "off");
      name.append(title, dot);

      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = `今日 ${group.today_messages} 条 · ${group.member_total} 人 · 需关注 ${group.risk_total}`;

      item.append(name, meta);
      return item;
    }),
  );

  renderGroup(groups.find((g) => g.id === state.groupId) || groups[0]);
}

function renderGroup(group) {
  if (!group) return;
  $("group-name").textContent = group.name;
  $("group-meta").textContent =
    `群号 ${group.id} · ${group.enabled ? "记录中" : "已关闭"}` +
    `${group.activated ? "" : " · 未激活"}`;

  const toggle = $("toggle");
  toggle.disabled = false;
  toggle.textContent = group.enabled ? "关闭记录" : "开启记录";
  toggle.onclick = () => toggleGroup(group);

  const daily = $("daily");
  daily.disabled = false;
  daily.onclick = () => generateDaily(group);

  const last = group.last_daily;
  const box = $("last-daily");
  if (last) {
    box.classList.remove("hidden");
    const mood = last.mood || {};
    box.replaceChildren();
    const line = document.createElement("div");
    const title = document.createElement("b");
    title.textContent = `${last.date} 的日报`;
    line.append(title);
    line.append(` · ${mood.label || "未标注"} · ${last.messages || 0} 条`);
    const view = document.createElement("button");
    view.className = "link";
    view.textContent = "查看全文";
    view.onclick = () => {
      $("report").textContent = last.private || last.public || "（无内容）";
    };
    line.append("　", view);
    box.append(line);
  } else {
    box.classList.add("hidden");
  }

  const tbody = $("members");
  const members = group.members || [];
  $("empty").classList.toggle("hidden", members.length > 0);
  tbody.replaceChildren(
    ...members.map((member) => {
      const tr = document.createElement("tr");

      const name = document.createElement("td");
      name.textContent = member.nick;
      const sub = document.createElement("div");
      sub.className = "muted";
      sub.textContent = member.uid;
      name.append(sub);

      const score = document.createElement("td");
      score.className = "num";
      score.textContent = member.score;

      const level = document.createElement("td");
      const pill = document.createElement("span");
      pill.className = "pill " + (LEVEL_CLASS[member.level] || "stable");
      pill.textContent = member.level_label;
      level.append(pill);

      const msgs = document.createElement("td");
      msgs.className = "num";
      msgs.textContent = member.messages;

      const today = document.createElement("td");
      today.className = "num";
      today.textContent = member.today;

      const action = document.createElement("td");
      const view = document.createElement("button");
      view.className = "link";
      view.textContent = "详情";
      view.onclick = () => loadReport(group.id, member.uid);
      action.append(view);

      tr.append(name, score, level, msgs, today, action);
      return tr;
    }),
  );
}

async function loadReport(groupId, uid) {
  const pre = $("report");
  pre.textContent = "生成中…";
  try {
    const data = unwrap(await bridge.apiGet("report", { group_id: groupId, uid }));
    pre.textContent = data.text;
  } catch (error) {
    pre.textContent = `生成失败：${error.message}`;
    toast(error.message);
  }
}

async function generateDaily(group) {
  if (state.busy) return;
  state.busy = true;
  const pre = $("report");
  pre.textContent = "正在分析今天的聊天记录…";
  try {
    const data = unwrap(await bridge.apiPost("daily", { group_id: group.id }));
    pre.textContent = data.private || data.public || "（无内容）";
    toast("日报已生成");
    await refresh();
  } catch (error) {
    pre.textContent = `生成失败：${error.message}`;
    toast(error.message);
  } finally {
    state.busy = false;
  }
}

async function toggleGroup(group) {
  if (state.busy) return;
  state.busy = true;
  try {
    await bridge.apiPost("toggle", { group_id: group.id, enabled: !group.enabled });
    toast(group.enabled ? "已关闭本群记录" : "已开启本群记录");
    await refresh();
  } catch (error) {
    toast(error.message);
  } finally {
    state.busy = false;
  }
}

$("refresh").addEventListener("click", () => refresh());
$("copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("report").textContent || "");
    toast("已复制");
  } catch {
    toast("复制失败，请手动选择");
  }
});

async function boot() {
  if (!bridge) {
    setStatus("bridge SDK 未加载：请从 AstrBot 插件详情页打开本视图", false);
    return;
  }
  try {
    const context = await bridge.ready();
    if (context && context.displayName) {
      $("subtitle").textContent = context.displayName;
    }
  } catch (error) {
    setStatus(`bridge 初始化失败：${error.message}`, false);
    return;
  }
  await refresh();
}

boot();
