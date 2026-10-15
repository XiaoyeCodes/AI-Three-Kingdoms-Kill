"use strict";

const $ = (s, root = document) => root.querySelector(s);
const escapeHtml = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const roleNames = {
  wolf: "狼人",
  villager: "平民",
  seer: "预言家",
  witch: "女巫",
  hunter: "猎人",
};
const roleSymbols = {
  wolf: "狼",
  villager: "民",
  seer: "鉴",
  witch: "药",
  hunter: "弓",
};
const roleDescriptions = {
  wolf: "潜伏在好人之中。夜里与同伴商议刀口，白天用可信的故事隐藏自己。",
  villager:
    "没有夜间技能，你的武器是推理。听取证词、追踪票型，找出暗处的狼人。",
  seer: "每夜查验一位玩家的阵营。你的情报能照亮迷雾，但何时亮明身份，由你决定。",
  witch:
    "手握一瓶解药、一瓶毒药，每夜至多用一瓶。仅首夜可自救，解药用尽不再得知刀口。",
  hunter: "被狼人杀害或被放逐后，可以开枪带走一位存活玩家。中毒无法开枪。",
};
const actionNames = {
  speak: "发言",
  kill: "选择刀口",
  check: "查验阵营",
  save: "使用解药",
  poison: "使用毒药",
  pass: "不使用技能",
  vote: "投票放逐",
  shoot: "开枪",
  abstain: "弃票",
};
const names = [
  "曹操",
  "刘备",
  "孙权",
  "诸葛亮",
  "貂蝉",
  "关羽",
  "司马懿",
  "周瑜",
  "赵云",
];
let game = null,
  configData = null,
  busy = false,
  auto = false,
  tab = "public",
  draft = "",
  actionChoice = "",
  targetChoice = "";
let lobby = { size: 9, human: 1, mode: "demo", human_role: "" };
let debugOpen = false,
  debugSeat = "latest",
  debugTraceId = "",
  memoryQuery = "",
  memoryDay = "0",
  memoryResult = null;
let toastTimer,
  loopEpoch = 0,
  previousTurn = "",
  showingLobby = false;
let readingDelay = Number(sessionStorage.getItem("readingDelay") ?? 3);
let activeRequest = 0;

async function api(path, body) {
  const response = await fetch("/api/" + path, {
    method: body === undefined ? "GET" : "POST",
    headers:
      body === undefined
        ? {}
        : { "Content-Type": "application/json", "X-Night-Council": "1" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  let result;
  try {
    result = await response.json();
  } catch {
    throw new Error("服务暂时不可用，请检查启动窗口。");
  }
  if (!response.ok) throw new Error(result.detail || "请求未完成，请重试。");
  return result;
}
function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    node.hidden = true;
  }, 4500);
}
function openDialog(id) {
  $(id).showModal();
}
function stopAuto() {
  auto = false;
  loopEpoch++;
}
function setGame(data) {
  if (data.game) {
    if (game?.id !== data.game.id) {
      debugOpen = false;
      debugSeat = "latest";
      debugTraceId = "";
      memoryQuery = "";
      memoryDay = "0";
      memoryResult = null;
    }
    game = data.game;
    showingLobby = false;
  }
  const current = game ? `${game.id}:${game.version}` : "";
  if (current !== previousTurn) {
    draft = "";
    actionChoice = "";
    targetChoice = "";
    previousTurn = current;
  }
}
function art() {
  return `<div class="hero-art" aria-label="月下七张身份牌的抽象插画"><div class="art-grid"></div><span class="art-caption">THE NIGHT KNOWS</span><span class="art-coord">月隐 · 人心</span><div class="art-ring"></div><div class="art-ring inner"></div><div class="moon"></div>${["谋", "隐", "鉴", "疑", "弈", "诡", "信"].map((x, i) => `<span class="art-seat s${i + 1}">${x}</span>`).join("")}<span class="art-bottom">一席九人 · 各怀其心</span></div>`;
}
function renderLobby() {
  markRoute("new");
  showingLobby = true;
  $("#app").innerHTML =
    `<section class="lobby"><div class="hero"><div class="hero-copy"><span class="eyebrow">A GAME OF TRUST & DECEPTION</span><h1>入夜之前，<br>没有人值得<em>全信。</em></h1><p>当三国群雄隐入狼人之夜，每句证词都可能是一场布局。<br>与 AI 同席，在交锋与沉默之间，找到真相。</p><div class="hero-meta"><span>独立人格</span><span>私密协商</span><span>真实推理</span></div></div>${art()}</div>
  <div class="setup-panel"><div class="setup-block"><div class="setup-label"><b>01</b> 选择你的席位</div><div class="segmented"><button data-setting="human" data-value="1" class="${lobby.human ? "selected" : ""}">真人入局</button><button data-setting="human" data-value="0" class="${!lobby.human ? "selected" : ""}">AI 观战</button></div><p class="setup-detail">${lobby.human ? "你将坐在 1 号位，与群雄一起推理。" : "上帝视角观看 AI 博弈，所有身份和私聊可见。"}</p>${
    lobby.human
      ? `<label class="small-select">练习身份<select id="human-role"><option value="">随机分配</option>${Object.entries(
          roleNames,
        )
          .filter(([r]) => lobby.size === 9 || r !== "hunter")
          .map(
            ([r, n]) =>
              `<option value="${r}" ${lobby.human_role === r ? "selected" : ""}>${n}</option>`,
          )
          .join("")}</select></label>`
      : ""
  }</div>
  <div class="setup-block"><div class="setup-label"><b>02</b> 选择棋局</div><div class="segmented"><button data-setting="size" data-value="9" class="${lobby.size === 9 ? "selected" : ""}">九人 · 预女猎</button><button data-setting="size" data-value="6" class="${lobby.size === 6 ? "selected" : ""}">六人 · 初识局</button></div><p class="setup-detail">${lobby.size === 9 ? "3 狼人 · 3 平民 · 预言家 · 女巫 · 猎人" : "2 狼人 · 2 平民 · 预言家 · 女巫"}</p><label class="small-select"><span>屠边规则 · 平票 PK · 无警长</span></label></div>
  <div class="setup-block"><div class="setup-label"><b>03</b> 选择对手</div><div class="segmented"><button data-setting="mode" data-value="demo" class="${lobby.mode === "demo" ? "selected" : ""}">练习模式</button><button data-setting="mode" data-value="llm" class="${lobby.mode === "llm" ? "selected" : ""}">真实大模型</button></div><p class="setup-detail">${lobby.mode === "demo" ? "规则机器人，无需密钥。先熟悉流程和技能。" : "使用已配置的 LLM，按供应商规则产生 API 费用。"}</p><div class="small-select"><span class="live-dot"></span><span>${configData?.configs?.global?.configured ? "默认模型已连接配置" : "可在模型设置中接入 API"}</span><button class="text-button" id="lobby-settings">设置</button></div></div></div>
  <div class="start-row"><div class="start-note"><span class="gold">✦ 今夜须知</span>　身份只属于你，真相属于最后留下的人。新玩家可先阅读 <button id="lobby-rules" class="text-button">玩法手册</button>。${game ? '<br><button id="resume-game" class="text-button">返回当前对局</button>' : ""}</div><button id="start-game" class="button primary large" ${busy ? "disabled" : ""}>${busy ? "正在安排席位…" : "入席，天黑请闭眼"}<span class="arrow">↗</span></button></div></section>`;
  document.querySelectorAll("[data-setting]").forEach((button) =>
    button.addEventListener("click", () => {
      const key = button.dataset.setting;
      lobby[key] =
        key === "mode" ? button.dataset.value : Number(button.dataset.value);
      if (lobby.size === 6 && lobby.human_role === "hunter")
        lobby.human_role = "";
      renderLobby();
    }),
  );
  $("#human-role")?.addEventListener("change", (e) => {
    lobby.human_role = e.target.value;
  });
  $("#lobby-settings").onclick = showSettings;
  $("#lobby-rules").onclick = () => openDialog("#rules-dialog");
  $("#resume-game")?.addEventListener("click", () => {
    showingLobby = false;
    navigate("game");
  });
  $("#start-game").onclick = startGame;
}
async function startGame() {
  if (busy) return;
  busy = true;
  stopAuto();
  renderLobby();
  try {
    const result = await api("games", {
      ...lobby,
      human_role: lobby.human ? lobby.human_role || null : null,
    });
    setGame(result);
    tab = "public";
  } catch (error) {
    toast(error.message);
  } finally {
    busy = false;
    showingLobby ? renderLobby() : navigate("game");
  }
}
function roleCard() {
  const p = game.personal;
  return `<section class="panel identity"><div class="identity-top"><span>${p ? "你的身份" : "观战视角"}</span><span>${p ? `NO. ${String(game.human).padStart(2, "0")}` : "ALL SEEING"}</span></div><div class="identity-symbol">${p ? roleSymbols[p.role] : "观"}</div><h2>${p ? roleNames[p.role] : "洞见全局"}</h2><p class="identity-desc">${p ? roleDescriptions[p.role] : "这里没有迷雾。你能看到每位玩家的真实身份、私密协商和行动。观察他们如何推理、试探与伪装。"}</p><div class="identity-divider"></div><div class="intel">${p ? `<strong>${escapeHtml(p.name)}</strong> · ${escapeHtml(p.trait)}<br>${p.alive ? "你仍在局中，请谨慎使用已知信息。" : "你已出局，可继续观战，终局将揭示全部身份。"}` : "观战不会向 AI 透露任何额外信息。"}
    ${p?.role === "witch" ? `<div><span class="potion ${p.potions.save ? "" : "used"}">◇ 解药 ${p.potions.save ? "可用" : "已用"}</span><span class="potion ${p.potions.poison ? "" : "used"}">◇ 毒药 ${p.potions.poison ? "可用" : "已用"}</span></div>` : ""}
    ${
      p?.role === "seer"
        ? `<div class="identity-divider"></div><strong>查验笔记</strong><br>${
            Object.keys(p.checks).length
              ? Object.entries(p.checks)
                  .map(
                    ([id, r]) => `${id}号 · ${r === "wolf" ? "狼人" : "好人"}`,
                  )
                  .join("<br>")
              : "尚未查验"
          }`
        : ""
    }
    ${
      p?.role === "wolf"
        ? `<div class="identity-divider"></div><strong>你的同伴</strong><br>${game.players
            .filter((x) => x.role === "wolf" && x.id !== game.human)
            .map((x) => `${x.id}号 ${x.name}${x.alive ? "" : " · 已出局"}`)
            .join("、")}`
        : ""
    }</div></section>`;
}

function debugPanel() {
  return liveReviewPanel();
}
function bindDebug() {
  bindLiveReview();
}

async function unlockDebug() {
  if (busy) return;
  stopAuto();
  try {
    setGame(await api("debug", { game_id: game.id, version: game.version }));
    debugOpen = true;
    $("#debug-dialog").close();
    renderGame();
    toast("本局已标记为调试局，私密决策只向你开放。");
  } catch (e) {
    toast(e.message);
  }
}
function roster() {
  return `<section class="panel roster-panel"><div class="panel-header"><h2>席上众人</h2><small>${game.alive_count} / ${game.players.length} 存活</small></div><div class="roster">${game.players.map((p) => `<div class="player-card ${p.alive ? "" : "dead"} ${p.role === "wolf" ? "wolf-known" : ""} ${game.pending?.actor === p.id ? "current" : ""}" aria-label="${p.id}号${p.name}，${p.alive ? "存活" : "出局"}，${p.role ? roleNames[p.role] : "身份未知"}"><span class="seat-no">${String(p.id).padStart(2, "0")}</span>${p.id === game.human ? '<span class="you">你</span>' : ""}${avatar(p.id, "roster-avatar", p.name)}<div class="player-name">${p.name}</div><div class="player-role">${p.role ? roleNames[p.role] : "身份未明"}${p.alive ? "" : " · 出局"}</div></div>`).join("")}</div><div class="roster-note">${game.omniscient ? "◇ 全知视角 · 身份已公开" : "◇ 迷雾中的身份由服务端隐藏"}<br>高亮边框标记当前公开行动者。</div></section>`;
}
function eventHtml(e, players = game.players, inspect = true) {
  const isPrivate = !e.public;
  let text = e.text;
  if (e.kind === "action" && e.data)
    text = `${actionNames[e.data.action] || e.data.action}${e.data.target ? " · " + e.data.target + "号" : ""}`;
  return `<article class="event ${e.kind}" data-event-id="${e.id}"><div class="event-head">${e.actor ? avatar(e.actor, "event-avatar") : '<span class="event-icon">◇</span>'}<span class="event-name">${escapeHtml(e.title)}</span>${isPrivate ? '<span class="private-tag">私密</span>' : ""}<span class="event-time">${e.day ? `第 ${e.day} ${e.phase === "night" ? "夜" : "天"}` : "入席"} · ${String(e.id).padStart(2, "0")}</span></div><p class="event-text">${escapeHtml(text)}</p>${
    e.kind === "ballot" && e.data
      ? `<div class="ballots">${Object.entries(e.data.ballots)
          .map(([a, t]) => voteChipHtml(e, a, t))
          .join("")}</div>`
      : ""
  }${inspect && game.debug_available && game.debug_traces?.some((t) => t.event_id === e.id) ? `<button class="text-button inspect-decision" data-observe="${e.id}">◎ 观察决策</button>` : ""}</article>`;
}
function feedHtml() {
  const events = game.events.filter(
    (e) => tab === "all" || (tab === "public" ? e.public : !e.public),
  );
  return events.length
    ? events.map((e) => eventHtml(e)).join("")
    : '<div class="feed-empty">此刻，寂静也是线索。<br>属于这个频道的消息将出现在这里。</div>';
}
function actionHtml() {
  const pending = game.pending;
  if (game.winner)
    return `<div class="action-title">✦ 故事已落幕，真相已揭晓。</div><div class="wait-line"><span class="wait-copy">切换「全局」查看私聊和夜间行动。</span><button class="button secondary" id="replay-action">进入复盘 ↗</button></div>`;
  let error = game.error
    ? `<div class="error-box">${escapeHtml(game.error)}<br><button id="error-settings" class="text-button">检查模型设置</button>　<button id="switch-practice" class="text-button">改用练习模式继续</button></div>`
    : "";
  if (game.needs_human) {
    const speech = pending.actions.includes("speak");
    if (!pending.actions.includes(actionChoice))
      actionChoice = pending.actions[0];
    let note = "你可以隐藏身份或提出质疑，法官只裁定规则。";
    if (pending.kind === "rebuttal")
      note = "回应首轮针对你的质疑，或说明你的判断为何改变。";
    if (pending.kind === "wolf_chat") note = "仅你的狼人同伴能听到这段协商。";
    if (pending.kind === "witch")
      note = pending.victim
        ? `今夜刀口是 ${pending.victim} 号。每晚至多一瓶药。`
        : "解药已用尽，不再提供刀口信息。";
    if (pending.kind === "seer")
      note = "结果只会告诉你，对方是否属于狼人阵营。";
    if (["vote", "pk_vote"].includes(pending.kind))
      note = "选票先封存，所有玩家提交后统一公开。";
    if (pending.kind === "hunter")
      note = "你可以带走一位存活者，也可以选择不开枪。";
    return (
      error +
      `<form id="action-form"><div class="action-title"><span class="live-dot"></span> 轮到你了 · ${pending.label}</div><p class="action-subtitle">${note}</p>${speech ? `<label class="sr-only" for="speech">你的发言</label><textarea id="speech" maxlength="600" placeholder="说出你的判断，让他们相信你…">${escapeHtml(draft)}</textarea>` : `<div class="action-selects"><label>你的决定<select id="action-choice">${pending.actions.map((a) => `<option value="${a}" ${actionChoice === a ? "selected" : ""}>${actionNames[a]}</option>`).join("")}</select></label><label id="target-label">行动目标<select id="target-choice"></select></label></div>`}<div class="action-footer"><small>${speech ? '<span id="char-count">' + draft.length + "</span> / 600 字 · Ctrl + Enter 提交" : "行动提交后不可撤回"}</small><button class="button primary" type="submit" ${busy ? "disabled" : ""}>${busy ? '<span class="spinner"></span>提交中' : "确认" + (speech ? "发言" : "行动")} ↗</button></div></form>`
    );
  }
  return (
    error +
    `<div class="action-title">${busy ? '<span class="spinner"></span>' : "◇"} ${busy ? "正在思考，请稍候" : pending?.label || "等待下一幕"}</div><div class="wait-line"><span class="wait-copy">${auto ? "自动推进中，轮到你时会停下。" : game.mode === "llm" ? "模型将根据已知信息，作出自己的判断。" : "练习机器人已就位，推进即可继续。"}</span><button id="step-action" class="button secondary" ${busy || auto ? "disabled" : ""}>${game.error ? "重试本回合" : "推进一步"} →</button></div>`
  );
}
function renderGame() {
  if (currentPage !== "game") return;
  if (!game) return renderLobby();
  const oldFeed = $("#feed");
  const scrollTop = oldFeed?.scrollTop || 0;
  const atBottom =
    !oldFeed ||
    oldFeed.scrollHeight - oldFeed.scrollTop - oldFeed.clientHeight < 80;
  const phase = game.phase;
  const phaseName =
    phase === "night"
      ? "夜幕落下"
      : phase === "vote"
        ? "众议裁决"
        : phase === "finished"
          ? "真相大白"
          : "天光初现";
  const phaseIndex =
    phase === "night"
      ? 0
      : phase === "vote"
        ? 3
        : ["talk", "rebuttal", "pk_talk"].includes(game.pending?.kind)
          ? 2
          : 1;
  $("#app").innerHTML =
    `<section class="board"><div class="board-heading"><div class="board-title"><div><span class="eyebrow">${phase === "night" ? "NIGHTFALL" : phase === "finished" ? "THE TRUTH" : "THE COUNCIL"}</span><h1>第 ${game.day} ${phase === "night" ? "夜" : "天"} <span class="gold">·</span> ${phaseName}</h1></div><div class="game-badges"><span class="badge gold">${game.mode === "llm" ? "真实 LLM" : "练习模式"}</span><span class="badge">${game.human ? "真人入局" : "AI 观战"}</span></div></div><div class="board-actions"><button class="button ghost" id="new-game" ${busy ? "disabled" : ""}>重新开局</button><button class="button secondary" id="download-replay" ${busy ? "disabled" : ""}>复盘回看 ↗</button>${!game.winner ? `<button class="button ${auto ? "secondary" : "primary"}" id="auto-toggle" ${busy && !auto ? "disabled" : ""}>${auto ? "Ⅱ 暂停推进" : "▷ 自动推进"}</button>` : ""}</div></div>
  ${game.winner ? `<div class="result-banner"><div><span class="eyebrow">THE COUNCIL HAS SPOKEN</span><h2>${game.winner === "good" ? "长夜已尽 · 好人获胜" : game.winner === "wolf" ? "暗影得势 · 狼人获胜" : "未分高下 · 本局平局"}</h2><p>${escapeHtml(game.events.filter((e) => e.kind === "result").at(-1)?.text)} 所有身份与私密记录现已解锁。</p></div><span class="result-seal">终</span><button id="result-new" class="button primary">再赴一局 ↗</button></div>` : ""}
  <div class="board-layout">${roster()}<section class="panel conversation"><div class="phase-track">${["01 夜间行动", "02 天亮结算", "03 公开议事", "04 投票放逐"].map((t, i) => `<span class="${!game.winner && i === phaseIndex ? "active" : ""}">${t}</span>`).join("")}</div><div class="feed-header"><div class="feed-tabs"><button class="feed-tab ${tab === "public" ? "active" : ""}" data-tab="public">公共议事</button><button class="feed-tab ${tab === "private" ? "active" : ""}" data-tab="private">${game.human ? "私密情报" : "幕后行动"}</button>${game.omniscient ? `<button class="feed-tab ${tab === "all" ? "active" : ""}" data-tab="all">全局</button>` : ""}</div><span class="feed-meta">${game.events.length} 条已知记录</span></div><div id="feed" class="feed" role="log" aria-label="对局记录">${feedHtml()}</div><div class="action-area">${actionHtml()}</div></section><aside class="aside-stack">${roleCard()}<section class="panel notes-panel"><h3>局中锦囊</h3><p>${game.phase === "night" ? "夜间信息各有归属。查验只看阵营，刀口只告诉尚有解药的女巫，狼人协商仅在狼队中流转。" : "强势不等于可信，沉默也不等于心虚。留意角色声明与票型能否互相印证，再决定把信任交给谁。"}</p><div class="identity-divider"></div><div class="usage-row"><span>模型请求</span><strong>${game.usage.calls} 次</strong></div><div class="usage-row"><span>已报告用量</span><strong>${game.usage.tokens.toLocaleString()} tokens</strong></div><div class="usage-row"><span>存档</span><strong>每步自动保存</strong></div></section></aside></div></section>`;
  const feed = $("#feed");
  const debugSlot = document.createElement("div");
  debugSlot.innerHTML = debugPanel();
  const sidebar = $(".aside-stack");
  sidebar.insertBefore(
    debugSlot.firstElementChild,
    debugOpen ? sidebar.firstElementChild : sidebar.children[1],
  );
  if (game.debug_mode) {
    const badge = document.createElement("span");
    badge.className = "badge gold";
    badge.textContent = "调试局 · 全知";
    $(".game-badges").appendChild(badge);
  }
  const pace = document.createElement("label");
  pace.className = "pace-control";
  pace.innerHTML = `阅读间隔 <select id="reading-pace" aria-label="自动推进阅读间隔">${[0, 3, 6, 10].map((n) => `<option value="${n}" ${readingDelay === n ? "selected" : ""}>${n} 秒</option>`).join("")}</select>`;
  $(".board-actions").prepend(pace);
  $("#reading-pace").onchange = (e) => {
    readingDelay = Number(e.target.value);
    sessionStorage.setItem("readingDelay", String(readingDelay));
  };
  bindDebug();
  feed.scrollTop = atBottom ? feed.scrollHeight : scrollTop;
  document.querySelectorAll("[data-tab]").forEach(
    (b) =>
      (b.onclick = () => {
        tab = b.dataset.tab;
        renderGame();
        $("#feed").scrollTop = $("#feed").scrollHeight;
      }),
  );
  $("#new-game").onclick = () => {
    stopAuto();
    renderGame();
    openDialog("#confirm-dialog");
  };
  $("#result-new")?.addEventListener("click", () => {
    stopAuto();
    renderLobby();
  });
  $("#download-replay").onclick = () => navigate("replay", game.id);
  $("#replay-action")?.addEventListener("click", () =>
    navigate("replay", game.id),
  );
  $("#auto-toggle")?.addEventListener("click", toggleAuto);
  $("#step-action")?.addEventListener("click", () => step());
  $("#error-settings")?.addEventListener("click", showSettings);
  $("#switch-practice")?.addEventListener("click", switchPractice);
  $("#action-form")?.addEventListener("submit", submitAction);
  $("#speech")?.addEventListener("input", (e) => {
    draft = e.target.value;
    $("#char-count").textContent = draft.length;
  });
  $("#speech")?.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key === "Enter") {
      e.preventDefault();
      $("#action-form").requestSubmit();
    }
  });
  $("#action-choice")?.addEventListener("change", (e) => {
    actionChoice = e.target.value;
    targetChoice = "";
    updateTargets();
  });
  if ($("#target-choice")) updateTargets();
}
function updateTargets() {
  const p = game.pending;
  const needs = ["kill", "check", "poison", "shoot", "vote", "save"].includes(
    actionChoice,
  );
  $("#target-label").hidden = !needs;
  let targets = actionChoice === "save" ? [p.victim] : p.targets;
  if (!targets.includes(Number(targetChoice)))
    targetChoice = String(targets[0] || "");
  $("#target-choice").innerHTML = targets
    .map(
      (id) =>
        `<option value="${id}" ${targetChoice === String(id) ? "selected" : ""}>${id}号 · ${game.players[id - 1].name}</option>`,
    )
    .join("");
  $("#target-choice").onchange = (e) => {
    targetChoice = e.target.value;
  };
}
async function submitAction(e) {
  e.preventDefault();
  if (busy) return;
  const speech = game.pending.actions.includes("speak");
  const action = speech ? "speak" : actionChoice;
  if (speech && !draft.trim()) {
    toast("先写下你的发言。");
    $("#speech").focus();
    return;
  }
  const needs = ["kill", "check", "poison", "shoot", "vote", "save"].includes(
    action,
  );
  await step({
    action,
    target: needs ? Number(targetChoice) : null,
    speech: speech ? draft.trim() : "",
  });
}
async function step(action = null) {
  if (busy || !game || game.winner) return false;
  busy = true;
  renderGame();
  const requestToken = ++activeRequest;
  const started = Date.now();
  let polling = false;
  const progressTimer = setInterval(async () => {
    if (polling || !busy || requestToken !== activeRequest) return;
    polling = true;
    try {
      const state = await api("state");
      const title = $(".action-area .action-title");
      if (title && busy && requestToken === activeRequest) {
        title.textContent = `${state.progress?.label || "等待模型响应"} · 已等待 ${Math.floor((Date.now() - started) / 1000)} 秒`;
      }
    } catch {
    } finally {
      polling = false;
    }
  }, 1000);
  let ok = false;
  try {
    setGame(
      await api("step", { game_id: game.id, version: game.version, action }),
    );
    ok = true;
  } catch (error) {
    stopAuto();
    toast(error.message);
    try {
      setGame(await api("state"));
    } catch {}
  } finally {
    clearInterval(progressTimer);
    busy = false;
    renderGame();
  }
  return ok;
}
async function toggleAuto() {
  if (auto) {
    stopAuto();
    renderGame();
    return;
  }
  if (busy) return;
  auto = true;
  const epoch = ++loopEpoch;
  renderGame();
  while (
    auto &&
    epoch === loopEpoch &&
    game &&
    !game.winner &&
    !game.needs_human
  ) {
    if (!(await step())) break;
    await new Promise((resolve) => setTimeout(resolve, readingDelay * 1000));
  }
  if (epoch === loopEpoch) {
    auto = false;
    renderGame();
    if (game?.needs_human) toast("轮到你了，自动推进已暂停。");
  }
}
async function switchPractice() {
  if (busy) return;
  stopAuto();
  try {
    setGame(await api("practice", { game_id: game.id, version: game.version }));
    renderGame();
    toast("已明确切换为练习模式，后续不再调用模型。");
  } catch (e) {
    toast(e.message);
  }
}
async function downloadReplay() {
  try {
    const response = await fetch("/api/replay");
    if (!response.ok) {
      const body = await response.json();
      throw new Error(body.detail);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `夜议-${game.id}-${game.winner ? "完整复盘" : "当前视角"}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    toast(e.message);
  }
}
async function showSettings() {
  stopAuto();
  if (game && !showingLobby) renderGame();
  try {
    configData = await api("settings");
    fillConfig();
    $("#model-result").textContent = "";
    openDialog("#settings-dialog");
  } catch (e) {
    toast(e.message);
  }
}
function fillConfig() {
  const scope = $("#config-scope").value;
  const own = configData.configs[scope];
  const base = own || configData.configs.global;
  $("#config-url").value = base.base_url;
  $("#config-model").value = base.model;
  $("#config-decision").value = base.decision_mode || "deep";
  $("#config-key").value = "";
  $("#key-status").textContent = own?.configured
    ? "已有密钥 · 留空保留"
    : scope === "global"
      ? "未设置密钥"
      : own
        ? "同地址可继承默认密钥"
        : "继承全局配置";
  $("#clear-config").textContent = scope === "global" ? "清除密钥" : "恢复默认";
  $("#model-result").textContent = "";
}
async function saveConfig(test = false, clear = false) {
  if (!clear && !$("#settings-form").reportValidity()) return;
  const form = $("#settings-form");
  const controls = [...form.querySelectorAll("button")];
  controls.forEach((b) => (b.disabled = true));
  const result = $("#model-result");
  result.className = "model-result";
  result.textContent = test ? "正在保存并测试连接…" : "";
  try {
    const scope = $("#config-scope").value;
    configData = await api("settings", {
      scope,
      base_url: $("#config-url").value,
      model: $("#config-model").value,
      api_key: $("#config-key").value,
      decision_mode: $("#config-decision").value,
      clear,
    });
    $("#config-key").value = "";
    fillConfig();
    if (test) {
      result.textContent = "正在验证 JSON 输出…";
      const check = await api("test-model", { scope });
      result.textContent = `连接成功 · ${check.model}\n耗时 ${check.latency} 秒 · ${check.tokens} tokens`;
    } else {
      result.textContent = clear
        ? "配置已清除。"
        : "设置已保存。密钥仅驻留服务端内存。";
      toast(clear ? "配置已清除" : "模型设置已保存");
    }
  } catch (e) {
    result.className = "model-result error";
    result.textContent = e.message;
  } finally {
    controls.forEach((b) => (b.disabled = false));
    if (showingLobby) renderLobby();
  }
}
document
  .querySelectorAll(".close-dialog")
  .forEach(
    (button) => (button.onclick = () => button.closest("dialog").close()),
  );
document.querySelectorAll("dialog").forEach((dialog) =>
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) {
      const r = dialog.getBoundingClientRect();
      if (
        e.clientX < r.left ||
        e.clientX > r.right ||
        e.clientY < r.top ||
        e.clientY > r.bottom
      )
        dialog.close();
    }
  }),
);
$("#nav-rules").onclick = () => openDialog("#rules-dialog");
$("#nav-settings").onclick = showSettings;
$("#nav-game").onclick = () => {
  if (game) {
    showingLobby = false;
    navigate("game");
  } else renderLobby();
};
$("#confirm-new").onclick = () => {
  $("#confirm-dialog").close();
  stopAuto();
  renderLobby();
};
$("#confirm-debug").onclick = unlockDebug;
$("#config-scope").onchange = fillConfig;
$("#settings-form").onsubmit = (e) => {
  e.preventDefault();
  saveConfig();
};
$("#test-config").onclick = () => saveConfig(true);
$("#clear-config").onclick = () => saveConfig(false, true);
$("#seat-options").innerHTML = names
  .map(
    (name, i) => `<option value="seat:${i + 1}">${i + 1}号 · ${name}</option>`,
  )
  .join("");
$("#role-guide").innerHTML = Object.entries(roleNames)
  .map(
    ([r, n]) =>
      `<div class="role-rule"><span>${roleSymbols[r]}</span><h3>${n}</h3><p>${roleDescriptions[r]}</p></div>`,
  )
  .join("");
async function init() {
  try {
    const [state, settings] = await Promise.all([
      api("state"),
      api("settings"),
    ]);
    configData = settings;
    lobby.mode = settings.default_available ? "llm" : "demo";
    setGame(state);
    if (state.busy) {
      toast("正在完成之前的行动，请稍候。");
      setTimeout(init, 1500);
      return;
    }
    routeFromLocation();
  } catch (e) {
    $("#app").innerHTML =
      '<div class="loading-page">服务连接失败，请确认启动窗口仍在运行。<button id="retry-load" class="button secondary">重新连接</button></div>';
    $("#retry-load").onclick = init;
    toast(e.message);
  }
}
