// 업무 화면. 서버 데이터는 textContent 로만 렌더(XSS 방지). 버튼 표시는 /api/me 의 권한 목록을 따르고, 최종 판단은 서버가 한다.
"use strict";
const NS = "http://www.w3.org/2000/svg";
const SVGT = new Set(["svg", "g", "rect", "line", "path", "text", "circle", "polyline", "polygon", "title", "defs", "pattern", "linearGradient", "stop", "marker", "ellipse"]);
const S = { me: null, w: null, data: null, overview: null, partners: null, variants: null, access: null, whatIf: {}, specs: {}, inboxAll: false,
  pfilter: "전체", pq: "", openReply: null, doc: null, loadMs: null };
const $ = (id) => document.getElementById(id);

const clean = (x) => x.replace(/\s*\u00b7\s*/g, ", ").replace(/\s+\u2014\s+/g, ": ").replace(/\u2014/g, "-").replace(/\u2013/g, "-");
function h(t, a, ...kids) {
  const e = SVGT.has(t) ? document.createElementNS(NS, t) : document.createElement(t);
  for (const [k, v] of Object.entries(a || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "css") Object.assign(e.style, v); // CSSOM(CSP 허용)
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat(Infinity)) if (c != null && c !== false) e.append(c instanceof Node ? c : clean(String(c)));
  return e;
}
const fmt = (n, d = 1) => (n == null || Number.isNaN(n) ? "-" : Number(n).toLocaleString("ko-KR", { maximumFractionDigits: d }));
const eok = (x) => (x == null ? "-" : `${fmt(x)}억`);
const krw = (x) => (x == null ? "-" : Math.abs(x) >= 1e8 ? `${fmt(x / 1e8)}억` : `${fmt(x / 1e4, 0)}만`);
const signed = (x, u = "") => (x == null ? "-" : `${x > 0 ? "+" : ""}${fmt(x)}${u}`);
const can = (cap) => (S.me?.can || []).includes(cap);
const ROLE = () => S.me.role;

// ── 아이콘(라인) ───────────────────────────────────────────────────────────────
const IC = {
  home: ["M3 10.5 12 3l9 7.5", "M5 9v12h14V9", "M10 21v-6h4v6"],
  bid: ["M6 3h8l4 4v14H6z", "M14 3v4h4", "M9 12h6", "M9 16h4"],
  inbox: ["M3 13l3-8h12l3 8", "M3 13v6h18v-6", "M3 13h5l1 3h6l1-3h5"],
  partners: ["M4 21V6l7-3v18", "M11 9h9v12", "M7 9h1", "M7 13h1", "M7 17h1", "M14 13h2", "M14 17h2", "M2 21h20"],
  settings: ["M4 7h9", "M17 7h3", "M13 5v4", "M4 12h3", "M11 12h9", "M7 10v4", "M4 17h11", "M19 17h1", "M15 15v4"],
  audit: ["M4 12a8 8 0 1 0 2.3-5.6", "M4 4v4h4", "M12 8v4l3 2"],
  test: ["M9 3h6", "M10 3v6L5 18a2 2 0 0 0 1.8 3h10.4a2 2 0 0 0 1.8-3l-5-9V3", "M7.5 15h9"],
  info: ["c12,12,9", "M12 11v5", "M12 8h.01"],
  logout: ["M14 4h5v16h-5", "M10 8l-4 4 4 4", "M6 12h10"],
  alert: ["M12 4 3 20h18z", "M12 10v4", "M12 17h.01"],
  check: ["M5 12l4 4 10-10"],
  clock: ["c12,12,9", "M12 7v5l3 2"],
  back: ["M15 6l-6 6 6 6"],
  search: ["c11,11,7", "M20 20l-4-4"],
  lock: ["M6 11h12v9H6z", "M8 11V8a4 4 0 0 1 8 0v3"],
  send: ["M4 12 20 4l-5 16-3-7z", "M12 13l8-9"],
  file: ["M6 3h8l4 4v14H6z", "M14 3v4h4"],
  dot: ["c12,12,3"],
  grid: ["M4 4h16v16H4z", "M4 10h16", "M4 15h16", "M10 4v16", "M15 4v16"],
  compare: ["M5 4v16", "M12 4v16", "M19 4v16", "M3 8h4", "M10 12h4", "M17 16h4"],
  list: ["M9 6h11", "M9 12h11", "M9 18h11", "M4 6h.01", "M4 12h.01", "M4 18h.01"],
  chart: ["M4 20V4", "M4 20h16", "M8 16v-5", "M12 16V8", "M16 16v-3"],
  graph: ["c6,6,2", "c18,6,2", "c12,18,2", "M8 6h8", "M7 8l4 8", "M17 8l-4 8"],
  ai: ["M12 3l2 5 5 2-5 2-2 5-2-5-5-2 5-2z"],
  x: ["M6 6l12 12", "M18 6L6 18"],
  chev: ["M9 6l6 6-6 6"],
  user: ["c12,8,4", "M4 21c0-4 4-6 8-6s8 2 8 6"],
};
function icon(name) {
  const svg = h("svg", { class: "ic", viewBox: "0 0 24 24", "aria-hidden": "true" });
  for (const d of IC[name] || []) {
    if (d.startsWith("c")) { const [cx, cy, r] = d.slice(1).split(","); svg.append(h("circle", { cx, cy, r })); }
    else svg.append(h("path", { d }));
  }
  return svg;
}

// ── 공통 UI ───────────────────────────────────────────────────────────────────
const tip = () => $("tip");
function showTip(target, lines) {
  const t = tip(); t.replaceChildren(...[].concat(lines).map((l) => h("div", {}, l))); t.classList.remove("hidden");
  const r = target.getBoundingClientRect ? target.getBoundingClientRect() : { left: target.clientX, bottom: target.clientY };
  t.style.left = Math.max(8, Math.min((r.left ?? 0) + 4, innerWidth - 310)) + "px"; t.style.top = (r.bottom ?? 0) + 8 + "px";
}
function tipAt(ev, lines) { const t = tip(); t.replaceChildren(...lines.map((l) => h("div", {}, l))); t.classList.remove("hidden");
  t.style.left = Math.min(ev.clientX + 14, innerWidth - 310) + "px"; t.style.top = ev.clientY + 14 + "px"; }
const hideTip = () => tip().classList.add("hidden");
function info(text) {
  const b = h("button", { class: "info", type: "button", "aria-label": "설명: " + text }, icon("info"));
  const show = () => showTip(b, text); b.addEventListener("mouseenter", show); b.addEventListener("focus", show);
  b.addEventListener("mouseleave", hideTip); b.addEventListener("blur", hideTip);
  b.addEventListener("keydown", (e) => { if (e.key === "Escape") hideTip(); });
  return b;
}
const badge = (txt, kind, ic) => h("span", { class: `badge ${kind || ""}` }, ic ? icon(ic) : null, txt);
const tile = (k, v, s, tipText) => h("div", { class: "tile" }, h("span", { class: "k" }, k, tipText ? info(tipText) : null), h("b", { class: "v" }, v), s ? h("span", { class: "s" }, s) : null);
function table(cols, rows, opts = {}) {
  if (!rows.length) return h("p", { class: "empty" }, opts.empty || "없음");
  return h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, cols.map((c) => h("th", { class: c.num ? "num" : null }, c.label)))),
    h("tbody", {}, rows.map((r) => h("tr", { class: opts.onRow ? "click" : null, onclick: opts.onRow ? () => opts.onRow(r) : null },
      cols.map((c) => h("td", { class: c.num ? "num" : null }, c.get(r))))))));
}
function section(title, tipText, tools, ...body) {
  return h("section", { class: "section" }, h("div", { class: "section-head" }, h("h2", {}, title), tipText ? info(tipText) : null,
    tools ? h("div", { class: "tools" }, tools) : null), ...body);
}
function toast(msg, bad = false) {
  const t = $("toast"); t.textContent = msg; t.className = "toast" + (bad ? " bad" : "");
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.add("hidden"), bad ? 9000 : 3500);
}
async function api(path, opts = {}) {
  const r = await fetch(path, { method: opts.method || "GET", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Capact-CSRF": "1" }, body: opts.body ? JSON.stringify(opts.body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (r.status === 401 && path !== "/api/login") { showLogin(); throw new Error("로그인이 필요합니다"); }
  if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : r.status === 500 ? "서버 오류" : JSON.stringify(j.detail || r.statusText));
  return j;
}
async function act(label, fn) {
  try { await fn(); toast(`${label} 완료`); S.data = null; S.w = null; await route(); } catch (e) { toast(`${label} 실패: ${e.message}`, true); }
}

// ── 데이터 ────────────────────────────────────────────────────────────────────
const simulating = () => Object.keys(S.whatIf).length > 0 || Object.keys(S.specs).length > 0;
async function getData() {
  if (!S.data) {
    const t0 = performance.now();
    S.data = simulating() ? await api("/api/simulate", { method: "POST", body: { what_if: S.whatIf, specs: S.specs } }) : await api("/api/checkpoints");
    S.loadMs = Math.round(performance.now() - t0);
  }
  return S.data;
}
const getOverview = async () => (S.overview ||= await api("/api/overview"));
const getPartners = async () => (S.partners ||= await api("/api/partners"));
const getVariants = async () => (S.variants ||= await api("/api/reply-variants"));
const getAccess = async () => (S.access ||= await api("/api/access"));

// ── 인증 ──────────────────────────────────────────────────────────────────────
function showLogin() { $("app").classList.add("hidden"); $("login").classList.remove("hidden"); document.title = "로그인"; }
$("login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault(); $("login-error").textContent = "";
  try {
    await api("/api/login", { method: "POST", body: { username: $("login-user").value, password: $("login-pass").value } });
    $("login-pass").value = ""; await start();
  } catch (e) { $("login-error").textContent = e.message; }
});
$("logout").append(icon("logout"));
$("logout").addEventListener("click", async () => { await api("/api/logout", { method: "POST" }).catch(() => {}); Object.assign(S, { me: null, data: null, w: null, partners: null, access: null }); $("main").replaceChildren(); $("chips").replaceChildren(); $("drawer").replaceChildren(); $("drawer").classList.add("hidden"); showLogin(); });

async function start() {
  S.me = await api("/api/me");
  S.access = await api("/api/access").catch(() => null);
  $("login").classList.add("hidden"); $("app").classList.remove("hidden");
  $("who").replaceChildren(h("b", {}, S.me.username), S.me.role);
  if (!location.hash) location.hash = "#home";
  await route();
}

// ── 라우팅 ────────────────────────────────────────────────────────────────────
const PAGES = [
  { id: "home", label: "홈", icon: "home" },
  { id: "bid", label: "입찰", icon: "compare" },
  { id: "assume", label: "확인요청", icon: "inbox" },
  { id: "board", label: "여력", icon: "grid" },
  { id: "tasks", label: "작업", icon: "list" },
  { id: "history", label: "이력", icon: "audit" },
  { sep: "참고" },
  { id: "partners", label: "거래처", icon: "partners" },
  { id: "analysis", label: "분석", icon: "chart" },
  { id: "settings", label: "설정", icon: "settings" },
];
addEventListener("hashchange", () => route());
function parseHash() { const [p, a] = location.hash.replace(/^#/, "").split("/"); return { page: p || "home", arg: a ? decodeURIComponent(a) : null }; }

async function route() {
  if (!S.me) return;
  const token = (route.n = (route.n || 0) + 1); // 빠른 이동 시 늦게 끝난 이전 페이지가 덮어쓰지 않게
  const { page, arg } = parseHash();
  renderNav(page);
  const main = $("main");
  main.replaceChildren(h("p", { class: "loading" }, "불러오는 중…"));
  try {
    const w = await getW();
    const d = ["analysis", "settings", "partners", "test", "audit"].includes(page) ? await getData() : S.data;
    renderChips(w);
    const fn = { home: pageHomeW, bid: pageBidW, assume: pageAssume, board: pageBoard, options: pageBidW, approvals: pageBidW, tasks: pageTasks,
      history: pageHistory, partners: arg ? pagePartner : pagePartners, analysis: arg === "work" ? pageInbox : pageBid, settings: pageSettings, audit: pageAudit,
      test: S.me.test_tools ? pageTestW : pageHomeW }[page] || pageHomeW;
    const nodes = await fn(page === "analysis" || page === "partners" || page === "settings" || page === "test" || page === "audit" ? d : w, arg, w);
    if (token !== route.n) return;
    main.replaceChildren(...[nodes].flat(Infinity).filter((x) => x != null && x !== false));
    renderNav(page);
  } catch (e) { if (token === route.n) main.replaceChildren(h("p", { class: "error" }, e.message)); }
  main.focus({ preventScroll: true });
}
function setTitle(t) { t = clean(t); $("page-title").textContent = t; document.title = t; }
function renderNav(page) {
  const mine = S.w ? myItemsW(S.w).length : 0;
  const cur = { options: "bid", approvals: "bid", audit: "history" }[page] || page;
  const link = (p) => h("a", { href: "#" + p.id, "aria-current": cur === p.id ? "page" : null }, icon(p.icon), p.label,
    p.id === "home" && mine ? h("span", { class: "count", "aria-label": `내 할 일 ${mine}건` }, mine) : null);
  $("nav-main").replaceChildren(...PAGES.map((p) => (p.sep ? h("div", { class: "nav-sep" }, p.sep) : link(p))));
  $("nav-test").replaceChildren(...(S.me.test_tools ? [link({ id: "test", label: "시연", icon: "test" })] : []));
}
function renderChips(w) {
  const b = w.bid, chips = [];
  if (b) {
    chips.push(h("span", { class: "chip " + (b.days_left <= 3 ? "bad" : b.days_left <= 7 ? "warn" : "") }, icon("clock"), `마감 D-${b.days_left}`));
    chips.push(h("span", { class: "chip " + ({ ON_HOLD: "bad", REVIEW_REQUIRED: "warn", WAITING_INFO: "warn", APPROVED: "good", READY_TO_SUBMIT: "good", SUBMITTED: "good" }[b.status] || "") }, b.status_ko));
  } else chips.push(h("span", { class: "chip" }, "입찰 건 없음"));
  chips.push(h("span", { class: "chip" }, `오늘 ${w.today}`));
  if (!writeCaps().length) chips.push(h("span", { class: "chip" }, icon("lock"), "읽기 전용"));
  if (S.me.test_tools) chips.push(h("span", { class: "chip test" }, icon("test"), "테스트 환경"));
  if (S.me.real_role === "시연 진행자") chips.push(roleSwitcher());
  $("chips").replaceChildren(...chips);
}

// ── 내 할 일(역할별) ────────────────────────────────────────────────────────────
function myItems(d) {
  const c = d.cp7, r = ROLE(), out = [];
  const isSales = r === "영업" || r === "영업임원";
  if (can("plan_inquiries") && !c.requests.length) out.push({ t: "문의 계획 만들기", k: "시작", go: "#inbox" });
  for (const q of c.requests) {
    if (q.status === "DRAFT" && isSales) out.push({ t: `발송: ${q.counterparty}`, k: "문의", go: "#inbox", due: q.answer_by });
    if (q.status === "SENT" && q.to_role === r) out.push({ t: `회신 입력: ${q.counterparty}`, k: "회신", go: "#inbox", due: q.answer_by });
    if (q.status === "EXPIRED" && (q.to_role === r || isSales)) out.push({ t: `재문의: ${q.counterparty}`, k: "기한 경과", go: "#inbox", due: q.answer_by });
  }
  for (const lv of c.levers) if (lv.status === "COUNTERED" && lv.owner === r) out.push({ t: `역제안 판단: ${lv.counterparty}`, k: "협상", go: "#inbox" });
  for (const a of c.approvals) if (a.status === "PENDING" && a.approver === r) out.push({ t: `승인 결정: ${a.action}`, k: "승인", go: "#inbox" });
  const gate = c.assumptions.some((a) => a.blocks);
  if (can("submit") && !c.submitted && c.requests.length && !gate) out.push({ t: "대안 선택·상신", k: "상신", go: "#analysis/6" });
  const approved = c.approvals.length && c.approvals.every((a) => a.status === "APPROVED");
  if (can("promote") && c.submitted && approved && (!c.plan || c.plan_stale)) out.push({ t: "확정 계획 반영", k: "확정", go: "#inbox" });
  return out;
}

// ── 차트 ──────────────────────────────────────────────────────────────────────
function bars({ title, tipText, labels, series, cap, capLabel, unit = "EA", stacked = true, tick = 4, short = (l) => l }) {
  const W = 900, H = 240, L = 54, B = 24, T = 8, n = labels.length, bw = (W - L - 8) / n;
  const tot = labels.map((_, i) => series.reduce((s, x) => s + (x.values[i] || 0), 0));
  const peak = stacked ? Math.max(...tot) : Math.max(...series.flatMap((s) => s.values));
  const ymax = Math.max(1, peak, cap || 0) * 1.08, y = (v) => T + (H - T - B) * (1 - v / ymax);
  const yf = (v) => (unit === "억" ? fmt(v, 0) : v >= 1e6 ? fmt(v / 1e6) + "M" : fmt(Math.round(v / 1000)) + "k");
  const svg = h("svg", { class: "plot", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": title });
  const ax = h("g", { class: "axis" });
  for (let k = 0; k <= 4; k++) { const v = (ymax / 4) * k; ax.append(h("line", { x1: L, x2: W - 4, y1: y(v), y2: y(v) }), h("text", { x: L - 6, y: y(v) + 3, "text-anchor": "end" }, yf(v))); }
  labels.forEach((w, i) => { if (i % tick === 0) ax.append(h("text", { x: L + i * bw + bw / 2, y: H - 7, "text-anchor": "middle" }, short(w))); });
  svg.append(ax);
  labels.forEach((w, i) => {
    const g = h("g"); let acc = 0;
    series.forEach((s, j) => {
      const v = s.values[i] || 0; if (v <= 0) return;
      if (stacked) { const y1 = y(acc + v), y0 = y(acc); acc += v;
        g.append(h("rect", { x: L + i * bw + 1, y: y1 + 1, width: Math.max(1, bw - 2), height: Math.max(0, y0 - y1 - 2), fill: `var(${s.color})` })); }
      else { const sw = (bw - 4) / series.length;
        g.append(h("rect", { x: L + i * bw + 2 + j * sw, y: y(v), width: Math.max(1, sw - 1), height: Math.max(0, y(0) - y(v)), fill: `var(${s.color})`, rx: 1 })); }
    });
    const hit = h("rect", { x: L + i * bw, y: T, width: bw, height: H - T - B, fill: "transparent" });
    hit.addEventListener("mousemove", (ev) => tipAt(ev, [w, ...series.filter((s) => s.values[i]).map((s) => `${s.label}: ${fmt(s.values[i])} ${unit}`),
      stacked ? `합계 ${fmt(tot[i])} ${unit}` : "", cap ? `${capLabel || "기준선"} ${fmt(cap)} ${unit}` : ""].filter(Boolean)));
    hit.addEventListener("mouseleave", hideTip);
    svg.append(g, hit);
  });
  if (cap) svg.append(h("line", { class: "cap-line", x1: L, x2: W - 4, y1: y(cap), y2: y(cap) }));
  const legend = h("div", { class: "legend" }, series.map((s) => { const i = h("i"); i.style.background = `var(${s.color})`; return h("span", {}, i, s.label); }),
    cap ? h("span", {}, "- - " + (capLabel || "기준선")) : null);
  const tbl = h("details", {}, h("summary", {}, "표로 보기"), table([{ label: "기간", get: (r) => r.w }, ...series.map((s) => ({ label: s.label, num: 1, get: (r) => fmt(s.values[r.i] || 0) })),
    ...(stacked ? [{ label: "합계", num: 1, get: (r) => fmt(tot[r.i]) }] : [])], labels.map((w, i) => ({ w, i }))));
  return h("div", { class: "chart" }, h("div", { class: "section-head" }, h("h2", {}, title), tipText ? info(tipText) : null), legend, svg, tbl);
}

function pareto(scs) {
  const W = 520, H = 250, L = 52, B = 30, T = 12;
  const xs = scs.map((s) => s.otif), ys = scs.map((s) => s.net_vs_baseline_eok);
  const x0 = Math.min(0, ...xs), x1 = 100, y0 = Math.min(0, ...ys) * 1.1, y1 = Math.max(1, ...ys) * 1.15;
  const X = (v) => L + (W - L - 10) * (v - x0) / (x1 - x0), Y = (v) => T + (H - T - B) * (1 - (v - y0) / (y1 - y0));
  const svg = h("svg", { class: "plot", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "대안 비교: 기존 고객 납기 준수율 대 증분 순이익" });
  svg.append(h("g", { class: "axis" }, h("line", { x1: L, x2: W - 10, y1: Y(0), y2: Y(0) }), h("line", { x1: L, x2: L, y1: T, y2: H - B }),
    h("text", { x: W - 10, y: H - 8, "text-anchor": "end" }, "기존 고객 납기 준수율(%) →"), h("text", { x: L + 4, y: T + 8 }, "증분 순이익(억) ↑")));
  scs.forEach((s) => {
    const c = h("circle", { cx: X(s.otif), cy: Y(s.net_vs_baseline_eok), r: 7, fill: s.feasible ? "var(--s1)" : "var(--surface)", stroke: "var(--s1)", "stroke-width": 2,
      "stroke-dasharray": s.id === "D" ? "3 2" : null });
    c.addEventListener("mousemove", (ev) => tipAt(ev, [`${s.id} ${s.label}`, `입찰 수락 ${s.accept_pct}% · 납기 준수 ${s.otif}%`, `증분 ${eok(s.net_vs_baseline_eok)}`, s.feasible ? "" : "실행 불가"].filter(Boolean)));
    c.addEventListener("mouseleave", hideTip);
    svg.append(c, h("text", { x: X(s.otif) + 10, y: Y(s.net_vs_baseline_eok) + 4, fill: "var(--text)", "font-size": 12 }, s.id));
  });
  return h("div", { class: "chart" }, h("div", { class: "section-head" }, h("h2", {}, "대안 위치"), info("가로: 기존 고객 납기 준수율, 세로: 입찰 없을 때 대비 증분 순이익. 속 빈 점은 실행 불가, 점선 D는 참고안(무동의 지연).")), svg);
}

// ── 날짜 ──────────────────────────────────────────────────────────────────────
function isoWeekMonday(label) { // "2026-W27"
  const [y, w] = label.split("-W").map(Number); const jan4 = new Date(Date.UTC(y, 0, 4)); const day = jan4.getUTCDay() || 7;
  const mon = new Date(jan4); mon.setUTCDate(jan4.getUTCDate() - day + 1 + (w - 1) * 7); return mon.toISOString().slice(0, 10);
}
const md = (iso) => (iso ? `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}` : "—");

// ════════════════════════════════════════════════════════════════════════════
// 홈
async function pageHome(d) {
  setTitle("홈");
  const ov = await getOverview(), c = d.cp7;
  const pm = ov.production_monthly, last = pm[pm.length - 1], prev = pm[pm.length - 2];
  const prodIds = Object.keys(ov.products);
  const sum = (r) => prodIds.reduce((s, p) => s + (r[p] || 0), 0);
  const sm = ov.sales_monthly, sl = sm[sm.length - 1], sly = sm[sm.length - 13];
  const tiles = h("div", { class: "grid" },
    tile(`${Number(last.month.slice(5))}월 생산`, fmt(sum(last)) + " EA", `전월 대비 ${signed(Math.round(1000 * (sum(last) / sum(prev) - 1)) / 10, "%")}`, "배치 생산 기록(원본) 월 합계"),
    tile(`${Number(sl.month.slice(5))}월 출하 매출`, krw(sl.shipment_krw), `수주 ${krw(sl.booking_krw)}${sly ? ` · 전년 동월 ${signed(Math.round(1000 * (sl.shipment_krw / sly.shipment_krw - 1)) / 10, "%")}` : ""}`),
    tile("진행 중 주문", `${fmt(d.cp1.backlog.orders)}건`, `${krw(d.cp1.backlog.amount_krw)} · 상태 불명 ${fmt(d.cp1.stale.orders)}건`, "수주 후 77일(관측 최대 리드타임) 이내 미출하 = 진행 중. 그보다 오래된 미출하는 누락/취소 구분 불가로 확인 요청."),
    tile("C-201 입찰 마감", `D-${c.days_left}`, `${md(c.deadline)} · 오늘 ${md(c.today)}`));

  // 진행 단계
  const sent = c.requests.filter((q) => q.status !== "DRAFT").length, replied = c.requests.filter((q) => ["REPLIED", "CLOSED"].includes(q.status)).length;
  const approvals = c.approvals.length, approved = c.approvals.filter((a) => a.status === "APPROVED").length;
  const steps = [
    ["문의 계획", c.requests.length > 0], ["문의 발송", c.requests.length > 0 && sent === c.requests.length],
    ["회신 수집", c.requests.length > 0 && replied === c.requests.length], ["상신", !!c.submitted],
    ["승인", approvals > 0 && approved === approvals], ["계획 확정", !!c.plan && !c.plan_stale]];
  const nowIdx = steps.findIndex((s) => !s[1]);
  const flow = h("div", { class: "flow" }, steps.map(([t, done], i) => h("div", { class: "st " + (done ? "done" : i === nowIdx ? "now" : "") }, icon(done ? "check" : i === nowIdx ? "dot" : "dot"), t)));
  const flowSub = h("div", { class: "row small muted" }, `문의 ${c.requests.length} · 발송 ${sent} · 회신 ${replied}`, approvals ? ` · 승인 ${approved}/${approvals}` : "");

  // 내 할 일
  const items = myItems(d);
  const todo = items.length ? h("ul", { class: "list" }, items.map((it) => h("li", {}, h("a", { class: "item", href: it.go },
    h("span", { class: "d" }, it.due ? md(it.due) : ""), h("span", { class: "t" }, it.t), badge(it.k)))))
    : h("p", { class: "empty" }, S.me.can.length ? "처리할 일이 없습니다" : "읽기 전용 계정");

  // 일정
  const ev = [{ d: c.today, t: "오늘" }, { d: c.deadline, t: "C-201 입찰 마감" }];
  const byDate = {};
  for (const q of c.requests) if (["DRAFT", "SENT"].includes(q.status)) (byDate[q.answer_by] ||= []).push(q.counterparty);
  for (const [dt, who] of Object.entries(byDate)) ev.push({ d: dt, t: `회신 기한 · ${who[0]}${who.length > 1 ? ` 외 ${who.length - 1}` : ""}` });
  const as4 = c.assumptions.find((a) => a.assumption_id === "AS-004");
  ev.push({ d: "2026-10-05", t: `C-201 공급 개시${as4 && as4.status !== "CONFIRMED" ? " (가정)" : ""}` });
  const wk = d.cp3.weekly_A.weeks;
  for (const o of d.cp3.options) if (o.start_week != null && wk[o.start_week]) ev.push({ d: isoWeekMonday(wk[o.start_week]), t: `${o.option_id} 가동 가능(최단)` });
  ev.sort((a, b) => a.d.localeCompare(b.d));
  const sched = h("ul", { class: "list" }, ev.map((e) => h("li", { class: e.d < c.today ? "past" : e.d === c.today ? "today" : "" }, h("span", { class: "d" }, e.d), h("span", { class: "t" }, e.t))));

  // 생산·가동률
  const months = pm.slice(-12);
  const prodChart = bars({ title: "월별 생산량", tipText: "원본 배치 생산 기록의 제품별 월 합계(EA).", labels: months.map((m) => m.month), tick: 2, short: (l) => l.slice(2).replace("-", "."),
    series: prodIds.map((p, i) => ({ label: `${ov.products[p]}`, values: months.map((m) => m[p] || 0), color: `--s${i + 1}` })) });
  const util = h("div", { class: "hbars" }, ov.lines.map((l) => {
    const fill = h("div", { class: "fill" + (l.utilization_pct >= 95 ? " hot" : "") }); fill.style.width = Math.min(100, l.utilization_pct) + "%";
    return h("div", { class: "hbar" }, h("span", {}, `${l.line_id} ${ov.products[l.product_id] || l.product_id}`), h("div", { class: "track" }, fill),
      h("span", { class: "num" }, `${l.utilization_pct}%`, l.utilization_pct >= 95 ? " " : null, l.utilization_pct >= 95 ? badge("병목", "bad") : null));
  }));

  return [tiles,
    section("C-201 진행", null, h("a", { href: "#analysis" }, "협상 분석"), h("div", { class: "panel" }, flow, flowSub)),
    h("div", { class: "cols" },
      h("div", {}, section("내 할 일", null, null, h("div", { class: "panel" }, todo)), h("div", { class: "section" }, prodChart)),
      h("div", {}, section("일정", null, null, h("div", { class: "panel" }, sched)),
        section("라인 가동률", `strat_capacity 기준(${ov.lines[0]?.snapshot}). 95% 이상은 병목으로 표시.`, null, h("div", { class: "panel" }, util))))];
}

// ════════════════════════════════════════════════════════════════════════════
// 입찰 검토
const BID_STEPS = ["기준 확인", "계약 조건", "생산 여력", "납기 영향", "수익성", "대안과 상신"];
async function pageBid(d, arg) {
  const n = Math.min(6, Math.max(1, Number(arg) || 1));
  setTitle("협상 분석(솔버)");
  const tabs = h("nav", { class: "steps", "aria-label": "검토 단계" }, BID_STEPS.map((t, i) => h("a", { href: `#analysis/${i + 1}`, "aria-current": n === i + 1 ? "page" : null },
    h("span", { class: "n" }, i + 1), t)));
  const body = [bid1, bid2, bid3, bid4, bid5, bid6][n - 1](d);
  const banner = simulating() ? h("div", { class: "banner warn" }, icon("alert"), h("span", { class: "grow" }, "가정 조정 결과입니다. 확정 계획이 아닙니다"),
    h("button", { onclick: () => { S.whatIf = {}; S.specs = {}; S.data = null; route(); } }, "원래 값으로")) : null;
  if (n >= 3) return [tabs, banner, h("div", { class: "cols" }, h("div", {}, body), h("aside", {}, whatIfPanel(d)))];
  return [tabs, banner, body];
}
const owner = (t) => h("span", { class: "owner" }, `확인: ${t}`);

function bid1(d) {
  const c = d.cp1, inv = c.invariance;
  return [
    h("div", { class: "grid" },
      tile("계획 기준", { S_MONTH: "월 능력", S_YEAR: "연 능력", H3: "실측 보정", CONFIRMED: "확정" }[c.basis] || c.basis, null, "용량표 수치를 한 달 능력으로 보고, 능력 × 가동률을 기존 부하로 둡니다."),
      tile("주간 기본 능력", fmt(c.base_weekly) + " EA", `기존 부하 ${fmt(c.load_weekly)} EA`, "L-02(더마) 라인. 부하 = 능력 × 가동률."),
      tile("진행 중 주문", `${fmt(c.backlog.orders)}건`, `${krw(c.backlog.amount_krw)} · 상태 불명 ${fmt(c.stale.orders)}건`),
      tile("규모 대응비", c.scale_factor, null, "수주·배치 규모를 계획 단위로 맞추는 비율.")),
    section("용량 단위", "원본 용량표의 기간 단위(월/연)가 명시되지 않음. 해석별로 계산해 결론이 같으면 진행, 다르면 생산기술 확정 전까지 상신을 잠급니다.", owner("생산기술"),
      inv ? h("div", {}, h("p", {}, inv.invariant ? badge("해석과 무관하게 같은 결론", "good", "check") : badge("해석에 따라 결론이 다름 · 상신 잠김", "bad", "lock")),
        table([{ label: "해석", get: (r) => INTERP_KO[r.interpretation] || r.interpretation }, { label: "입찰 물량 소화", num: 1, get: (r) => fmt(r.accept_pct) + "%" },
          { label: "필요 조치", get: (r) => r.actions.join(", ") || "없음" },
          { label: "실행", get: (r) => (r.feasible ? badge("가능", "good") : badge("불가", "bad")) }], inv.signatures))
        : h("p", {}, badge("생산기술 회신으로 확정", "good", "check"))),
    section("라인별 용량 vs 실적", "용량표(capacity_qty)를 월 단위로 볼 때 배치 실적과의 차이.", null,
      table([{ label: "라인", get: (r) => `${r.line_id} ${r.product_id}` }, { label: "용량표", num: 1, get: (r) => fmt(r.capacity_qty) },
        { label: "가동률", num: 1, get: (r) => r.utilization_pct + "%" }, { label: "월 실적", num: 1, get: (r) => fmt(r.observed_monthly_production_avg) },
        { label: "월 해석 오차", num: 1, get: (r) => fmt(r.rel_error_if_per_month * 100) + "%" }], c.capacity_lines)),
    section("원가·품질", null, owner("재무·품질"), h("div", { class: "grid" },
      tile("원료원가/EA", fmt(c.material.cost_per_ea_krw_2026h1) + "원", `${(c.material.kg_per_ea_2026h1 * 1000).toFixed(1)}g × ${c.material.usd_per_kg_latest} USD/kg`, "RFP 지정 원가 기준(원료 투입). 환율 연동."),
      tile("가공비/EA", fmt(c.unit_cost.conversion_per_ea) + "원", null, "특근 할증 계산 기준."),
      tile("확정 결함률(12개월)", (c.quality.confirmed_defect_rate_12m * 100).toFixed(2) + "%", null, "입찰 리콜 기대비용 = 결함률 × 단가."),
      tile("회수 검토 배치", c.quality.recall_eligible_batches.length + "개", `${fmt(c.quality.recall_eligible_units)} EA`, `결정: ${c.quality.decision_owner}`))),
  ];
}

function docViewer() { return h("div", { class: "doc", id: "doc" }, "조항을 선택하면 원문 근거가 표시됩니다."); }
async function showDoc(r, box) {
  try { const { text } = await api(`/api/contracts/${encodeURIComponent(r.doc)}`); const [s, e] = r.span;
    box.replaceChildren(text.slice(0, s), h("mark", {}, text.slice(s, e)), text.slice(e)); box.querySelector("mark")?.scrollIntoView({ block: "center" });
  } catch (err) { toast(err.message, true); }
}
const KIND = { late_penalty: "지체상금", termination_trigger: "해지 조건", partial_shipment: "분할 납품", price_discount_waiver: "할인 시 면책",
  fixed_delivery_slot: "고정 납기(편성)", cost_linked_pricing: "원가 연동" };
const paramText = (p) => Object.entries(p || {}).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(", ");
const GROUP_KO = { "C-102-SLOT": "C-102 편성 납품", OTHERS: "기타 고객", "RECALL-REPL": "회수 대체", "C-201": "C-201 입찰" };
const INTERP_KO = { S_MONTH: "월 능력으로 해석", S_YEAR: "연 능력으로 해석", H3: "배치 실적으로 보정", CONFIRMED: "확정" };
const REQ_KO = { formula_revalidation: "처방 재검증", "M-B_recontract": "M-B 재계약" };
const md2 = (s) => { const [, m, dd] = String(s).split("-"); return `${Number(m)}월 ${Number(dd)}일`; };
const man = (n) => krw(n) + "원";
// 계약 조항 조건을 사람이 읽는 문장으로
function clauseText(kind, p = {}) {
  switch (kind) {
    case "partial_shipment": return p.allowed ? `분할 납품 허용, 첫 출하 ${p.min_first_shipment_pct}% 이상` : "분할 납품 불가";
    case "late_penalty": return `지연 하루 ${p.rate_per_day_pct}%, 주문액의 ${p.cap_pct_of_order}%까지`;
    case "price_discount_waiver": return `${p.discount_pct}% 할인하면 납기 ${p.extends_grace_days}일 유예${p.waives_penalty ? ", 지체상금 면제" : ""}`;
    case "termination_trigger": return `${p.scope === "any_single_order" ? "주문 한 건이라도 " : ""}${p.delay_days_gt}일 넘게 늦으면 해지 가능`;
    case "fixed_delivery_slot": return `${md2(p.slot_date)} 편성 고정, ${p.product_id} ${fmt(p.qty)} EA, 취소 시 보상 ${man(p.cancellation_compensation_krw)}`;
    case "cost_linked_pricing": return "원료가 변동을 단가에 연동";
    default: return paramText(p);
  }
}
// 협상 카드 한계선
function breakevenText(b = {}) {
  if (b.max_unit_cost_multiplier != null) return `외주 단가 ${b.max_unit_cost_multiplier.toFixed(2)}배까지 이익`;
  if (b.max_overtime_multiplier_on_conversion_cost != null) return `특근 가공비 ${b.max_overtime_multiplier_on_conversion_cost.toFixed(1)}배까지 이익`;
  if (b.max_concession_krw != null) return `양보 ${man(b.max_concession_krw)}까지 (주당 ${man(b.value_per_week_krw)})`;
  if (b.value_at_stake_krw != null) return `걸린 가치 ${man(b.value_at_stake_krw)}${b.all_or_nothing_feasible === false ? ", 전량 조건이면 불가" : ""}`;
  if (b.assumed_delays_weeks) return `가정 소요 ${Object.entries(b.assumed_delays_weeks).map(([k, v]) => `${REQ_KO[k] || k} ${v}주`).join(", ")}`;
  return paramText(b) || "-";
}
function bid2(d) {
  const c = d.cp2, box = docViewer();
  return [
    section("계약 조항", "합성 계약서(원본에 원문 없음). 조항을 누르면 원문 위치를 표시합니다.", owner("영업·법무"),
      table([{ label: "조항", get: (r) => h("button", { class: "ghost", onclick: () => showDoc(r, box) }, icon("file"), `${r.customer} ${r.clause}`) },
        { label: "종류", get: (r) => KIND[r.kind] || r.kind }, { label: "조건", get: (r) => clauseText(r.kind, r.params) },
        { label: "구분", get: (r) => (r.hard ? badge("필수", "bad") : badge("조정 가능")) }, { label: "확인", get: (r) => (r.confirmed ? badge("확인됨", "good") : badge("미확인", "warn")) }], c.clauses)),
    section("원문", null, null, box),
    section("고객별 지연 한도", "해지 조건은 1주 여유, 지체상금은 상한 소진 시점을 한도로 계산. 근거 없으면 8주.", null,
      table([{ label: "고객", get: (r) => GROUP_KO[r.group] || r.group },
        { label: "늦어도 되는 기간", num: 1, get: (r) => (r.max_late_weeks == null ? "-" : r.max_late_weeks === 0 ? "늦으면 안 됨" : `최대 ${r.max_late_weeks}주`) },
        { label: "지체상금", num: 1, get: (r) => (r.late_rate ? `하루 ${r.late_rate}%` : "-") },
        { label: "근거", get: (r) => [r.penalty_clause, r.hard_clause].filter(Boolean).join(", ") || badge("가정", "warn") }], c.limits)),
  ];
}

function bid3(d) {
  const c = d.cp3, w = c.weekly_A;
  return [
    h("div", { class: "grid" }, tile("주간 기본 능력", fmt(c.base_weekly) + " EA", `${c.line} 가동률 ${c.utilization_pct}%`),
      tile("기존 부하 후 여유", fmt(c.free_weekly) + " EA/주"), tile("특근 한도", c.overtime_cap_pct + "%", null, "근무형태 자료가 없어 기본 0. 생산 회신이 있어야 반영."),
      tile("제외된 설비 옵션", c.excluded_options.join(", ") || "없음")),
    section("주별 생산 계획 (A안)", null, owner("생산관리"), bars({ title: "원천별 생산", labels: w.weeks, short: (l) => l.slice(5), cap: c.base_weekly, capLabel: "기본 능력",
      series: [{ label: "기본", values: w.production.base, color: "--s1" }, { label: "특근", values: w.production.overtime, color: "--s2" },
        { label: "설비 옵션", values: w.production.option, color: "--s3" }, { label: "외주", values: w.production.outsourced, color: "--s4" }] })),
    section("설비 옵션", "선행 요건(처방 재검증·원료 재계약) 회신 후에만 가동 시점이 정해집니다.", owner("생산·품질·구매"),
      table([{ label: "옵션", get: (r) => r.option_id }, { label: "가동 시작", num: 1, get: (r) => (r.start_week == null ? "요건 회신 후" : `${r.start_week}주차`) }, { label: "추가 능력/주", num: 1, get: (r) => fmt(r.added_weekly) },
        { label: "투자", num: 1, get: (r) => krw(r.capex_krw) }], c.options),
      h("p", { class: "small muted" }, "선행 요건 회신: ", Object.entries(c.requirement_replies).map(([k, v]) => `${REQ_KO[k] || k} ${STATUS[v.status] || v.status}${v.weeks ? ` ${v.weeks}주` : ""}`).join(", ") || "아직 없음")),
    c.notes.length ? h("details", {}, h("summary", {}, "계산 가정"), h("ul", { class: "small" }, c.notes.map((x) => h("li", {}, x)))) : null,
  ];
}

function bid4(d) {
  const c = d.cp4, w = d.cp3.weekly_A, a = c.scenario_A;
  const order = ["C-101", "C-102", "C-102-SLOT", "OTHERS", "RECALL-REPL", "C-201"];
  const names = GROUP_KO;
  const keys = order.filter((k) => w.shipments[k]);
  return [
    h("div", { class: "grid" }, tile("입찰 요청", fmt(c.bid_weekly) + " EA/주", `${c.bid_start_week}주차부터`), tile("A안 입찰 수락", a.accept_pct + "%"),
      tile("기존 고객 납기 준수", a.otif + "%", `입찰 없을 때 ${c.baseline.otif}%`),
      tile("해지 조건까지", a.kpis.min_days_to_termination_trigger != null ? a.kpis.min_days_to_termination_trigger + "일" : "-", "C-101 최소 여유")),
    section("주별 출하 (A안)", null, owner("영업"), bars({ title: "고객별 출하", labels: w.weeks, short: (l) => l.slice(5), cap: c.capacity_weekly, capLabel: "기본 능력",
      series: keys.map((k, i) => ({ label: names[k] || k, values: w.shipments[k], color: `--s${i + 1}` })) })),
    section("고객별 영향", null, null,
      table([{ label: "고객", get: (r) => h("a", { href: `#partners/${r.customer_id}` }, r.customer_id) },
        { label: "지연(입찰 없을 때)", num: 1, get: (r) => (c.baseline.customers.find((x) => x.customer_id === r.customer_id)?.max_delay_days ?? "-") + "일" },
        { label: "지연(A안)", num: 1, get: (r) => r.max_delay_days + "일" }, { label: "위약금", num: 1, get: (r) => krw(r.penalty_krw) },
        { label: "해지 위험", get: (r) => (r.termination_risk ? badge("위험", "bad", "alert") : badge("없음", "good")) }, { label: "근거 조항", get: (r) => r.evidence_clause_ids.join(", ") || "-" }], a.customers)),
  ];
}

function bid5(d) {
  const c = d.cp5;
  const fxName = { rfp: "RFP", as_of: "기준일", downside: "하락" };
  return [
    h("div", { class: "grid" }, tile("손익분기 단가 인하", `${fmt(Math.abs(c.breakeven_price_cut_pct))}%까지`, null, "공헌이익(단가 − 원료 − 리콜 기대비용)이 0이 되는 인하율. 고정비 제외."),
      tile("적용 조건", `${fxName[c.current.fx_label] || c.current.fx_label} 환율 · 인하 ${c.current.price_pressure_pct}%`)),
    section("단위 공헌이익", "환율 × 단가 인하. 원료(USD)도 환율에 연동.", owner("재무"),
      table([{ label: "환율", get: (r) => `${fxName[r.fx_label] || r.fx_label} ${fmt(r.usd_krw)}` }, { label: "인하", num: 1, get: (r) => r.price_cut_pct + "%" },
        { label: "단가", num: 1, get: (r) => fmt(r.price_krw) }, { label: "원료", num: 1, get: (r) => fmt(r.material_krw) }, { label: "리콜 기대", num: 1, get: (r) => fmt(r.recall_krw) },
        { label: "공헌/EA", num: 1, get: (r) => fmt(r.contribution_krw) }, { label: "공헌률", num: 1, get: (r) => r.margin_pct + "%" }], c.grid)),
    section("설비 투자 회수", "사용 가능량은 더마 부족분까지만 회수에 기여.", owner("재무"),
      table([{ label: "옵션", get: (r) => r.option_id }, { label: "투자", num: 1, get: (r) => krw(r.capex_krw) }, { label: "가동 시작", num: 1, get: (r) => (r.start_week == null ? "요건 회신 후" : `${r.start_week}주차`) },
        { label: "사용/월", num: 1, get: (r) => fmt(r.usable_monthly_units) }, { label: "회수(가동 후)", num: 1, get: (r) => (r.payback_months_after_start == null ? "-" : r.payback_months_after_start + "개월") },
        { label: "비고", get: (r) => r.note }], c.paybacks)),
  ];
}

function bid6(d) {
  const c = d.cp6, c7 = d.cp7, gate = c7.assumptions.some((a) => a.blocks);
  const submittable = (s) => can("submit") && !c.specs[s.id]?.reference_only && "ABC".includes(s.id) && s.id.length === 1;
  const card = (s) => h("div", { class: "card" + (c.specs[s.id]?.reference_only ? " ref" : "") },
    h("h3", {}, h("span", { class: "opt-id" }, s.id), " ", s.label.replace(/^\(참고\)\s*/, "").replace(/\s*[—·]\s*/g, ", "), c.specs[s.id]?.reference_only ? badge("참고") : null, c7.submitted === s.id ? badge("상신됨", "good") : null),
    h("dl", { class: "kv" }, h("dt", {}, "입찰 수락"), h("dd", {}, s.accept_pct + "%"), h("dt", {}, "증분 순이익"), h("dd", {}, eok(s.net_vs_baseline_eok)),
      h("dt", {}, "납기 준수"), h("dd", {}, s.otif + "%"), h("dt", {}, "추가 조치"), h("dd", {}, s.actions.join(", ") || "없음"),
      h("dt", {}, "실행"), h("dd", {}, s.feasible ? badge("가능", "good") : badge(`불가, ${fmt(s.shortfall_ea)} EA 부족`, "bad")),
      h("dt", {}, "필요 승인"), h("dd", {}, s.approvals.map((x) => x.approver).filter((v, i, a) => a.indexOf(v) === i).join(", ") || "없음")),
    submittable(s) ? h("button", { class: "primary", disabled: gate || simulating() ? true : null, title: gate ? "상신 잠김: 용량 단위 미확정" : simulating() ? "가정 조정 중에는 상신 불가" : null,
      onclick: () => act(`${s.id}안 상신`, async () => {
        const r = await api("/api/submit", { method: "POST", body: { scenario: s.id, accept_loss: false } });
        if (r.result && !r.result.submitted) throw new Error(r.result.blockers.join(" / "));
      }) }, icon("send"), "상신") : null);
  return [
    c.warnings.length ? h("div", { class: "banner warn" }, icon("alert"), h("div", { class: "grow" }, h("b", {}, `확인 필요 ${c.warnings.length}건`), h("ul", { class: "small warn-list" }, c.warnings.map((w) => h("li", {}, w.replace(/time_shift/g, "납기 양해").replace(/\s*—\s*/g, ". ").replace(/A,B/g, "A와 B")))))) : null,
    section("대안", "Z 입찰 없음(기준), A 기존 고객 보호, B 고객 동의로 납기 조정, C 입찰 전량, D 동의 없는 지연(참고용).", owner("영업임원 → CEO/이사회"),
      h("div", { class: "cards" }, [c.baseline, ...c.scenarios].map(card))),
    pareto(c.scenarios),
    section("협상 카드 우선순위", "각 카드를 받아들였다고 가정하고 다시 계산한 증분 가치. 한계선은 이익이 남는 최대 조건.", null,
      table([{ label: "#", num: 1, get: (r) => c.probes.indexOf(r) + 1 }, { label: "카드", get: (r) => r.label }, { label: "상대", get: (r) => r.counterparty },
        { label: "증분", num: 1, get: (r) => krw(r.delta_net_krw) }, { label: "수락률", num: 1, get: (r) => signed(r.delta_accept_pp, "%p") },
        { label: "한계선", get: (r) => breakevenText(r.breakeven) }, { label: "회신 소요(가정)", num: 1, get: (r) => r.answer_days + "일" },
        { label: "마감 전", get: (r) => (r.answerable_before_deadline ? badge("가능", "good") : badge("불가", "bad")) }], c.probes)),
    h("p", { class: "small muted" }, `결정론 솔버 ${c.solver.name}, ${c.solver.wall_ms}ms. 같은 입력이면 같은 결과`),
  ];
}

function whatIfPanel(d) {
  const reg = d.cp7.registry.specs, wi = { ...S.whatIf };
  const fx = h("select", {}, d.cp5.fx.map((f) => h("option", { value: f.label, selected: (wi.fx_label || d.cp5.current.fx_label) === f.label ? true : null }, `${{ rfp: "RFP", as_of: "기준일", downside: "하락" }[f.label] || f.label} ${f.usd_krw}`)));
  const pc = h("input", { type: "range", min: -30, max: 0, step: 1, value: wi.price_pressure_pct ?? d.cp5.current.price_pressure_pct, "aria-label": "단가 인하율" });
  const pcv = h("b", { class: "num" }, pc.value + "%"); pc.addEventListener("input", () => (pcv.textContent = pc.value + "%"));
  const inputs = reg.map((s) => [s, h("input", { type: "number", min: s.min_value, max: s.max_value, step: s.integer ? 1 : 0.05, value: wi[s.key] ?? s.value })]);
  const ownSpec = (() => {
    const lab = h("input", { value: "보호 + 최소 50% 수락" }), pr = h("input", { type: "checkbox", checked: true }), co = h("input", { type: "checkbox" }),
      mi = h("input", { type: "number", min: 0, max: 1, step: 0.05, value: 0.5 });
    return h("details", {}, h("summary", {}, "대안 직접 만들기"), h("label", {}, "이름", lab), h("label", {}, h("span", { class: "row" }, pr, "기존 고객 보호")),
      h("label", {}, h("span", { class: "row" }, co, "고객 동의 사용")), h("label", {}, "최소 수락률(0~1)", mi),
      h("button", { onclick: () => { const id = "EFGHIJKLMNOPQRSTUVWXY"[Object.keys(S.specs).length]; S.specs[id] = { label: lab.value, protect_existing: pr.checked, use_consent: co.checked, min_accept: Number(mi.value) };
        S.data = null; location.hash = "#analysis/6"; route(); } }, "추가해 비교"));
  })();
  return h("div", { class: "panel whatif" }, h("div", { class: "section-head" }, h("h2", {}, "가정 조정"), info("결과만 다시 계산합니다. 저장되지 않으며 확정 계획을 바꾸지 않습니다.")),
    h("label", {}, "환율", fx), h("label", {}, h("span", { class: "row" }, "단가 인하 ", pcv), pc),
    h("details", {}, h("summary", {}, "모델 파라미터"), inputs.map(([s, i]) => h("label", {}, h("span", { class: "row" }, s.label, info(`${s.basis} · 범위 ${s.min_value}~${s.max_value} ${s.unit} · 담당 ${s.editable_by.join("/")}`)), i))),
    ownSpec,
    h("div", { class: "row" }, h("button", { class: "primary", onclick: () => {
      S.whatIf = { fx_label: fx.value, price_pressure_pct: Number(pc.value) };
      inputs.forEach(([s, i]) => { if (Number(i.value) !== s.value) S.whatIf[s.key] = Number(i.value); });
      if (S.whatIf.fx_label === d.cp5.current.fx_label && S.whatIf.price_pressure_pct === d.cp5.current.price_pressure_pct && Object.keys(S.whatIf).length === 2 && !Object.keys(S.specs).length) S.whatIf = {};
      S.data = null; route();
    } }, "다시 계산"), simulating() ? h("button", { class: "ghost", onclick: () => { S.whatIf = {}; S.specs = {}; S.data = null; route(); } }, "초기화") : null));
}

// ════════════════════════════════════════════════════════════════════════════
// 업무함
const STATUS = { DRAFT: "작성", SENT: "발송", REPLIED: "회신", EXPIRED: "기한 경과", CLOSED: "종료", HYPOTHESIS: "가설", REQUESTED: "요청", ACCEPTED: "수락",
  COUNTERED: "역제안", REJECTED: "거절", APPROVED: "승인", APPLIED: "반영", PENDING: "대기", OPEN: "진행", WAITING_REPLY: "회신 대기", DONE: "완료", BLOCKED: "막힘",
  UNCONFIRMED: "미확인", PROPOSED: "제안", CONFIRMED: "확정" };
const stBadge = (s) => badge(STATUS[s] || s, ["APPROVED", "APPLIED", "ACCEPTED", "REPLIED", "DONE", "CONFIRMED"].includes(s) ? "good"
  : ["REJECTED", "EXPIRED", "BLOCKED"].includes(s) ? "bad" : ["PENDING", "COUNTERED", "SENT", "WAITING_REPLY"].includes(s) ? "warn" : "");
const OUTCOME = { ACCEPT: "수락", CONDITIONAL: "조건부", COUNTER: "역제안", REJECT: "거절" };
const TERM_LABEL = { period: "용량 기간", qty_per_month: "월 수량(EA)", lead_time_weeks: "준비 기간(주)", unit_cost_multiplier: "단가 배수", min_term_months: "최소 계약(개월)",
  weeks: "소요(주)", condition: "조건", moq_multiplier: "MOQ 배수", discount_pct: "할인율(%)", extends_grace_days: "유예 연장(일)", waives_penalty: "지체상금 면제",
  partial_award_allowed: "부분 낙찰 허용", supply_start: "공급 개시일", requirement: "요건", penalty_note: "비고" };
const DEFAULT_TERMS = { INTERNAL_OVERTIME: { qty_per_month: 30000 } };

async function pageInbox(d) {
  setTitle("업무함");
  const c = d.cp7, r = ROLE(), all = S.inboxAll || !S.me.can.length;
  const variants = await getVariants();
  const mineReq = (q) => q.to_role === r || ((r === "영업" || r === "영업임원") && ["DRAFT", "EXPIRED"].includes(q.status));
  const reqs = all ? c.requests : c.requests.filter(mineReq);
  const levers = all ? c.levers : c.levers.filter((l) => l.owner === r);
  const apprs = all ? c.approvals : c.approvals.filter((a) => a.approver === r);
  const tasks = all ? c.tasks : c.tasks.filter((t) => t.owner_role === r);
  const breakeven = (topic) => d.cp6.probes.find((x) => x.topic === topic)?.breakeven?.max_unit_cost_multiplier ?? null;

  const replyForm = (q) => {
    const base = { ...(DEFAULT_TERMS[q.topic] || (variants[q.topic] || []).find((v) => v.outcome === "ACCEPT")?.terms || {}) };
    const out = h("select", {}, Object.entries(OUTCOME).map(([k, v]) => h("option", { value: k }, v)));
    const fields = Object.entries(base).filter(([, v]) => typeof v !== "object").map(([k, v]) => {
      const inp = typeof v === "boolean" ? h("input", { type: "checkbox", checked: v ? true : null }) : k === "period"
        ? h("select", {}, h("option", { value: "per_month", selected: v === "per_month" ? true : null }, "월"), h("option", { value: "per_year", selected: v === "per_year" ? true : null }, "연"))
        : h("input", { type: typeof v === "number" ? "number" : "text", value: v, step: "any" });
      return [k, v, inp];
    });
    const memo = h("input", { placeholder: "회신 내용 요약", maxlength: 300 });
    return h("div", { class: "form-inline" }, h("div", { class: "fields" }, h("label", {}, "결과", out), fields.map(([k, , inp]) => h("label", {}, TERM_LABEL[k] || k, inp))),
      h("label", {}, "내용", memo),
      h("div", { class: "row" }, h("button", { class: "primary", onclick: () => {
        const terms = {}; for (const [k, v, inp] of fields) terms[k] = typeof v === "boolean" ? inp.checked : typeof v === "number" ? Number(inp.value) : inp.value;
        S.openReply = null;
        act("회신 저장", () => api(`/api/requests/${q.id}/reply`, { method: "POST", body: { outcome: out.value, terms: out.value === "REJECT" ? {} : terms, text: memo.value || "담당자 입력" } }));
      } }, "저장"), h("button", { class: "ghost", onclick: () => { S.openReply = null; route(); } }, "취소")));
  };
  const reqAction = (q) => {
    if (q.status === "DRAFT" && can("send")) return h("button", { onclick: () => act("발송", () => api(`/api/requests/${q.id}/send`, { method: "POST" })) }, icon("send"), "발송");
    if (q.status === "SENT" && can("reply") && (q.to_role === r || r === "영업"))
      return S.openReply === q.id ? replyForm(q) : h("button", { onclick: () => { S.openReply = q.id; route(); } }, "회신 입력");
    if (q.status === "EXPIRED" && can("send")) { const dt = h("input", { type: "date", value: d.cp7.deadline });
      return h("span", { class: "row" }, dt, h("button", { onclick: () => act("재문의", () => api(`/api/requests/${q.id}/reask`, { method: "POST", body: { answer_by: dt.value } })) }, "재문의")); }
    if (q.status === "SENT") return h("span", { class: "small muted" }, `담당 ${q.to_role}`);
    return "";
  };
  const toolbar = h("div", { class: "toolbar" },
    S.me.can.length ? h("div", { class: "seg", role: "group", "aria-label": "범위" },
      h("button", { "aria-pressed": !all ? "true" : "false", onclick: () => { S.inboxAll = false; route(); } }, "내 담당"),
      h("button", { "aria-pressed": all ? "true" : "false", onclick: () => { S.inboxAll = true; route(); } }, "전체")) : null,
    h("span", { class: "grow" }),
    can("plan_inquiries") ? h("button", { class: c.requests.length ? null : "primary", onclick: () => act("문의 계획", () => api("/api/inquiries/plan", { method: "POST" })) }, "문의 계획 만들기") : null,
    can("promote") ? h("button", { onclick: () => act("확정 계획 반영", () => api("/api/promote", { method: "POST" })) }, icon("check"), "확정 계획 반영") : null);

  const out = [toolbar,
    section("문의", "상대방 회신은 담당 역할이 입력합니다. 회신 결과는 다음 계산에 바로 반영됩니다.", null,
      table([{ label: "상대", get: (q) => h("span", {}, q.counterparty, h("div", { class: "small muted" }, q.to_role)) }, { label: "질문", get: (q) => q.question },
        { label: "상태", get: (q) => stBadge(q.status) }, { label: "기한", num: 1, get: (q) => `${md(q.answer_by)} (${q.days_left >= 0 ? "D-" + q.days_left : "D+" + -q.days_left})` },
        { label: "", get: reqAction }], reqs, { empty: all ? "문의가 없습니다" : "담당 문의가 없습니다" })),
    section("협상 카드", "가정을 바꾸는 협상 조건. 수락·승인된 카드만 계획에 반영됩니다.", null,
      table([{ label: "상대", get: (l) => l.counterparty }, { label: "종류", get: (l) => l.type }, { label: "조건", get: (l) => paramText(l.params) }, { label: "상태", get: (l) => stBadge(l.status) },
        { label: "", get: (l) => (l.status === "COUNTERED" && can("accept_counter") && (all || l.owner === r)
          ? h("button", { onclick: () => act("역제안 수락", () => api(`/api/levers/${l.id}/accept-counter`, { method: "POST", body: { breakeven_multiplier: breakeven(l.topic) } })) }, `역제안 수락 (한계 ${breakeven(l.topic) ?? "—"}배)`) : "") }],
      levers, { empty: "없음" })),
    section("승인", "승인 주체는 설정의 권한표를 따릅니다(요청 시점 버전 고정).", null,
      table([{ label: "항목", get: (a) => a.action }, { label: "승인 주체", get: (a) => a.approver }, { label: "상태", get: (a) => stBadge(a.status) },
        { label: "", get: (a) => (a.status === "PENDING" && a.approver === r && can("decide") ? h("span", { class: "row" },
          h("button", { class: "primary", onclick: () => act("승인", () => api(`/api/approvals/${a.approval_id}/decide`, { method: "POST", body: { approve: true } })) }, "승인"),
          h("button", { class: "danger", onclick: () => { const note = prompt("반려 사유"); if (note == null) return; act("반려", () => api(`/api/approvals/${a.approval_id}/decide`, { method: "POST", body: { approve: false, note } })); } }, "반려"))
          : a.status === "PENDING" ? h("span", { class: "small muted" }, `${a.approver} 결정 대기`) : "") }], apprs, { empty: c.submitted ? "없음" : "상신 전" })),
    section("작업", null, null, table([{ label: "작업", get: (t) => t.title }, { label: "담당", get: (t) => t.owner_role }, { label: "상태", get: (t) => stBadge(t.status) },
      { label: "사유", get: (t) => t.blocked_reason || "" }], tasks, { empty: "없음" })),
  ];
  if (all) out.push(section("가정", "확정 전 가정. '잠금'은 확정 전까지 상신을 막습니다.", null,
    table([{ label: "ID", get: (a) => a.assumption_id }, { label: "내용", get: (a) => a.statement }, { label: "담당", get: (a) => a.owner_role }, { label: "상태", get: (a) => stBadge(a.status) },
      { label: "", get: (a) => (a.blocks ? badge("잠금", "bad", "lock") : "") }], c.assumptions)));
  if (can("cond_fx") || can("cond_recall")) {
    const fx = h("select", {}, d.cp5.fx.map((f) => h("option", { value: f.label, selected: d.cp5.current.fx_label === f.label ? true : null }, `${f.label} ${f.usd_krw}`)));
    const cut = h("input", { type: "number", min: -30, max: 0, step: 1, value: d.cp5.current.price_pressure_pct }), rw = h("input", { type: "number", min: 0, max: 51, value: 3 });
    out.push(section("조건 변경", "확정 계획이 있으면 '재검토' 상태가 됩니다.", null, h("div", { class: "panel" },
      can("cond_fx") ? h("label", {}, "환율·단가 인하(%)", h("span", { class: "row" }, fx, cut, h("button", { onclick: () => act("조건 변경", () => api("/api/conditions", { method: "POST", body: { fx_label: fx.value, price_pressure_pct: Number(cut.value) } })) }, "반영"))) : null,
      can("cond_recall") ? h("label", {}, "품질위원회 회수 의결 → 대체 생산 시작 주", h("span", { class: "row" }, rw, h("button", { onclick: () => act("회수 의결 반영", () => api("/api/conditions", { method: "POST", body: { recall_replacement_week: Number(rw.value) } })) }, "반영"))) : null)));
  }
  return out;
}

// ════════════════════════════════════════════════════════════════════════════
// 거래처
async function pagePartners() {
  setTitle("거래처");
  const p = await getPartners();
  const q = h("input", { type: "search", placeholder: "이름·코드 검색", value: S.pq, "aria-label": "거래처 검색" });
  const body = h("div");
  const draw = () => {
    const rows = p.items.filter((x) => (S.pfilter === "전체" || x.kind === S.pfilter) && (!S.pq || `${x.id} ${x.name}`.toLowerCase().includes(S.pq.toLowerCase())));
    body.replaceChildren(table([
      { label: "거래처", get: (x) => h("span", {}, x.name, h("div", { class: "small muted" }, `${x.id} · ${x.channel}`)) },
      { label: "주문", num: 1, get: (x) => fmt(x.orders) }, { label: "누적 금액", num: 1, get: (x) => krw(x.amount_krw) }, { label: "최근 주문", num: 1, get: (x) => x.last_order || "—" },
      { label: "진행", num: 1, get: (x) => fmt(x.backlog) }, { label: "상태 불명", num: 1, get: (x) => (x.stale ? badge(fmt(x.stale), "warn") : "0") },
      { label: "클레임", num: 1, get: (x) => (x.claims_confirmed ? badge(`${x.claims_confirmed}/${x.claims}`, "bad") : fmt(x.claims)) },
      { label: "미수금", num: 1, get: (x) => krw(x.ar_outstanding_krw) },
      { label: "수주 추이", num: 1, get: (x) => (x.trend_yoy_pct == null ? "—" : h("span", { class: x.trend_yoy_pct <= -20 ? "badge bad" : "" }, signed(x.trend_yoy_pct, "%"))) },
      { label: "계약서", get: (x) => (x.contract_doc ? badge("있음", "good", "file") : h("span", { class: "muted small" }, "없음")) },
    ], rows, { onRow: (x) => (location.hash = `#partners/${x.id}`), empty: "검색 결과 없음" }));
  };
  q.addEventListener("input", () => { S.pq = q.value; draw(); });
  const seg = h("div", { class: "seg", role: "group", "aria-label": "구분" }, ["전체", "고객", "대리점"].map((k) => h("button", { "aria-pressed": S.pfilter === k ? "true" : "false",
    onclick: () => { S.pfilter = k; seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", b.textContent === k ? "true" : "false")); draw(); } }, k)));
  draw();
  return [h("div", { class: "toolbar" }, seg, h("div", { class: "search" }, icon("search"), q), h("span", { class: "grow" }),
    h("span", { class: "small muted" }, `기준일 ${p.as_of}`), info("수주 추이: 최근 3개월 수주액의 전년 동기 대비. 진행: 77일 이내 미출하. 상태 불명: 그보다 오래된 미출하. 클레임: 확정/전체.")), body];
}

async function pagePartner(d, id) {
  const x = await api(`/api/partners/${encodeURIComponent(id)}`);
  const isCust = !!x.customer_id;
  setTitle(`${x.name}`);
  const impact = isCust ? d.cp4.scenario_A.customers.find((c) => c.customer_id === x.customer_id) : null;
  const box = docViewer();
  const head = h("div", { class: "toolbar" }, h("a", { href: "#partners", class: "row" }, icon("back"), "거래처"), h("span", { class: "muted" }, `${x.customer_id || x.agency_id} · ${isCust ? x.channel : "대리점"}`));
  const tiles = h("div", { class: "grid" },
    tile("누적 주문", `${fmt(x.orders_total.orders)}건`, `${krw(x.orders_total.amount_krw)} · ${x.orders_total.first || "—"} ~ ${x.orders_total.last || "—"}`),
    tile("진행 중", `${fmt(x.backlog.orders)}건`, `${fmt(x.backlog.qty)} EA`),
    tile("상태 불명", `${fmt(x.stale.orders)}건`, x.stale.orders ? "확인 요청 대상" : null),
    isCust ? tile("리드타임", `${fmt(x.lead_time_days.median)}일`, `최대 ${fmt(x.lead_time_days.max)}일`, "수주일 → 출하일, 중앙값") : tile("리베이트", `${x.rebates.length}건`, krw(x.rebates.reduce((s, r) => s + r.rebate_krw, 0))),
    tile("미수금", krw(x.ar_outstanding_krw), isCust && x.ar_max_aging_days ? `최장 ${x.ar_max_aging_days}일` : null),
    isCust ? tile("품질 클레임", `${x.claims.filter((c) => c.confirmed).length}/${x.claims.length}`, "확정/전체") : null);

  const out = [head, tiles];
  if (isCust) {
    out.push(h("div", { class: "cols" },
      section("거래 조건", "원본 고객 마스터. 계약서 원문은 C-101·C-102만 합성본이 있습니다.", null, h("div", { class: "panel" }, h("dl", { class: "kv" },
        h("dt", {}, "결제 조건"), h("dd", {}, `${x.terms.payment_terms_days}일`), h("dt", {}, "원가 연동 조항"), h("dd", {}, x.terms.cost_linked_clause ? "있음" : "없음"),
        h("dt", {}, "매출 비중"), h("dd", {}, `${x.terms.revenue_share_pct}%`), h("dt", {}, "거래 시작"), h("dd", {}, x.terms.effective_date),
        h("dt", {}, "계약서 원문"), h("dd", {}, x.contract_doc ? badge(`${x.contract_doc} (합성)`, "good", "file") : badge("없음, 확인 필요", "warn"))))),
      section("기존 고객 영향 (분석 보호안)", "협상 분석(솔버)의 기존 고객 보호안 기준. 입찰 화면의 대안 A, B, C와는 다른 계산입니다.", h("a", { href: "#analysis/4" }, "납기 영향"), h("div", { class: "panel" }, impact ? h("dl", { class: "kv" },
        h("dt", {}, "최대 지연"), h("dd", {}, `${impact.max_delay_days}일`), h("dt", {}, "위약금"), h("dd", {}, krw(impact.penalty_krw)),
        h("dt", {}, "해지 위험"), h("dd", {}, impact.termination_risk ? badge("위험", "bad", "alert") : badge("없음", "good")))
        : h("p", { class: "empty" }, "개별 계산 대상 아님(기타 고객 합산)")))));
    const m = x.monthly;
    out.push(h("div", { class: "section" }, bars({ title: "수주·출하 매출", labels: m.map((r) => r.month), unit: "억", stacked: false, tick: 3, short: (l) => l.slice(2).replace("-", "."),
      series: [{ label: "수주", values: m.map((r) => r.booking_krw / 1e8), color: "--s1" }, { label: "출하", values: m.map((r) => r.shipment_krw / 1e8), color: "--s2" }] })));
    if (x.clauses.length) out.push(section("계약 조항", null, null, table([{ label: "조항", get: (r) => h("button", { class: "ghost", onclick: () => showDoc(r, box) }, icon("file"), r.clause) },
      { label: "종류", get: (r) => KIND[r.kind] || r.kind }, { label: "조건", get: (r) => clauseText(r.kind, r.params) }, { label: "구분", get: (r) => (r.hard ? badge("필수", "bad") : badge("조정 가능")) }], x.clauses), box));
  }
  if (x.related?.length) out.push(section("진행 중인 협의", null, null, table([{ label: "구분", get: (r) => r.kind }, { label: "내용", get: (r) => r.text }, { label: "상태", get: (r) => stBadge(r.status) },
    { label: "기한", num: 1, get: (r) => md(r.date) }], x.related)));
  out.push(section("최근 주문", "최근 30건", null, table([{ label: "주문", get: (r) => r.order_id }, { label: "수주일", num: 1, get: (r) => r.date }, { label: "제품", get: (r) => r.product_id },
    { label: "수량", num: 1, get: (r) => fmt(r.qty) }, { label: "금액", num: 1, get: (r) => krw(r.amount_krw) },
    { label: "상태", get: (r) => badge(r.status, r.status === "출하" ? "good" : r.status === "상태 불명" ? "warn" : "") }, { label: "출하일", num: 1, get: (r) => r.ship_date || "—" }], x.recent_orders)));
  if (isCust) out.push(section("품질 클레임", null, null, table([{ label: "일자", num: 1, get: (r) => r.date }, { label: "제품", get: (r) => r.product_id }, { label: "배치", get: (r) => r.batch_id },
    { label: "유형", get: (r) => r.type }, { label: "수량", num: 1, get: (r) => fmt(r.qty) }, { label: "결함", get: (r) => (r.confirmed ? badge("확정", "bad") : badge("미확정")) }, { label: "상태", get: (r) => r.status }], x.claims)));
  else out.push(section("리베이트", null, null, table([{ label: "기간", get: (r) => r.period }, { label: "실적", num: 1, get: (r) => krw(r.sales_krw) }, { label: "리베이트", num: 1, get: (r) => krw(r.rebate_krw) },
    { label: "상태", get: (r) => r.status }, { label: "지급일", num: 1, get: (r) => r.paid || "—" }], x.rebates)));
  out.push(section("미수금", null, null, table([{ label: "청구", num: 1, get: (r) => r.invoice_date }, { label: "만기", num: 1, get: (r) => r.due_date }, { label: "금액", num: 1, get: (r) => krw(r.amount_krw) },
    { label: "미수", num: 1, get: (r) => krw(r.outstanding_krw) }, { label: "경과", num: 1, get: (r) => (r.aging_days > 90 && r.outstanding_krw ? badge(r.aging_days + "일", "bad") : r.aging_days + "일") }], x.ar)));
  return out;
}

// ════════════════════════════════════════════════════════════════════════════
// 기준 정보
const CAP_LABEL = { plan_inquiries: "문의 계획", send: "문의 발송·재문의", reply: "회신 입력", accept_counter: "역제안 수락", submit: "대안 상신", accept_loss: "손실 감수 상신",
  decide: "승인 결정(지정 주체)", promote: "확정 계획 반영", cond_fx: "환율·단가 조건", cond_recall: "회수 의결 입력", registry: "파라미터 변경(지정 담당)", policy: "승인 권한표 수정",
  test_mock_reply: "모의 회신", test_clock: "업무 시계", test_reset: "검토 회차 초기화",
  w05_receive: "RFP 접수", w05_send: "확인요청 발송", w05_confirm: "회신을 가정으로 확인", w05_supersede: "가정 교체", w05_submit: "대안 상신",
  w05_submit_bid: "입찰 제출", w05_answer: "확인요청 회신", w05_decide: "승인 결정", w05_task: "작업 완료", w05_note: "대안 의견", w05_ack: "위험 표시 확인",
  demo_act_as: "역할 대리(시연)", audit_view: "감사 기록 열람", access_view: "권한표 전체 열람", registry_view_all: "파라미터 전체 열람", integrations_view: "외부 연결 상태" };
const SOLVER_ONLY = new Set(["outsourcing_contract", "customer_schedule_change", "overtime_plan", "quality_recall"]);
const POLICY_KO = { bid_participation: "입찰 참여", capex_investment: "설비투자", "price_renegotiation_C-101": "C-101 재협상, 판가", quality_recall: "품질 회수",
  outsourcing_contract: "외주 계약", customer_schedule_change: "고객 납기 변경", overtime_plan: "특근 계획" };
async function pageSettings(d) {
  setTitle("설정");
  const c = d.cp7, acc = await getAccess(), r = ROLE();
  const pa = h("select", {}, c.policy.rules.map((x) => h("option", { value: x.action }, POLICY_KO[x.action] || x.action))), pr = h("select", {}, acc.roles.filter((x) => !["심사자", "정책관리자"].includes(x)).map((x) => h("option", { value: x }, x)));
  const policy = section("승인 권한표", "승인 주체는 사람(정책관리자)만 바꿀 수 있습니다. 진행 중인 승인 요청은 요청 시점 버전을 따릅니다.", h("span", { class: "small muted" }, `v${c.policy.version}`),
    table([{ label: "항목", get: (x) => h("span", { class: "row" }, POLICY_KO[x.action] || x.action, SOLVER_ONLY.has(x.action) ? badge("분석 화면용") : null) }, { label: "승인 주체", get: (x) => x.approver }, { label: "근거", get: (x) => badge(x.provenance === "SOURCE" ? "원본 규정" : x.provenance === "HUMAN_INPUT" ? "사람 지정" : x.provenance === "ASSUMPTION" ? "가정" : x.provenance, x.provenance === "SOURCE" ? "good" : "warn") }], c.policy.rules),
    can("policy") ? h("div", { class: "panel row" }, pa, "→", pr, h("button", { class: "primary", onclick: () => { const reason = prompt("변경 사유(5자 이상)"); if (!reason) return;
      act("권한표 변경", () => api(`/api/policy/${encodeURIComponent(pa.value)}`, { method: "POST", body: { approver: pr.value, reason } })); } }, "변경")) : null);
  const params = section("모델 파라미터", "확정값 변경은 담당 역할만, 사유와 함께 기록됩니다. 미리보기는 입찰 검토의 '가정 조정'에서.", null,
    table([{ label: "항목", get: (s) => h("span", { class: "row" }, s.label, info(s.basis)) }, { label: "값", num: 1, get: (s) => `${s.value} ${s.unit}` }, { label: "범위", num: 1, get: (s) => `${s.min_value}~${s.max_value}` },
      { label: "담당", get: (s) => s.editable_by.join(", ") },
      { label: "", get: (s) => (can("registry") && s.editable_by.includes(r) ? h("button", { onclick: () => { const v = prompt(`${s.label} 새 값 (${s.min_value}~${s.max_value})`, s.value); if (v == null) return;
        const reason = prompt("변경 사유(5자 이상)"); if (!reason) return; act("파라미터 변경", () => api(`/api/registry/${s.key}`, { method: "POST", body: { value: Number(v), reason } })); } }, "변경") : "") }], can("registry_view_all") ? c.registry.specs : c.registry.specs.filter((s) => s.editable_by.includes(r)), { empty: "내가 담당하는 파라미터가 없습니다" }));
  const roles = acc.roles;
  const matrix = section("역할별 권한", "버튼 표시와 서버 검사가 같은 표를 씁니다. 승인·회신·파라미터는 지정 담당자만 추가로 허용됩니다.", null,
    table([{ label: "기능", get: (k) => h("span", { class: k.startsWith("test_") ? "badge test" : "" }, CAP_LABEL[k] || k) },
      ...roles.map((ro) => ({ label: ro, get: (k) => (acc.access[k].includes(ro) ? h("span", { class: "check", "aria-label": "허용" }, icon("check")) : h("span", { class: "dash", "aria-label": "없음" }, "-")) }))],
      Object.keys(acc.access)));
  const integ = can("integrations_view") ? await api("/api/integrations") : null;
  const MODE_KO = { mock: "모의", live: "실제", replay: "녹화 재생", file: "파일", PostgresRepository: "PostgreSQL", MemoryRepository: "메모리" };
  const integSec = integ ? section("외부 연결", (integ.items.some((it) => it.id === "llm" && it.mode === "live") ? "AI는 실제 모델 연결. 바이어·공급사 회신, ERP/MES, 외부 발송은 같은 인터페이스의 모의·파일 구현. 연결 방법은 docs/INTEGRATIONS.md" : "모의 구현을 같은 인터페이스로 끼워 둔 상태. 실제 연결 방법은 docs/INTEGRATIONS.md"), null,
    h("div", { class: "cards" }, integ.items.map((it) => h("div", { class: "card soft" }, h("h3", {}, it.name, badge(MODE_KO[it.mode] || it.mode, it.mode === "mock" ? "test" : "acc"), badge(it.status === "ok" ? "정상" : "키 없음", it.status === "ok" ? "good" : "bad")),
      h("details", {}, h("summary", {}, "세부"), h("dl", { class: "kv small" }, Object.entries(it.detail).map(([k, v]) => [h("dt", {}, k), h("dd", {}, Array.isArray(v) ? v.join(", ") : String(v))]))))))) : null;
  return [policy, params, can("access_view") ? matrix : null, integSec];
}

// ════════════════════════════════════════════════════════════════════════════
// 변경 기록
const ACTION = { open: "검토 시작", plan_inquiries: "문의 계획", send: "문의 발송", receive: "회신 입력", submit: "상신", decide: "승인 결정", promote: "확정 반영",
  advance_clock: "시계 이동", change_conditions: "조건 변경", set_parameter: "파라미터 변경", set_policy_rule: "권한표 변경", reask: "재문의", accept_counter: "역제안 수락", test_reset: "회차 초기화" };
async function pageAudit() {
  setTitle("변경 기록");
  const ev = (await api("/api/audit")).slice().reverse();
  const act_ = (a) => { const k = Object.keys(ACTION).find((x) => a === x || a.startsWith(x)); return k ? ACTION[k] + (a.length > k.length ? a.slice(k.length) : "") : a; };
  return [h("div", { class: "toolbar" }, h("span", { class: "small muted" }, `${S.me.workspace} · 최근 ${ev.length}건`), info("추가만 가능한 기록(수정·삭제 불가).")),
    table([{ label: "시각", num: 1, get: (e) => e.at.replace("T", " ").slice(0, 19) }, { label: "사용자", get: (e) => ({ system: "시스템", clock: "업무 시계" })[e.actor] || e.actor }, { label: "역할", get: (e) => e.actor_role },
      { label: "행위", get: (e) => act_(e.action) }, { label: "대상", get: (e) => e.object_ref }], ev, { empty: "기록 없음" })];
}

// ════════════════════════════════════════════════════════════════════════════
// 테스트 도구 (운영 화면과 분리)
async function pageTest(d) {
  setTitle("테스트 도구");
  const c = d.cp7, variants = await getVariants();
  const sent = c.requests.filter((q) => q.status === "SENT");
  const mock = section("모의 회신", "정책표의 가상 회신(유리·불리·무응답)을 주입합니다. 기록은 SIMULATED로 남습니다. 문의 담당 역할 또는 영업 계정만.", null,
    table([{ label: "상대", get: (q) => h("span", {}, q.counterparty, h("div", { class: "small muted" }, q.to_role)) }, { label: "기한", num: 1, get: (q) => md(q.answer_by) },
      { label: "회신", get: (q) => {
        const vs = variants[q.topic] || [];
        if (!vs.length) return h("span", { class: "small muted" }, "정책표 없음 · 업무함에서 직접 입력");
        const sel = h("select", {}, vs.map((v) => h("option", { value: v.variant }, `${OUTCOME[v.outcome] || (v.outcome === "NO_REPLY" ? "무응답" : v.outcome)} · ${v.variant}${v.favorable ? "" : " (불리)"}`)));
        const prev = h("button", { class: "info", type: "button", "aria-label": "회신 문구 보기" }, icon("info"));
        const show = () => showTip(prev, (vs.find((v) => v.variant === sel.value) || {}).text || ""); prev.addEventListener("mouseenter", show); prev.addEventListener("focus", show);
        prev.addEventListener("mouseleave", hideTip); prev.addEventListener("blur", hideTip);
        const allowed = can("test_mock_reply") && (q.to_role === ROLE() || ROLE() === "영업");
        return h("span", { class: "row" }, sel, prev, allowed ? h("button", { onclick: () => act("모의 회신", () => api(`/api/requests/${q.id}/reply`, { method: "POST", body: { variant: sel.value } })) }, "주입")
          : h("span", { class: "small muted" }, `${q.to_role}·영업 계정`));
      } }], sent, { empty: "발송된 문의가 없습니다 (업무함에서 문의 계획 → 발송)" }));
  const today = h("input", { type: "date", value: c.today });
  const clock = section("업무 시계", "날짜를 앞당겨 회신 기한 경과(무응답) 처리를 확인합니다.", null, h("div", { class: "panel row" }, `현재 ${c.today}`, today,
    can("test_clock") ? h("button", { onclick: () => act("시계 이동", () => api("/api/clock", { method: "POST", body: { today: today.value } })) }, "이동") : h("span", { class: "small muted" }, "영업·영업임원·CEO 계정")));
  const reset = section("검토 회차 초기화", "새 회차를 열어 처음부터 다시 진행합니다. 이전 회차와 기록은 지우지 않습니다.", null, h("div", { class: "panel row" }, `현재 ${S.me.workspace}`,
    can("test_reset") ? h("button", { class: "danger", onclick: () => { if (!confirm("새 검토 회차를 열까요? 모든 사용자의 화면이 새 회차로 바뀝니다.")) return;
      act("회차 초기화", async () => { const r = await api("/api/test/reset", { method: "POST" }); resetUi(r.workspace); }); } }, "새 회차 시작") : h("span", { class: "small muted" }, "영업임원·CEO·정책관리자 계정")));
  const diag = section("진단", null, null, h("div", { class: "panel" }, h("dl", { class: "kv" },
    h("dt", {}, "회차"), h("dd", {}, `${d.workspace} · v${d.version}`), h("dt", {}, "계산"), h("dd", {}, `${d.cp6.solver.name} ${d.cp6.solver.wall_ms}ms · 화면 응답 ${S.loadMs ?? "—"}ms`),
    h("dt", {}, "입력 해시"), h("dd", {}, d.cp6.input_hash.slice(0, 24) + "…"), h("dt", {}, "내 권한"), h("dd", {}, (S.me.can.map((k) => CAP_LABEL[k] || k).join(", ")) || "읽기 전용"))));
  return [h("div", { class: "banner test" }, icon("test"), h("span", { class: "grow" }, "테스트 환경 — 여기서 넣은 값은 모의 입력으로 기록됩니다")),
    h("div", { class: "test-zone" }, mock, clock, reset, diag)];
}

// ── 시작 ──────────────────────────────────────────────────────────────────────
api("/api/me").then(() => start()).catch(() => showLogin());
