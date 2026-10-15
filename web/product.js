"use strict";

const portraitFiles = [
  "caocao",
  "liubei",
  "sunquan",
  "zhugeliang",
  "diaochan",
  "guanyu",
  "simayi",
  "zhouyu",
  "zhaoyun",
];
const traits = [
  "审慎多疑",
  "温和结盟",
  "权衡局势",
  "缜密推演",
  "敏锐试探",
  "直率坚定",
  "隐忍布局",
  "主动控场",
  "稳健求证",
];
const kindNames = {
  talk: "首轮发言",
  rebuttal: "回应质疑",
  pk_talk: "平票辩论",
  last_words: "遗言",
  wolf_chat: "狼队协商",
  wolf_vote: "狼队刀口",
  seer: "查验",
  witch: "用药",
  vote: "放逐投票",
  pk_vote: "平票复投",
  hunter: "猎人行动",
};
const phaseNames = {
  night: "夜间",
  day: "白天",
  vote: "投票",
  finished: "终局",
  opening: "入席",
};
let currentPage = "home",
  pageEpoch = 0,
  historyData = [],
  historyFilter = "all";
let reviewTab = "decision",
  replayData = null,
  replayId = "",
  replayPerspective = "public";
let replayCursor = null,
  replayPlaying = false,
  replayTimer,
  replayRequest = 0,
  replayFilter = "all",
  replayTrace = "";

function memorySearchHtml(trace) {
  const key = `${currentPage === "replay" ? replayId : game.id}:${trace.actor}:${trace.event_id}`;
  const result = memoryResult?.key === key ? memoryResult : null;
  return `<div class="memory-search"><h4>回查当时的原文</h4><label class="sr-only" for="memory-query">记忆关键词</label><input id="memory-query" placeholder="人名、身份或原话；留空查全部" maxlength="100" value="${escapeHtml(memoryQuery)}"><div class="memory-search-row"><label class="sr-only" for="memory-day">记忆轮次</label><select id="memory-day"><option value="0">全部轮次</option>${Array.from({ length: trace.day }, (_, i) => `<option value="${i + 1}" ${memoryDay === String(i + 1) ? "selected" : ""}>第 ${i + 1} 轮</option>`).join("")}</select><button class="button secondary" id="search-memory">查原文</button></div><p class="security-note">仅检索这次决策之前，这个角色实际收到的记录。</p>${result ? `<p class="security-note">${result.total} 条档案 · 匹配 ${result.matched} 条${result.limited ? " · 先显示前100条" : ""}</p>${result.records.map((r) => `<details class="candidate"><summary>#${r.id} · 第${r.day}轮 · ${escapeHtml(r.speaker)}</summary><p>${escapeHtml(r.text)}</p></details>`).join("")}` : ""}</div>`;
}
function bindMemorySearch(trace, render) {
  if (!trace || !$("#search-memory")) return;
  $("#memory-query").oninput = (e) => (memoryQuery = e.target.value);
  $("#memory-day").onchange = (e) => (memoryDay = e.target.value);
  $("#search-memory").onclick = async () => {
    const key = `${currentPage === "replay" ? replayId : game.id}:${trace.actor}:${trace.event_id}`;
    const params = `q=${encodeURIComponent(memoryQuery)}&day=${Number(memoryDay)}`;
    const id = currentPage === "replay" ? replayId : game.id;
    const path = `history/${encodeURIComponent(id)}/memory/${trace.actor}?cursor=${Math.max(0, trace.event_id - 1)}&${params}`;
    $("#search-memory").disabled = true;
    try {
      memoryResult = { ...(await api(path)), key };
      render();
    } catch (e) {
      toast(e.message);
      $("#search-memory")?.removeAttribute("disabled");
    }
  };
}

function avatar(seat, size = "", alt = "") {
  const file = portraitFiles[Number(seat) - 1];
  return file
    ? `<img class="portrait ${size}" src="/static/avatars/${file}.png" alt="${escapeHtml(alt)}" width="96" height="96" loading="lazy">`
    : "";
}
function voteChipHtml(event, voter, target) {
  const traces =
    currentPage === "replay"
      ? replayData?.traces || []
      : game?.debug_traces || [];
  const trace = traces.findLast(
    (t) =>
      t.actor === Number(voter) &&
      t.day === event.day &&
      t.event_id < event.id &&
      t.kind === (event.data.round ? "pk_vote" : "vote"),
  );
  const label = `${voter} → ${target || "弃票"}`;
  return trace
    ? `<button class="vote-chip vote-inspect" data-vote-trace="${trace.id}" aria-label="查看${voter}号这张选票的决策">${label} ◎</button>`
    : `<span class="vote-chip">${label}</span>`;
}
function markRoute(page) {
  currentPage = page;
  showingLobby = page === "new";
  document.querySelectorAll(".nav-link").forEach((b) => {
    const active =
      b.id ===
      `nav-${page === "new" ? "game" : page === "replay" ? "history" : page}`;
    b.classList.toggle("active", active);
    active
      ? b.setAttribute("aria-current", "page")
      : b.removeAttribute("aria-current");
  });
  const hash =
    page === "replay" ? `#replay/${encodeURIComponent(replayId)}` : `#${page}`;
  if (location.hash !== hash) history.pushState(null, "", hash);
}
function stopReplay() {
  replayPlaying = false;
  clearTimeout(replayTimer);
}
async function refreshPageAfterTurn() {
  const page = currentPage,
    epoch = pageEpoch;
  if (page === "new") return renderLobby();
  if (page !== "home" && page !== "history") return renderGame();
  try {
    const result = await api("history");
    if (epoch !== pageEpoch) return;
    historyData = result.games;
  } catch {
    if (epoch !== pageEpoch) return;
  }
  page === "home" ? renderHome() : renderHistory();
}
async function navigate(page, id = "") {
  stopAuto();
  stopReplay();
  const token = ++pageEpoch;
  if (page === "game" && !game) page = "new";
  if (page === "replay") replayId = id;
  markRoute(page);
  window.scrollTo(0, 0);
  if (page === "game") return renderGame();
  if (page === "new") return renderLobby();
  $("#app").innerHTML =
    '<div class="loading-page"><span class="spinner"></span> 正在打开记录…</div>';
  try {
    if (page === "replay") {
      replayCursor = null;
      replayTrace = "";
      replayPerspective = "public";
      replayFilter = "all";
      await loadReplay();
    } else {
      const result = await api("history");
      if (token !== pageEpoch) return;
      historyData = result.games;
      page === "history" ? renderHistory() : renderHome();
    }
  } catch (e) {
    if (token !== pageEpoch) return;
    $("#app").innerHTML =
      `<section class="empty-state"><span>◇</span><h1>记录暂时未能打开</h1><p>${escapeHtml(e.message)}</p><button class="button secondary" id="retry-page">重新加载</button></section>`;
    $("#retry-page").onclick = () => navigate(page, id);
  }
}
function dateText(time) {
  return new Date(time * 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
function resultText(g) {
  return g.winner === "good"
    ? "好人获胜"
    : g.winner === "wolf"
      ? "狼人获胜"
      : g.winner
        ? "平局"
        : "进行中";
}
function matchCard(g) {
  return `<article class="match-card"><div class="match-top"><span class="badge ${g.winner ? "" : "gold"}">${resultText(g)}</span><small>${dateText(g.updated)}</small></div><div class="match-title"><div class="match-icon">${g.human ? avatar(g.human) : "<span>观</span>"}</div><div><h3>${g.size} 人 · ${g.size === 9 ? "预女猎" : "初识局"}</h3><p>${g.human ? `真人入局 · ${roleNames[g.role]}` : "AI 观战"} · ${g.mode === "llm" ? "真实大模型" : "练习模式"}${g.debug_mode ? " · 调试局" : ""}</p></div></div><div class="match-meta"><span>第 ${g.day} 轮</span><span>${g.events} 条公开记录</span><span class="match-id">#${escapeHtml(g.id.slice(0, 6))}</span></div><div class="match-actions"><button class="button ${g.winner ? "primary" : "secondary"}" data-replay="${escapeHtml(g.id)}">${g.winner ? "进入复盘" : "回看进程"} ↗</button>${!g.winner ? `<button class="button primary" data-resume="${escapeHtml(g.id)}" ${busy ? "disabled" : ""}>继续对局 →</button>` : ""}</div></article>`;
}
function bindMatchActions() {
  document
    .querySelectorAll("[data-replay]")
    .forEach((b) => (b.onclick = () => navigate("replay", b.dataset.replay)));
  document.querySelectorAll("[data-resume]").forEach(
    (b) =>
      (b.onclick = async () => {
        if (busy) return toast("当前行动完成后即可切换对局。");
        b.disabled = true;
        try {
          setGame(await api(`history/${b.dataset.resume}/resume`, {}));
          navigate("game");
        } catch (e) {
          toast(e.message);
          b.disabled = false;
        }
      }),
  );
  document
    .querySelectorAll("[data-new]")
    .forEach((b) => (b.onclick = () => navigate("new")));
}
function renderHome() {
  const unfinished = historyData.filter((g) => !g.winner),
    completed = historyData.filter((g) => g.winner);
  const continuation =
    unfinished.find((g) => g.id === game?.id) || unfinished[0];
  $("#app").innerHTML =
    `<section class="home-page page-wrap"><div class="home-hero"><div class="home-copy"><span class="eyebrow">NIGHT COUNCIL · 三国夜议</span><div class="hero-kicker"><i class="live-dot"></i> 与九种人格，同赴一场暗局</div><h1>各怀其心，<br><em>由你识破。</em></h1><p>一句证词，一场试探。<br>在三国群雄的谎言与沉默之间，找到你的答案。</p><div class="hero-buttons"><button class="button primary large" data-new>开启新对局 <span>↗</span></button><button class="button secondary large" id="home-rules">了解玩法</button></div><div class="hero-footnote"><span>独立人格</span><span>私有记忆</span><span>逐步复盘</span></div></div><div class="portrait-stage" aria-label="曹操、诸葛亮、貂蝉三国人物插画"><div class="stage-halo"></div>${[1, 4, 5].map((s, i) => `<div class="hero-portrait hp-${i}">${avatar(s, "", names[s - 1])}<div><small>${traits[s - 1]}</small><b>${names[s - 1]}</b></div></div>`).join("")}<span class="stage-caption">人物是性格，身份是秘密。</span></div></div><div class="home-overview"><div class="continue-card"><div><span class="eyebrow">${continuation ? "YOUR UNFINISHED STORY" : "YOUR NEXT STORY"}</span><h2>${continuation ? "夜议未散，等你归席。" : "今夜，留一个席位给你。"}</h2><p>${continuation ? `${continuation.size} 人局 · 第 ${continuation.day} 轮 · ${phaseNames[continuation.phase]} · ${dateText(continuation.updated)} 保存` : historyData.length ? "上一场夜议已落幕。开启新的棋局，或在历史对局回看关键判断。" : "第一次入局？可选择无需密钥的练习模式，熟悉身份与行动。"}</p></div><button class="button primary" ${continuation ? `data-resume="${continuation.id}"` : "data-new"}>${continuation ? "继续对局 →" : "选择棋局 →"}</button></div><div class="stat-card"><strong>${historyData.length}</strong><span>累计对局</span></div><div class="stat-card"><strong>${completed.length}</strong><span>已完成</span></div></div><div class="section-heading"><div><span class="eyebrow">RECENT COUNCILS</span><h2>最近的故事</h2></div><button class="text-button" id="all-history">全部历史 ↗</button></div>${historyData.length ? `<div class="match-grid">${historyData.slice(0, 3).map(matchCard).join("")}</div>` : '<div class="quiet-empty">还没有对局。开始后，每次发言与行动都会自动归档。</div>'}<div class="section-heading"><div><span class="eyebrow">NINE MINDS, NINE VOICES</span><h2>席上群雄</h2></div><p>性格固定 · 身份随机</p></div><div class="persona-gallery">${names.map((n, i) => `<div class="persona-tile">${avatar(i + 1, "", n)}<b>${n}</b><span>${traits[i]}</span></div>`).join("")}</div></section>`;
  bindMatchActions();
  $("#home-rules").onclick = () => openDialog("#rules-dialog");
  $("#all-history").onclick = () => navigate("history");
}
function renderHistory() {
  const list = historyData.filter(
    (g) =>
      historyFilter === "all" ||
      (historyFilter === "finished" ? g.winner : !g.winner),
  );
  $("#app").innerHTML =
    `<section class="page-wrap"><div class="page-heading"><div><span class="eyebrow">THE ARCHIVE</span><h1>每一局，都有迹可循。</h1><p>继续未完的对局，或回到那张改变局势的选票之前。</p></div><button class="button primary" data-new>＋ 开启新对局</button></div><div class="archive-bar"><div class="pill-tabs">${[
      ["all", "全部对局"],
      ["active", "进行中"],
      ["finished", "已结束"],
    ]
      .map(
        ([v, t]) =>
          `<button data-filter="${v}" class="${historyFilter === v ? "active" : ""}">${t} <small>${historyData.filter((g) => v === "all" || (v === "finished" ? g.winner : !g.winner)).length}</small></button>`,
      )
      .join(
        "",
      )}</div><small>按最近保存排序 · 当前浏览器的本地档案</small></div>${list.length ? `<div class="match-grid archive-grid">${list.map(matchCard).join("")}</div>` : '<div class="empty-state"><span>◇</span><h2>这一页还没有故事</h2><p>切换筛选，或开启一场新的夜议。</p><button class="button secondary" data-new>开启新对局</button></div>'}<p class="archive-note">每步自动保存，开启新局会保留旧局。历史记录属于当前浏览器会话；清除 Cookie 或更换浏览器后无法直接找回，请及时下载重要复盘。</p></section>`;
  bindMatchActions();
  document.querySelectorAll("[data-filter]").forEach(
    (b) =>
      (b.onclick = () => {
        historyFilter = b.dataset.filter;
        renderHistory();
      }),
  );
}

function traceBody(trace, players, evidence = []) {
  if (!trace)
    return '<div class="observer-empty"><span>◎</span><h3>这一刻，还没有决策记录</h3><p>选择一个 AI 行动，查看它当时的判断、证据和私有记忆。</p></div>';
  const p = players.find((p) => p.id === trace.actor),
    a = trace.assessment,
    vote = a?.vote_review;
  const decision = trace.action || {};
  const requests = trace.requests || [];
  const reported = requests.filter((r) => Number.isFinite(r.reasoning_tokens));
  const reasoningUsage = reported.length
    ? `供应商报告推理 ${reported.reduce((n, r) => n + r.reasoning_tokens, 0)} tokens`
    : "供应商未报告推理 token 数";
  const thinkingMode = requests.some((r) => r.native_thinking)
    ? "已请求供应商思考模式"
    : "未请求供应商原生思考";
  let content = "";
  if (reviewTab === "evidence") {
    content = `<h4>这次决策引用了什么</h4>${evidence.length ? evidence.map((e) => `<div class="evidence-card"><button class="text-button" data-evidence="${e.id}">#${e.id} · 第 ${e.day} 轮 · ${escapeHtml(e.title)} ↗</button><p>${escapeHtml(e.text)}</p></div>`).join("") : '<p class="muted">这条决策没有可展示的原文引用。摘要代表角色判断，不等同于已核实的事实。</p>'}`;
  } else if (reviewTab === "memory") {
    const n = trace.notebook || {};
    content = `<h4>当时的私有策略账本</h4><p class="muted">保留在这次行动时的版本，不显示后续更新。</p><div class="thought-block"><small>身份说辞 / 伪装</small><p>${escapeHtml(n.cover_story || "尚未形成记录")}</p></div><div class="thought-block"><small>后续打算</small><p>${escapeHtml(n.plan || "尚未形成记录")}</p></div>${Object.entries(
      n.beliefs || {},
    )
      .map(
        ([id, b]) =>
          `<div class="belief-row"><b>${escapeHtml(id)}号</b><p>${escapeHtml(b)}</p></div>`,
      )
      .join(
        "",
      )}<div class="trace-telemetry">档案 ${trace.memory?.archive_records ?? "—"} 条 · 本次原文 ${trace.memory?.exact_records ?? "—"} 条 · 主动回查 ${trace.memory?.retrieved_ids?.length ?? 0} 条</div>`;
  } else if (reviewTab === "vote") {
    content = vote
      ? `<div class="thought-block"><small>为何投这一票</small><p>${escapeHtml(vote.decision_reason)}</p></div><h4>逐人比较 · 包含弃票</h4>${vote.candidates.map((c) => `<details class="candidate"><summary>${c.target === null ? "弃票" : c.target + "号 · " + (players.find((p) => p.id === c.target)?.name || "")}</summary><p><span class="gold">支持依据</span><br>${escapeHtml(c.case_for)}</p><p><span class="muted">反证与代价</span><br>${escapeHtml(c.case_against)}</p></details>`).join("")}<div class="thought-block"><small>何时会改票</small><p>${escapeHtml(vote.change_condition)}</p></div><details class="candidate"><summary>来源核验与最新辩护</summary><p>${escapeHtml(vote.source_audit)}</p><p>${escapeHtml(vote.latest_defense)}</p></details>`
      : `<div class="thought-block primary-thought"><small>这次行动的判断摘要</small><p>${escapeHtml(trace.summary || "未记录判断摘要")}</p></div>${trace.intent ? `<div class="thought-block"><small>行动意图</small><p>${escapeHtml(trace.intent)}</p></div>` : ""}<p class="muted">这条记录没有逐候选人的投票复核。深度博弈的投票行动会记录支持理由、反证和改票条件；练习模式及旧存档可能没有这些字段。</p>`;
  } else {
    content = `<div class="thought-block primary-thought"><small>判断结论</small><p>${escapeHtml(trace.summary || "未记录摘要")}</p></div>${a?.revision ? `<div class="thought-block"><small>相比之前，改变了什么</small><p>${escapeHtml(a.revision)}</p></div>` : ""}${trace.intent ? `<div class="thought-block"><small>行动意图</small><p>${escapeHtml(trace.intent)}</p></div>` : ""}${a?.suspicions?.length ? `<h4>角色眼中的嫌疑分布 <small>主观估计</small></h4><div class="suspicion-list">${a.suspicions.map((s) => `<div class="suspicion-row"><span>${s.seat}号 ${escapeHtml(players.find((p) => p.id === s.seat)?.name || "")}</span><div class="probability"><i style="width:${Math.max(0, Math.min(100, Math.round(s.wolf_probability * 100)))}%"></i></div><b>${Math.round(s.wolf_probability * 100)}%</b><p>${escapeHtml(s.note)}</p></div>`).join("")}</div>` : ""}${a?.alternatives?.length ? `<details class="candidate"><summary>其他解释与不确定性</summary>${a.alternatives.map((x) => `<p>${escapeHtml(x.hypothesis)}<br><small>${escapeHtml(x.weakness)}</small></p>`).join("")}</details>` : ""}`;
  }
  if (reviewTab === "memory") content += memorySearchHtml(trace);
  return `<div class="observer-person">${avatar(trace.actor)}<div><b>${trace.actor}号 · ${escapeHtml(p?.name)}</b><span>${escapeHtml(p?.trait || "")} · ${kindNames[trace.kind] || "行动"}</span></div><span class="badge">第 ${trace.day} 轮</span></div><div class="decision-chip"><span>${actionNames[decision.action] || "行动"}${decision.target ? ` → ${decision.target}号` : ""}</span><small>${trace.decision_mode === "demo" || trace.model === "练习策略" ? "练习规则策略" : trace.decision_mode === "deep" ? "深度博弈" : "模型决策"}</small></div><div class="observer-tabs" role="tablist" aria-label="观察内容">${[
    ["decision", "判断"],
    ["vote", "投票复核"],
    ["evidence", "证据"],
    ["memory", "记忆"],
  ]
    .map(
      ([v, t]) =>
        `<button role="tab" aria-selected="${reviewTab === v}" data-review-tab="${v}" class="${reviewTab === v ? "active" : ""}">${t}${v === "evidence" ? ` <small>${evidence.length}</small>` : ""}</button>`,
    )
    .join(
      "",
    )}</div><div class="observer-content" role="tabpanel">${content}</div><details class="trace-details"><summary>模型与运行记录</summary><p>${escapeHtml(trace.model)} · ${trace.duration_seconds ?? "—"} 秒 · ${trace.requests?.length ?? 0} 次请求</p><p>${thinkingMode} · ${reasoningUsage}</p><p>这里展示可核对的决策摘要，不展示模型原始思维链。私有记录仅供观察，不会传给其他 AI。</p></details>`;
}
function liveReviewPanel() {
  if (!game.debug_available)
    return `<section class="panel debug-panel"><div class="panel-header"><h2>AI 观察室</h2><span class="private-tag">私密</span></div><div class="debug-body"><p>终局后可查看每个角色如何判断、伪装与改票。</p><p class="muted">当前保持身份迷雾。调试局会揭开全部身份。</p><button class="button secondary full" id="open-debug-gate" ${busy ? "disabled" : ""}>开启调试局</button></div></section>`;
  const list = (game.debug_traces || []).filter(
    (t) => debugSeat === "latest" || t.actor === Number(debugSeat),
  );
  const trace = list.find((t) => String(t.id) === debugTraceId) || list.at(-1);
  const evidence = (trace?.evidence_ids || [])
    .map((id) => game.events.find((e) => e.id === id && e.id < trace.event_id))
    .filter(Boolean);
  return `<section class="panel debug-panel"><div class="panel-header"><h2>AI 观察室</h2><button class="text-button" id="toggle-debug" aria-expanded="${debugOpen}">${debugOpen ? "收起" : "展开"}</button></div>${
    debugOpen
      ? `<div class="debug-body"><div class="observer-selects"><label>观察角色<select id="debug-seat"><option value="latest">跟随最新行动</option>${game.players
          .filter((p) => p.id !== game.human)
          .map(
            (p) =>
              `<option value="${p.id}" ${debugSeat === String(p.id) ? "selected" : ""}>${p.id}号 · ${p.name}</option>`,
          )
          .join(
            "",
          )}</select></label><label>决策时刻<select id="debug-trace"><option value="">最新行动</option>${list
          .toReversed()
          .map(
            (t) =>
              `<option value="${t.id}" ${debugTraceId === String(t.id) ? "selected" : ""}>第${t.day}轮 · ${kindNames[t.kind] || t.kind} · #${t.event_id}</option>`,
          )
          .join(
            "",
          )}</select></label></div>${traceBody(trace, game.players, evidence)}</div>`
      : '<div class="debug-body observer-intro"><span>◎</span><p>从一句发言，回到它背后的判断。<br><small>点击发言旁的「观察决策」可直接关联。</small></p></div>'
  }</section>`;
}
function bindReviewTabs(render, evidenceClick) {
  document.querySelectorAll("[data-review-tab]").forEach(
    (b) =>
      (b.onclick = () => {
        reviewTab = b.dataset.reviewTab;
        render();
      }),
  );
  document
    .querySelectorAll("[data-evidence]")
    .forEach(
      (b) => (b.onclick = () => evidenceClick(Number(b.dataset.evidence))),
    );
}
function jumpLiveEvidence(id) {
  tab = "all";
  renderGame();
  const event = document.querySelector(`[data-event-id="${id}"]`);
  event?.scrollIntoView({ behavior: "smooth", block: "center" });
  event?.classList.add("highlighted-event");
}
function bindLiveReview() {
  $("#open-debug-gate")?.addEventListener("click", () => {
    stopAuto();
    openDialog("#debug-dialog");
  });
  $("#toggle-debug")?.addEventListener("click", () => {
    debugOpen = !debugOpen;
    renderGame();
  });
  $("#debug-seat")?.addEventListener("change", (e) => {
    debugSeat = e.target.value;
    debugTraceId = "";
    renderGame();
  });
  $("#debug-trace")?.addEventListener("change", (e) => {
    debugTraceId = e.target.value;
    renderGame();
  });
  bindReviewTabs(renderGame, jumpLiveEvidence);
  const list = (game.debug_traces || []).filter(
    (t) => debugSeat === "latest" || t.actor === Number(debugSeat),
  );
  bindMemorySearch(
    list.find((t) => String(t.id) === debugTraceId) || list.at(-1),
    renderGame,
  );
  document.querySelectorAll("[data-vote-trace]").forEach(
    (b) =>
      (b.onclick = () => {
        const trace = game.debug_traces.find(
          (t) => String(t.id) === b.dataset.voteTrace,
        );
        if (!trace) return;
        debugOpen = true;
        debugSeat = String(trace.actor);
        debugTraceId = String(trace.id);
        reviewTab = "vote";
        renderGame();
        $(".debug-panel").scrollIntoView({
          behavior: "smooth",
          block: "nearest",
        });
      }),
  );
  document.querySelectorAll("[data-observe]").forEach(
    (b) =>
      (b.onclick = () => {
        const trace = game.debug_traces.find(
          (t) => t.event_id === Number(b.dataset.observe),
        );
        if (!trace) return;
        debugOpen = true;
        debugSeat = String(trace.actor);
        debugTraceId = String(trace.id);
        reviewTab = "decision";
        renderGame();
        $(".debug-panel").scrollIntoView({
          behavior: "smooth",
          block: "nearest",
        });
      }),
  );
}

async function loadReplay(cursor = replayCursor) {
  const token = ++replayRequest,
    epoch = pageEpoch,
    id = replayId,
    perspective = replayPerspective;
  const data = await api(
    `history/${encodeURIComponent(id)}/replay?perspective=${encodeURIComponent(perspective)}${cursor === null ? "" : `&cursor=${cursor}`}`,
  );
  if (
    token !== replayRequest ||
    epoch !== pageEpoch ||
    currentPage !== "replay"
  )
    return;
  replayData = data;
  replayCursor = data.cursor;
  renderReplay(true);
}
async function seekReplay(cursor, stop = true) {
  if (stop) stopReplay();
  replayTrace = "";
  try {
    await loadReplay(cursor);
  } catch (e) {
    stopReplay();
    toast(e.message);
  }
}
function filteredTimeline() {
  return (replayData?.timeline || []).filter(
    (e) =>
      replayFilter === "all" ||
      (replayFilter === "speech"
        ? e.kind === "speech"
        : replayFilter === "ballot"
          ? ["ballot", "elimination", "result"].includes(e.kind)
          : e.phase === "night"),
  );
}
function adjacentReplay(direction) {
  const ids = filteredTimeline().map((e) => e.id);
  return direction > 0
    ? ids.find((id) => id > replayCursor)
    : ids.findLast((id) => id < replayCursor);
}
async function playReplay() {
  if (!replayPlaying) return;
  const next = adjacentReplay(1);
  if (next === undefined) {
    stopReplay();
    renderReplay();
    return;
  }
  await seekReplay(next, false);
  if (replayPlaying) replayTimer = setTimeout(playReplay, 2200);
}
function renderReplay(followCursor = false) {
  const r = replayData,
    timeline = filteredTimeline();
  if (!r || currentPage !== "replay") return;
  const priorScroll = $("#replay-feed")?.scrollTop || 0;
  const event = r.events.at(-1),
    traces = r.traces || [];
  const trace =
    traces.find((t) => String(t.id) === replayTrace) ||
    traces.findLast((t) => t.event_id === event?.id) ||
    traces.at(-1);
  const chapters = [];
  for (const e of r.timeline) {
    const key = `${e.day}-${e.phase}`;
    if (chapters.at(-1)?.key !== key) chapters.push({ ...e, key });
  }
  const currentChapter = chapters.findLast((c) => c.id <= r.cursor);
  const shownEvents = r.events.filter(
    (e) => replayFilter === "all" || timeline.some((t) => t.id === e.id),
  );
  $("#app").innerHTML =
    `<section class="replay-page page-wrap"><div class="page-heading"><div><button class="text-button back-link" id="back-history">← 历史对局</button><span class="eyebrow">REVISIT THE COUNCIL</span><h1>回到判断发生的那一刻。</h1><p>${r.players.length} 人局 · ${r.mode === "llm" ? "真实大模型" : "练习模式"} · #${escapeHtml(r.id.slice(0, 6))} <span class="badge">${r.finished ? "已结束" : "进程回看"}</span></p></div><div class="replay-top-actions"><label>信息视角<select id="replay-perspective">${r.perspectives.map((p) => `<option value="${p.value}" ${p.value === r.perspective ? "selected" : ""}>${escapeHtml(p.label)}</option>`).join("")}</select></label><button class="button secondary" id="export-archive">↓ 导出此视角</button></div></div><div class="replay-player"><div class="transport"><button class="icon-button" id="replay-first" aria-label="回到开局">|‹</button><button class="icon-button" id="replay-prev" aria-label="上一条" ${adjacentReplay(-1) === undefined ? "disabled" : ""}>‹</button><button class="play-button" id="replay-play" aria-label="${replayPlaying ? "暂停回放" : "自动回放"}">${replayPlaying ? "Ⅱ" : "▶"}</button><button class="icon-button" id="replay-next" aria-label="下一条" ${adjacentReplay(1) === undefined ? "disabled" : ""}>›</button><button class="icon-button" id="replay-last" aria-label="跳到最后">›|</button></div><label class="replay-progress"><span>第 ${r.day} 轮 · ${phaseNames[r.phase] || r.phase}<b>事件 ${r.cursor} / ${r.max_cursor}</b></span><input id="replay-range" aria-label="复盘时间轴" type="range" min="0" max="${r.max_cursor}" value="${r.cursor}"></label><span class="playback-note">${replayPlaying ? "每 2.2 秒一条" : "拖动时间轴回看"}</span></div><div class="replay-layout"><aside class="panel chapter-panel"><div class="panel-header"><h2>对局时间线</h2></div><div class="chapters">${chapters.map((c) => `<button data-seek="${c.id}" class="chapter ${currentChapter?.key === c.key ? "active" : ""}"><span>${c.phase === "night" ? "☾" : c.phase === "vote" ? "◇" : "☀"}</span><div><b>${c.day ? `第 ${c.day} 轮` : "入席"}</b><small>${c.day ? phaseNames[c.phase] || c.phase : "身份分配"}</small></div></button>`).join("")}</div></aside><section class="replay-middle"><div class="replay-roster">${r.players.map((p) => `<div class="replay-seat ${p.alive ? "" : "dead"}" title="${escapeHtml(p.trait)}">${avatar(p.id)}<b>${p.id} ${p.name}</b><small>${p.role ? roleNames[p.role] : "未知"}${p.alive ? "" : " · 出局"}</small></div>`).join("")}</div><div class="panel replay-story"><div class="feed-header"><div class="pill-tabs">${[
      ["all", "全部"],
      ["speech", "发言"],
      ["ballot", "票型 / 结果"],
      ["night", "夜间"],
    ]
      .map(
        ([v, t]) =>
          `<button data-replay-filter="${v}" class="${replayFilter === v ? "active" : ""}">${t}</button>`,
      )
      .join(
        "",
      )}</div></div><div class="replay-feed" id="replay-feed">${shownEvents.length ? shownEvents.map((e) => `<div class="replay-event ${e.id === event?.id ? "selected" : ""}" data-replay-event="${e.id}">${eventHtml(e, r.players, false)}${traces.some((t) => t.event_id === e.id) ? `<button class="text-button inspect-decision" data-trace-event="${e.id}">◎ 查看这次决策</button>` : ""}</div>`).join("") : '<div class="observer-empty"><h3>这一刻，没有该类记录</h3><p>切换筛选或推进时间轴。</p></div>'}</div></div><p class="replay-perspective-note">${r.perspective === "public" ? "公开视角仅显示桌面信息。切换角色视角，可查看其实际接收的记录和私有判断。" : r.perspective === "all" ? "全知视角会揭示身份与私密行动，供复盘对照。" : "仅显示这个角色收到的消息与自身决策；出局后不会继续接收其他人的发言。"}</p></section><aside class="panel replay-observer"><div class="panel-header"><h2>AI 观察室</h2><span class="private-tag">独立视角</span></div><div class="debug-body">${
      r.perspective === "public"
        ? '<div class="observer-empty"><span>◎</span><h3>看见表象，也理解判断。</h3><p>在上方切换到某个角色或全知视角，再点击发言的「查看这次决策」。</p><p class="security-note">未结束的真人局只开放你的视角。</p></div>'
        : `${
            traces.length
              ? `<label>截至当前的决策<select id="replay-trace"><option value="">跟随当前 / 最近决策</option>${traces
                  .toReversed()
                  .map(
                    (t) =>
                      `<option value="${t.id}" ${replayTrace === String(t.id) ? "selected" : ""}>${t.actor}号 · 第${t.day}轮 · ${kindNames[t.kind] || t.kind} · #${t.event_id}</option>`,
                  )
                  .join(
                    "",
                  )}</select></label><p class="observer-context">${replayTrace ? "选中的历史决策" : trace?.event_id === event?.id ? "当前事件的决策" : "最近一条可见决策"} · 事件 #${trace?.event_id}</p>`
              : ""
          }${traceBody(trace, r.players, trace?.evidence || [])}`
    }</div></aside></div></section>`;
  $("#back-history").onclick = () => navigate("history");
  $("#replay-perspective").onchange = (e) => {
    stopReplay();
    replayPerspective = e.target.value;
    replayTrace = "";
    seekReplay(replayCursor);
  };
  $("#replay-range").onchange = (e) => seekReplay(Number(e.target.value));
  $("#replay-first").onclick = () => seekReplay(0);
  $("#replay-last").onclick = () => seekReplay(r.max_cursor);
  $("#replay-prev").onclick = () => seekReplay(adjacentReplay(-1));
  $("#replay-next").onclick = () => seekReplay(adjacentReplay(1));
  $("#replay-play").onclick = () => {
    if (replayPlaying) {
      stopReplay();
      renderReplay();
    } else {
      replayPlaying = true;
      if (adjacentReplay(1) === undefined) {
        seekReplay(0, false).then(playReplay);
      } else playReplay();
    }
  };
  $("#replay-trace")?.addEventListener("change", (e) => {
    replayTrace = e.target.value;
    renderReplay();
  });
  document
    .querySelectorAll("[data-seek]")
    .forEach((b) => (b.onclick = () => seekReplay(Number(b.dataset.seek))));
  document.querySelectorAll("[data-trace-event]").forEach(
    (b) =>
      (b.onclick = async () => {
        reviewTab = "decision";
        await seekReplay(Number(b.dataset.traceEvent));
        $(".replay-observer").scrollIntoView({
          behavior: "smooth",
          block: "nearest",
        });
      }),
  );
  document.querySelectorAll("[data-replay-filter]").forEach(
    (b) =>
      (b.onclick = () => {
        stopReplay();
        replayFilter = b.dataset.replayFilter;
        renderReplay();
      }),
  );
  bindReviewTabs(renderReplay, (id) => seekReplay(id));
  bindMemorySearch(trace, renderReplay);
  $("#export-archive").onclick = exportArchive;
  document.querySelectorAll("[data-vote-trace]").forEach(
    (b) =>
      (b.onclick = () => {
        replayTrace = b.dataset.voteTrace;
        reviewTab = "vote";
        renderReplay();
        $(".replay-observer").scrollIntoView({
          behavior: "smooth",
          block: "nearest",
        });
      }),
  );
  $("#replay-feed").scrollTop = followCursor
    ? $("#replay-feed").scrollHeight
    : priorScroll;
}
async function exportArchive() {
  try {
    const data = await api(
      `history/${encodeURIComponent(replayId)}/replay?perspective=${encodeURIComponent(replayPerspective)}`,
    );
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `夜议-${replayId}-${replayPerspective}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    toast(e.message);
  }
}
function routeFromLocation() {
  const [page, id] = (location.hash.slice(1) || "home").split("/");
  navigate(
    ["home", "new", "game", "history", "replay"].includes(page) ? page : "home",
    id || "",
  );
}
$("#nav-home").onclick = () => navigate("home");
$("#nav-game").onclick = () => navigate(game ? "game" : "new");
$("#nav-history").onclick = () => navigate("history");
$(".brand").onclick = (e) => {
  e.preventDefault();
  navigate("home");
};
window.addEventListener("popstate", routeFromLocation);
init();
