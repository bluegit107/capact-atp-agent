// 입찰 건 워크스페이스. 원칙: 코드에는 이름을, 용어에는 풀이를, 숫자에는 출처를.
// 화면 문구 규칙: 짧게, 가운뎃점과 긴 줄표를 쓰지 않는다. 글이 많은 내용은 읽기 패널(.reading)로만.
"use strict";
const ACTOR_KO = { system: "시스템", clock: "업무 시계", agent: "AI", demo: "시연 진행자" };

const getW = async () => (S.w ||= await api("/api/w05"));
const SRC = { ORIGINAL: ["원본", "good"], DERIVED: ["도출", "acc"], ASSUMED: ["가정", "warn"], MOCK_REPLY: ["모의", "test"] };
const ST = { UNCONFIRMED: "미확인", CONFIRMED: "확인됨", REFUTED: "반박됨", SUPERSEDED: "교체됨", DRAFT: "초안", SENT: "발송", ANSWERED: "회신 받음", CANCELLED: "취소",
  PROPOSED: "제시", SELECTED: "선택", REJECTED: "기각", STALE: "무효", FEASIBLE: "가능", CONDITIONAL: "조건부", INFEASIBLE: "불가", PENDING: "대기", APPROVED: "승인",
  VOIDED: "무효", OPEN: "열림", IN_PROGRESS: "진행 중", DONE: "완료", BLOCKED: "막힘", TENTATIVE: "가예약", RELEASED: "해제", COMPLETED: "완료", NEEDS_HUMAN: "사람 확인",
  FAILED: "실패", RUNNING: "실행 중", INFO: "참고", WARN: "주의", BLOCK: "차단" };
const stKind = (s) => (["CONFIRMED", "FEASIBLE", "APPROVED", "DONE", "SELECTED", "COMPLETED"].includes(s) ? "good"
  : ["REFUTED", "INFEASIBLE", "REJECTED", "VOIDED", "BLOCKED", "STALE", "RELEASED", "FAILED", "BLOCK"].includes(s) ? "bad"
    : ["UNCONFIRMED", "CONDITIONAL", "PENDING", "SENT", "TENTATIVE", "NEEDS_HUMAN", "WARN", "OPEN"].includes(s) ? "warn" : "");
const stB = (s) => badge(ST[s] || s, stKind(s));
const TASK_KO = { PROPOSAL_TERMS: "제안서 공급조건 작성", CAPEX_SUBMISSION: "설비투자 상신", CAPACITY_RESERVATION: "생산 예약 확정", PROCUREMENT_RECONTRACT: "M-B 재계약 추진",
  INFO_FOLLOWUP: "확인요청 독촉", MANUAL_REVIEW: "수동 검토", RESUBMIT: "재상신 준비" };
const DEC_KO = { BID_PARTICIPATION: "입찰 참여", CAPEX: "설비투자" };
const LENS_KO = { PRODUCTION: "생산", FINANCE: "재무", SALES: "영업", PROCUREMENT: "조달", QUALITY: "품질" };
const PARTY_KO = { "C-201_BUYER": "C-201 바이어", "V-205": "V-205 원료 공급사" };
const PNAME2 = { "PL-01": "기초", "PL-02": "더마" };

const nm = (code) => { const g = S.w?.glossary?.[code]; return g ? `${code} ${g}` : code; };
function term(word, label) {
  const t = S.w?.terms?.[word]; const el = h("span", { class: t ? "term" : null, tabindex: t ? 0 : null }, label || word);
  if (t) { const show = () => showTip(el, `${word}: ${t}`); el.addEventListener("mouseenter", show); el.addEventListener("focus", show); el.addEventListener("mouseleave", hideTip); el.addEventListener("blur", hideTip); }
  return el;
}
function srcBadge(type, mid) {
  const [txt, k] = SRC[type] || [type, ""];
  return mid ? h("button", { class: `badge ${k} src`, type: "button", onclick: () => openDrawer(mid), "aria-label": `${txt} 근거 보기` }, txt) : badge(txt, k);
}
function fmtV(v, unit) {
  if (v == null) return "-";
  if (unit === "KRW") return `${fmt(v / 1e8)}억원`;
  if (unit === "pct") return `${fmt(v)}%`;
  if (unit === "EA/week") return `${fmt(v, 0)} EA/주`;
  if (unit === "EA") return `${fmt(v, 0)} EA`;
  if (unit === "years") return `${fmt(v)}년`;
  return `${v}${unit && unit !== "date" ? " " + unit : ""}`;
}
function mcell(m) {
  if (!m) return "-";
  if (m.hidden) return h("span", { class: "muted small row" }, icon("lock"), "열람 제한");
  return h("span", { class: "row num" }, fmtV(m.value, m.unit), srcBadge(m.source_type, m.id));
}
function guard(el, why) {
  if (!why) return el;
  const wrap = h("span", { class: "why", tabindex: 0 }, el); const show = () => showTip(wrap, why);
  wrap.addEventListener("mouseenter", show); wrap.addEventListener("focus", show); wrap.addEventListener("mouseleave", hideTip); wrap.addEventListener("blur", hideTip);
  return wrap;
}
function capBtn(cap, label, onclick, opts = {}) {
  const ok = can(cap) && !opts.disabledReason;
  const roles = (S.access?.access?.[cap] || []).filter((r) => r !== "시연 진행자");
  const why = opts.disabledReason || (ok ? null : `${roles.join(", ") || "지정 역할"} 권한입니다`);
  const b = h("button", { class: opts.cls || (opts.primary ? "primary" : opts.danger ? "danger" : null), disabled: ok ? null : true, onclick: ok ? onclick : null }, opts.icon ? icon(opts.icon) : null, label);
  return guard(b, ok ? null : why);
}
function roleBtn(role, label, onclick, opts = {}) {
  const why = opts.disabledReason || (ROLE() === role ? null : `${role} 권한입니다`);
  return guard(h("button", { class: opts.cls || null, disabled: why ? true : null, onclick: why ? null : onclick }, opts.icon ? icon(opts.icon) : null, label), why);
}
const post = (path, body) => api(path, { method: "POST", body: body || {} });
const aiMark = (run) => run ? h("details", {}, h("summary", {}, badge("AI 작성", "test", "ai"), h("span", { class: "small muted" }, `${ST[run.status] || run.status}, 도구 ${run.tool_calls.length}회`)),
  h("ol", { class: "small" }, run.tool_calls.map((c) => h("li", {}, `${c.name}${c.allowed === false ? " (거부)" : ""}`))), run.errors.length ? h("p", { class: "small error" }, run.errors.join(" / ")) : null) : null;

// 회차를 새로 열면 이전 회차의 화면 선택값(설비안 토글, 선택 이슈, 가정 조정, 근거 창)을 모두 비운다
function resetUi(workspace) {
  Object.assign(S, { w: null, data: null, partners: null, whatIf: {}, specs: {}, openReply: null, boardCapex: undefined, homeSel: undefined, showStale: undefined, naive: undefined });
  if (workspace) S.me.workspace = workspace;
  closeDrawer();
}
async function roleSwitchTo(role) { await post("/api/demo/act-as", { role: role || null }); S.me = await api("/api/me"); S.w = null; S.data = null; $("who").replaceChildren(h("b", {}, S.me.username), S.me.role); route(); }
function roleSwitcher() {
  const sel = h("select", { "aria-label": "역할 전환" }, ["시연 진행자", "영업", "영업임원", "생산", "구매", "품질", "재무", "CEO/이사회"].map((r) => h("option", { value: r, selected: S.me.role === r ? true : null }, r)));
  sel.addEventListener("change", () => roleSwitchTo(sel.value === "시연 진행자" ? null : sel.value).catch((e) => toast(e.message, true)));
  return h("label", { class: "chip test" }, icon("test"), sel);
}

// ── 근거 드로어(3단) ──────────────────────────────────────────────────────────
function closeDrawer() { $("drawer").classList.add("hidden"); }
async function openDrawer(mid) {
  const dr = $("drawer"); dr.classList.remove("hidden"); dr.replaceChildren(h("p", { class: "loading" }, "불러오는 중"));
  try {
    const d = await api(`/api/w05/metric/${mid}`), m = d.metric;
    const dims = Object.values(m.dims || {}).map((v) => nm(String(v))).join(", ");
    const src = d.sources.map((s) => h("div", { class: "card" }, h("div", { class: "small row" }, icon("file"), s.file),
      s.kind === "single" ? table(Object.keys(s.row_values?.[0] || {}).map((k) => ({ label: k, get: (r) => String(r[k]) })), (s.row_values || []).filter(Boolean))
        : s.kind === "doc_line" ? h("ul", { class: "small list-plain" }, Object.entries(s.lines || {}).map(([n, t]) => h("li", {}, `${n}행: ${t ?? "-"}`)))
          : h("dl", { class: "kv" }, h("dt", {}, "필터"), h("dd", {}, JSON.stringify(s.filter)), h("dt", {}, "집계"), h("dd", {}, s.agg), h("dt", {}, "행 수"), h("dd", {}, fmt(s.row_count)),
            h("dt", {}, "표본 행"), h("dd", {}, (s.sample_rows || []).join(", ") || "-"))));
    const ins = d.inputs.map((i) => i.kind === "metric"
      ? h("li", {}, h("button", { class: "ghost", onclick: () => openDrawer(i.id) }, i.key, Object.keys(i.dims || {}).length ? ` ${Object.values(i.dims).map(String).map(nm).join(", ")}` : ""), " ", i.value == null ? "열람 제한" : fmtV(i.value, i.unit), " ", srcBadge(i.source_type))
      : h("li", {}, h("div", { class: "row" }, badge(i.code || "가정", "warn"), i.label, stB(i.status), srcBadge(i.source_type)), h("div", { class: "small muted" }, `값 ${JSON.stringify(i.value)}, ${i.confirmed_by ? "확인 " + i.confirmed_by : "아직 확인 전"}`)));
    dr.replaceChildren(h("div", { class: "drawer-head" }, h("b", {}, "근거"), h("button", { class: "icon-btn", "aria-label": "닫기", onclick: closeDrawer }, icon("x"))),
      h("section", {}, h("div", { class: "small muted" }, "1 지표"), h("div", { class: "drawer-val" }, m.value == null ? "열람 제한" : fmtV(m.value, m.unit), srcBadge(m.source_type)),
        h("div", { class: "small muted" }, `${m.key}${dims ? ", " + dims : ""}`)),
      h("section", {}, h("div", { class: "small muted" }, "2 계산식과 기댄 값"), h("div", { class: "formula" }, d.formula), ins.length ? h("ul", { class: "small list-plain" }, ins) : h("p", { class: "small muted" }, "원본 값 그대로")),
      h("section", {}, h("div", { class: "small muted" }, "3 원본"), src.length ? src : h("p", { class: "small muted" }, "2의 입력을 눌러 따라가세요")));
  } catch (e) { dr.replaceChildren(h("p", { class: "error" }, e.message), h("button", { onclick: closeDrawer }, "닫기")); }
}
addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

// ── 내 할 일: 가능하면 그 자리에서 바로 처리 ────────────────────────────────────
function myItemsW(w) {
  const r = ROLE(), out = [], sales = r === "영업" || r === "영업임원";
  if (!w.bid) { if (can("w05_receive")) out.push({ t: "C-201 RFP 접수", ic: "bid", go: "#bid", run: () => post("/api/w05/receive"), runLabel: "접수", label: "RFP 접수" }); return out; }
  for (const q of w.requests) {
    const who = q.to_external ? PARTY_KO[q.to_external] : q.to_role;
    if (q.status === "DRAFT" && sales) out.push({ t: `${who}에 보낼 질의`, ic: "send", due: q.due_on, go: "#assume", run: () => post(`/api/w05/requests/${q.id}/send`), runLabel: "발송", label: "발송" });
    if (q.status === "SENT" && q.to_role === r) out.push({ t: `${q.question.slice(0, 34)}`, ic: "inbox", due: q.due_on, go: "#assume", goLabel: "회신" });
    if (q.status === "SENT" && q.to_external && r === "시연 진행자") out.push({ t: `${who} 모의 회신`, ic: "test", due: q.due_on, go: "#test", goLabel: "입력" });
    if (q.status === "ANSWERED" && q.to_external && sales && q.assumption_keys.some((k) => w.assumptions.find((a) => a.key === k && a.status === "UNCONFIRMED")))
      out.push({ t: `${who} 회신 반영`, ic: "check", go: "#assume", run: () => post(`/api/w05/requests/${q.id}/confirm`), runLabel: "가정 확인", label: "가정 확인" });
  }
  for (const n of w.notices || []) if (n.status === "OPEN" && sales) out.push({ t: `외부 통지: ${n.text.slice(0, 26)}`, ic: "alert", kind: "warn", go: "#assume", goLabel: "검토" });
  for (const a of w.approvals) if (a.status === "PENDING" && a.required_role === r)
    out.push({ t: `${DEC_KO[a.decision_type]} 결정`, ic: "check", kind: "warn", go: "#bid", run: () => post(`/api/w05/approvals/${a.id}/decide`, { decision: "APPROVED" }), runLabel: "승인", label: "승인" });
  for (const t of w.tasks) if (["OPEN", "IN_PROGRESS"].includes(t.status) && t.owner_role === r) {
    const conds = t.type === "PROPOSAL_TERMS" && (t.payload.conditions || []).length && !t.payload.conditions_acknowledged;
    out.push({ t: TASK_KO[t.type] || t.type, ic: "list", due: t.due_on, go: "#tasks", ...(conds ? {} : { run: () => post(`/api/w05/tasks/${t.id}/complete`, {}), runLabel: "완료", label: "작업 완료" }) });
  }
  if (sales && w.bid.status === "IN_REVIEW") out.push({ t: "대안 골라 상신", ic: "compare", go: "#bid", goLabel: "대안 보기" });
  if (sales && w.bid.status === "READY_TO_SUBMIT") out.push({ t: "입찰 제출(모의)", ic: "send", kind: "good", go: "#bid", run: () => post("/api/w05/submit-bid"), runLabel: "제출", label: "입찰 제출" });
  const lens = { 생산: "PRODUCTION", 재무: "FINANCE", 영업: "SALES", 구매: "PROCUREMENT", 품질: "QUALITY" }[r];
  const byRule = {};  // 같은 규칙의 표시는 대안마다 붙으므로 하나로 묶어 한 번에 확인
  for (const f of w.flags) if (f.severity === "WARN" && f.lens === lens && !f.acknowledged_by && w.options.find((o) => o.id === f.option_id && o.status === "PROPOSED")) (byRule[f.rule_key] ||= []).push(f);
  for (const [rule, fs] of Object.entries(byRule))
    out.push({ t: `${FLAG_KO[rule] || rule} 확인`, ic: "alert", kind: "warn", go: "#bid", run: async () => { for (const f of fs) await post(`/api/w05/flags/${f.id}/ack`); }, runLabel: "확인함", label: "위험 표시 확인" });
  return out;
}
function todoList(items) {
  if (!items.length) return h("p", { class: "empty" }, writeCaps().length ? "처리할 일이 없습니다" : "읽기 전용 계정");
  return h("ul", { class: "list" }, items.map((it) => h("li", { class: "todo" }, h("a", { class: "item", href: it.go || "#home", "aria-label": `${it.t} 열기` },
    h("span", { class: `ic-bub ${it.kind || ""}` }, icon(it.ic || "dot")), h("span", { class: "t" }, it.t), it.due ? h("span", { class: "d" }, md(it.due)) : null),
    it.run ? h("button", { class: "primary", onclick: () => act(it.label, it.run) }, it.runLabel) : h("a", { href: it.go }, h("button", {}, it.goLabel || "열기")))));
}
const writeCaps = () => (S.me?.can || []).filter((c) => !c.endsWith("_view") && !c.endsWith("_view_all"));
const FLAG_KO = { "RF-PROD-01": "공장 평균 대비 병목", "RF-PROD-02": "기존 주문 초과 주", "RF-PROD-03": "설비 준비와 납품 개시 간격", "RF-PROC-01": "M-B 재계약",
  "RF-FIN-01": "설비투자 규모", "RF-FIN-02": "최악 손익", "RF-FIN-03": "재료비만 반영", "RF-SALES-01": "C-101 수주 감소", "RF-SALES-02": "상태 불명 주문", "RF-QUAL-01": "더마 품질 클레임" };

const FLOW = ["RECEIVED", "WAITING_INFO", "IN_REVIEW", "PENDING_APPROVAL", "APPROVED", "READY_TO_SUBMIT", "SUBMITTED"];
const FLOW_KO = { RECEIVED: "접수", WAITING_INFO: "확인 대기", IN_REVIEW: "검토", PENDING_APPROVAL: "승인 대기", APPROVED: "승인", READY_TO_SUBMIT: "제출 준비", SUBMITTED: "제출" };
const FLOW_IC = { RECEIVED: "bid", WAITING_INFO: "inbox", IN_REVIEW: "compare", PENDING_APPROVAL: "clock", APPROVED: "check", READY_TO_SUBMIT: "list", SUBMITTED: "send" };
function statusFlow(b) {
  const cur = FLOW.indexOf(b.status);
  return h("div", { class: "flow" }, FLOW.map((s, i) => h("div", { class: "st " + (i < cur ? "done" : i === cur ? "now" : "") }, icon(i < cur ? "check" : FLOW_IC[s]), FLOW_KO[s])),
    ["REVIEW_REQUIRED", "ON_HOLD"].includes(b.status) ? h("div", { class: "st bad-st" }, icon("alert"), `${b.status_ko}${b.hold_reason ? ": " + b.hold_reason : ""}`) : null);
}
function changeBanner(w) {
  const c = w.change_banner; if (!c) return null;
  const val = (v) => (typeof v === "object" && v !== null ? JSON.stringify(v) : String(v));
  const parts = c.kept ? [`${c.label} ${val(c.before)} → ${val(c.after)}`, "결론 유지"]
    : [`${c.label} ${val(c.before)} → ${val(c.after)}`, `승인 무효 ${c.counts.voided}`, `작업 막힘 ${c.counts.blocked}`, `예약 해제 ${c.counts.released}`];
  const met = Object.entries(c.metrics || {}).filter(([, [a, b2]]) => a !== b2).map(([p, [a, b2]]) => `${PNAME2[p] || p} 충족률 ${a}% → ${b2}%`);
  return h("div", { class: "banner " + (c.kept ? "good-b" : "warn") }, icon(c.kept ? "check" : "alert"),
    h("span", { class: "grow row" }, parts.map((p, i) => (i ? badge(p, c.kept ? "good" : "warn") : h("b", {}, p))), met.map((m) => badge(m, "bad"))),
    h("a", { href: "#bid/graph" }, h("button", {}, icon("graph"), "전파 보기")));
}

// ════════════════════════════════════════════════════════════════════════════
// 홈: 디지털 트윈 + 핵심 이슈 + 바로 처리
function homeIssues(w, bd) {
  const out = [];
  // 병목: 입찰 라인 중 가동률이 가장 높은 라인(데이터 기준, 라인이 바뀌어도 그대로 동작)
  const bl = (bd.bid_lines || Object.keys(bd.line_util)).slice().sort((a, b) => bd.line_util[b] - bd.line_util[a])[0];
  const rows = bd.lines[bl] || [], over = rows.filter((r) => r.overload > 0).length, head = rows.length ? Math.max(0, rows[30].full_capacity - rows[30].baseline_load) : 0;
  const lineP = (w.lines || []).find((l) => l.line_id === bl), pname = S.w?.glossary?.[lineP?.product_id] || "";
  const need = lineP ? Math.round(lineP.annual_qty / 52) : null;
  out.push({ id: "bottleneck", kind: bd.line_util[bl] >= 95 ? "bad" : "warn", at: bl, title: `${pname} 라인 병목`, sub: `가동률 ${bd.line_util[bl]}%, 초과 주 ${over}개`,
    body: [`공장 평균 ${fmt(bd.f1_util)}%와 달리 ${bl} ${pname} 라인은 ${bd.line_util[bl]}%입니다.`, `평소 남는 능력은 주 ${fmt(head, 0)} EA뿐이고, 기존 주문만으로 능력을 넘는 주가 ${over}개 있습니다.`,
      need ? `C-201 ${pname} 요청을 52주에 나누면 주 ${fmt(need)} EA가 필요합니다.` : ""].filter(Boolean), go: "#board", goLabel: "여력 보드" });
  if (!w.bid) out.push({ id: "bid", kind: "note", at: "C-201", title: "C-201 입찰 접수 대기", sub: "기초 600만, 더마 150만 EA", body: ["해외 신규 고객의 RFP가 들어와 있습니다.", "접수하면 판단에 필요한데 없는 정보(납품 개시일 등)를 찾아 확인요청을 만듭니다."], run: () => post("/api/w05/receive"), runLabel: "RFP 접수", cap: "w05_receive" });
  else {
    const crit = w.assumptions.filter((a) => a.is_critical && a.status === "UNCONFIRMED");
    if (crit.length) out.push({ id: "gap", kind: "warn", at: crit.some((a) => a.key === "mb_recontract") ? "V-205" : "C-201", title: `핵심 정보 ${crit.length}건 미확인`, sub: crit.map((a) => a.label).join(", "),
      body: ["핵심 정보가 확인되기 전에는 상신할 수 없습니다.", ...crit.map((a) => `${a.label}: ${a.owner_role} 확인 필요`)], go: "#assume", goLabel: "확인요청" });
    const block = w.flags.find((f) => f.severity === "BLOCK" && w.options.find((o) => o.id === f.option_id && o.status === "PROPOSED"));
    if (block) out.push({ id: "block", kind: "bad", at: "V-205", title: "M-B 재계약 필요", sub: "OPT-02 대안 상신 차단", body: [block.text], go: "#assume", goLabel: "확인요청" });
    if (w.change_banner && !w.change_banner.kept) out.push({ id: "change", kind: "warn", at: "C-201", title: `${w.change_banner.label} 변경`, sub: "승인과 예약 다시 검토", body: ["전제가 바뀌어 이전 승인, 작업, 예약을 되돌렸습니다."], go: "#bid/graph", goLabel: "전파 보기" });
  }
  out.push({ id: "c101", kind: "warn", at: "CUST", title: "C-101 수주 감소", sub: "최근 3개월 전년 대비 감소", body: ["최대 고객 C-101의 신규 수주가 3개월 연속 줄었습니다.", "원인이 확인되지 않아 여력으로 계산하지 않습니다. 영업 임원 확인 대상입니다."], go: "#partners/C-101", goLabel: "거래처 이력" });
  out.push({ id: "unknown", kind: "note", at: "CUST", title: "상태 불명 주문", sub: `${fmt(bd.unknown_total.orders)}건 확인 필요`, body: ["최대 리드타임 77일을 넘겼는데 출하 기록이 없는 주문입니다.", "취소인지 미납인지 확인 전까지 부하에서 빼고 위험 물량으로만 표시합니다."], go: "#assume", goLabel: "확인요청" });
  return out.slice(0, 4);
}

function iso(x, y, z, o = { x: 0, y: 0, s: 46 }) { const c = 0.866, s = 0.5; return [o.x + (x - y) * c * o.s, o.y + (x + y) * s * o.s - z * o.s]; }
const P = (pts) => pts.map((p) => p.map((v) => Math.round(v * 10) / 10).join(",")).join(" ");
function box(x, y, wdt, dep, ht, z0 = 0) {
  const a = iso(x, y, ht), b = iso(x + wdt, y, ht), c = iso(x + wdt, y + dep, ht), d = iso(x, y + dep, ht), b0 = iso(x + wdt, y, z0), c0 = iso(x + wdt, y + dep, z0), d0 = iso(x, y + dep, z0);
  return [h("polygon", { class: "left", points: P([d, c, c0, d0]) }), h("polygon", { class: "right", points: P([c, b, b0, c0]) }), h("polygon", { class: "top", points: P([a, b, c, d]) })];
}
// 높이 강조: 대부분 70% 이상이라 60% 아래는 잘라 내고(≈ 표시), 60~100% 구간을 크게 보여 준다
const UTIL_FLOOR = 60;
const utilHeight = (u) => 0.55 + Math.max(0.12, (u - UTIL_FLOOR) / (100 - UTIL_FLOOR)) * 2.9;
function breakMark(x, y, wdt, dep, z) {
  const pts = [], n = 6;
  for (let i = 0; i <= n; i++) { const t = i / n; pts.push(iso(x + wdt * t, y + dep, z + (i % 2 ? 0.07 : -0.07))); }
  for (let i = 1; i <= n; i++) { const t = i / n; pts.push(iso(x + wdt, y + dep - dep * t, z + (i % 2 ? -0.07 : 0.07))); }
  return h("polyline", { class: "brk", points: P(pts) });
}
function cylinder(cx, cy, r, ht) { // 화면 좌표 원통(원료 탱크)
  const ry = r * 0.45;
  return [h("path", { class: "tank-side", d: `M${cx - r} ${cy} L${cx - r} ${cy - ht} A ${r} ${ry} 0 0 0 ${cx + r} ${cy - ht} L${cx + r} ${cy} A ${r} ${ry} 0 0 1 ${cx - r} ${cy} Z` }),
    h("ellipse", { class: "tank-top", cx, cy: cy - ht, rx: r, ry })];
}
function digitalTwin(w, bd, ov, issues, state) {
  const floors = h("g"), flows = h("g"), objs = h("g"), labels = h("g"), top = h("g"), pts = [];
  const track = (...ps) => ps.forEach((p) => pts.push(p));
  const plants = [...new Set(ov.lines.map((l) => l.plant))];
  const util = Object.fromEntries(ov.lines.map((l) => [l.line_id, l.utilization_pct]));
  const pos = {}, base = {};
  const optSel = (w.options || []).find((o) => o.id === w.bid?.selected_option_id) || (w.options || []).find((o) => o.status === "PROPOSED" && o.label === "B");
  const specs = [];
  plants.forEach((pl, k) => {
    const lines = ov.lines.filter((l) => l.plant === pl), bx = 4 * k, by = -5.4 * k + 0.1;
    const x0 = bx - 0.7, x1 = bx + lines.length * 2.6 - 0.6, y0 = by - 0.7, y1 = by + 3.1;
    const fl = [iso(x0, y0, 0), iso(x1, y0, 0), iso(x1, y1, 0), iso(x0, y1, 0)]; track(...fl);
    floors.append(h("polygon", { class: "floor", points: P(fl) }), h("polygon", { class: "floor-side", points: P([iso(x0, y1, 0), iso(x1, y1, 0), iso(x1, y1, -0.3), iso(x0, y1, -0.3)]) }));
    const lp = iso(x1, y1, 0);
    floors.append(h("text", { class: "plant-label", x: lp[0] - 6, y: lp[1] + 30, "text-anchor": "end" }, `${pl} 공장`));
    lines.forEach((l, i) => specs.push({ id: l.line_id, x: bx + i * 2.6, y: by, u: util[l.line_id] ?? 0, name: ov.products[l.product_id] || l.product_id }));
  });
  for (const o of optSel?.capex_ids || []) { // 선택 대안의 설비안: 대상 라인 앞에 반투명 계획 블록
    const tgt = bd.capex_lines?.[o], plant = (tgt || "").split("-")[0];
    const same = specs.filter((s) => ov.lines.find((l) => l.line_id === s.id)?.plant === plant);  // 신설 라인은 같은 공장 가운데 앞쪽
    const host = specs.find((s) => s.id === tgt) || same[Math.floor(same.length / 2)];
    if (host) specs.push({ id: tgt || o, x: host.x, y: host.y + 1.85, u: null, plan: o, name: tgt && !specs.some((s) => s.id === tgt) ? "신설" : "증설" });
  }
  specs.sort((a, b) => a.x + a.y - (b.x + b.y));
  for (const s of specs) {
    const ht = s.plan ? 0.9 : utilHeight(s.u), cls = s.plan ? "plan" : s.u >= 95 ? "hot" : s.u >= 85 ? "warn" : "";
    const g = h("g", { class: `blk ${cls}${state.sel === s.id ? " sel" : ""}`, tabindex: 0, role: "button", "aria-label": `${s.id} ${s.name} ${s.plan ? "계획" : s.u + "%"}` }, box(s.x, s.y, 1.3, 1.5, ht));
    if (!s.plan) g.append(breakMark(s.x, s.y, 1.3, 1.5, 0.32));
    g.addEventListener("click", () => state.pick({ line: s.plan ? s.plan : s.id }));
    g.addEventListener("keydown", (e) => { if (e.key === "Enter") state.pick({ line: s.plan ? s.plan : s.id }); });
    objs.append(g);
    const t = iso(s.x + 0.65, s.y + 0.75, ht); pos[s.id] = t; base[s.id] = iso(s.x + 0.65, s.y + 0.75, 0.3); track(iso(s.x, s.y, ht), iso(s.x + 1.3, s.y + 1.5, 0));
    labels.append(h("text", { class: "halo lbl", x: t[0], y: t[1] - 30, "text-anchor": "middle" }, s.plan ? `${s.plan} ${s.name}` : `${s.id} ${s.name}`),
      h("text", { class: `halo pct ${cls}`, x: t[0], y: t[1] - 12, "text-anchor": "middle" }, s.plan ? "계획" : `${s.u}%`));
  }
  // 외부 주체: 이름표가 아니라 사물(컨테이너=입찰, 건물=고객, 탱크=원료 공급)
  const first = specs.filter((s) => !s.plan).sort((a, b) => a.x - b.x)[0] || { x: 0, y: 0 };
  const bidAt = [first.x - 4.4, first.y - 1.2], custAt = [first.x - 2.6, first.y + 4.0];
  const bidG = h("g", { class: "obj bid" }, box(bidAt[0], bidAt[1], 1.6, 0.9, 0.8), box(bidAt[0] + 0.15, bidAt[1] + 0.1, 1.3, 0.7, 1.5, 0.8));
  const bc = iso(bidAt[0] + 0.8, bidAt[1] + 0.45, 1.5); pos["C-201"] = bc; track(iso(bidAt[0], bidAt[1], 1.6), iso(bidAt[0] + 1.6, bidAt[1] + 0.9, 0));
  const custG = h("g", { class: "obj cust" }, box(custAt[0], custAt[1], 0.9, 0.9, 0.9), box(custAt[0] + 1.1, custAt[1] + 0.2, 0.7, 0.7, 0.6), box(custAt[0] + 0.3, custAt[1] + 1.2, 0.7, 0.6, 0.5));
  const cc = iso(custAt[0] + 0.8, custAt[1] + 0.8, 0.9); pos["CUST"] = cc; track(iso(custAt[0], custAt[1], 1), iso(custAt[0] + 1.8, custAt[1] + 1.8, 0));
  const derma = specs.filter((s) => (w.lines || []).some((l) => l.line_id === s.id) || s.plan).map((s) => s.id);
  const lastPlantSpecs = specs.filter((s) => !s.plan && s.y === Math.min(...specs.map((q) => q.y)));
  const tankBase = iso(Math.max(...lastPlantSpecs.map((s) => s.x)) + 2.6, Math.min(...lastPlantSpecs.map((s) => s.y)) + 0.6, 0);
  const tankG = h("g", { class: "obj tank" }, cylinder(tankBase[0], tankBase[1], 26, 56)); pos["V-205"] = [tankBase[0], tankBase[1] - 56]; track([tankBase[0] - 30, tankBase[1] - 70], [tankBase[0] + 30, tankBase[1] + 14]);
  objs.prepend(bidG, custG); objs.append(tankG);
  const objLabel = (p, t1, t2, dy = 22) => labels.append(h("text", { class: "halo lbl", x: p[0], y: p[1] + dy, "text-anchor": "middle" }, t1), h("text", { class: "halo sub", x: p[0], y: p[1] + dy + 15, "text-anchor": "middle" }, t2));
  objLabel(iso(bidAt[0] + 0.8, bidAt[1] + 0.9, 0), "C-201 입찰", w.bid ? `${w.bid.status_ko}, D-${w.bid.days_left}` : "접수 대기");
  objLabel(iso(custAt[0] + 0.9, custAt[1] + 1.8, 0), "기존 고객", "진행 중 주문 1,216건");
  objLabel(tankBase, "V-205 M-B 원료", "독점 공급", 30);
  // 흐름: 입찰 수요(파랑), 고객 납품(빨강이면 위험), 원료 공급(초록). 화살표 끝은 라인 바닥
  const arrow = (a, b, cls) => { if (!a || !b) return; const mx = (a[0] + b[0]) / 2, my = Math.min(a[1], b[1]) - 36;
    flows.append(h("path", { class: `flowline ${cls}`, d: `M${a[0]} ${a[1]} Q ${mx} ${my} ${b[0]} ${b[1]}`, "marker-end": `url(#ar-${cls})` })); };
  const bidLines = (w.lines || []).map((l) => l.line_id);
  for (const ln of bidLines) arrow(pos["C-201"], base[ln], util[ln] >= 95 ? "risk" : "demand");
  for (const ln of bidLines) arrow(base[ln], pos["CUST"], util[ln] >= 95 ? "risk" : "deliver");
  for (const ln of derma.filter((x) => (bd.line_product || {})[x] === "PL-02" || ov.lines.find((l) => l.line_id === x)?.product_id === "PL-02")) arrow(pos["V-205"], base[ln], "supply");
  issues.forEach((it, i) => { const p = pos[it.at] || pos["L-02"]; if (!p) return; const n = issues.slice(0, i).filter((x) => x.at === it.at).length;
    const cx = p[0] + 34 + n * 28, cy = p[1] - 46;
    const g = h("g", { class: `issue ${it.kind}`, tabindex: 0, role: "button", "aria-label": it.title }, h("circle", { cx, cy, r: 12 }), h("text", { x: cx, y: cy + 4, "text-anchor": "middle" }, i + 1));
    g.addEventListener("click", () => state.pick({ issue: it.id })); top.append(g); track([cx - 14, cy - 14], [cx + 14, cy + 14]); });
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const vb = [Math.min(...xs) - 70, Math.min(...ys) - 40, Math.max(...xs) - Math.min(...xs) + 140, Math.max(...ys) - Math.min(...ys) + 90];
  const mk = (id) => h("marker", { id: `ar-${id}`, viewBox: "0 0 10 10", refX: 8, refY: 5, markerWidth: 6, markerHeight: 6, orient: "auto-start-reverse" }, h("path", { d: "M0 0 L10 5 L0 10 z", class: `ar ${id}` }));
  const svg = h("svg", { class: "twin", viewBox: vb.map((v) => Math.round(v)).join(" "), role: "img", "aria-label": "생산 라인 디지털 트윈" },
    h("defs", {}, ["risk", "demand", "deliver", "supply"].map(mk)), floors, flows, objs, labels, top);
  const kpi = h("div", { class: "twin-kpi" }, [["생산(6월)", `${fmt(Object.keys(ov.products).reduce((s, p) => s + (ov.production_monthly.at(-1)[p] || 0), 0) / 1e6, 2)}M EA`],
    ["진행 주문", "1,216건"], ["병목", `${ov.lines.filter((l) => l.utilization_pct >= 95).map((l) => l.line_id).join(", ") || "없음"}`], ["마감", w.bid ? `D-${w.bid.days_left}` : "접수 전"]]
    .map(([k, v]) => h("span", {}, h("small", {}, k), h("b", {}, v))));
  const lg = h("div", { class: "twin-legend" }, [["var(--bad)", "95% 이상"], ["#d39a2b", "85% 이상"], ["var(--accent)", "여유"], ["var(--accent-2)", "입찰 수요"], ["var(--s3)", "원료 공급"]]
    .map(([c, t]) => { const i = h("i"); i.style.background = c; return h("span", {}, i, t); }), h("span", {}, "높이 60% 이상 확대 ≈"));
  return h("div", { class: "twin-wrap" }, kpi, svg, lg);
}

async function pageHomeW(w) {
  setTitle("홈");
  const [ov, bd] = await Promise.all([getOverview(), api("/api/w05/board")]);
  const issues = homeIssues(w, bd), items = myItemsW(w);
  const state = { sel: S.homeSel && (issues.some((i) => i.id === S.homeSel) || bd.line_util[S.homeSel] || ov.lines.some((l) => l.line_id === S.homeSel)) ? S.homeSel : issues[0]?.id };
  const detail = h("div", { class: "reading" }), twinBox = h("div"), listBox = h("div", { class: "issue-list" });
  const cx0 = (lid) => (bd.capex_info?.[lid] ? `${lid} ${bd.capex_info[lid].new_line ? "신설안" : "증설안"}` : nm(lid));
  const renderDetail = () => {
    const it = issues.find((x) => x.id === state.sel);
    if (it) {
      detail.replaceChildren(h("h3", {}, h("span", { class: `badge ${it.kind === "note" ? "acc" : it.kind}` }, `이슈 ${issues.indexOf(it) + 1}`), it.title), ...it.body.map((p) => h("p", {}, p)),
        h("div", { class: "act-row" }, it.run ? capBtn(it.cap, it.runLabel, () => act(it.runLabel, it.run), { primary: true }) : null, it.go ? h("a", { href: it.go }, h("button", {}, it.goLabel)) : null));
      return;
    }
    const lid = state.sel, rows = bd.lines[lid], meta = ov.lines.find((l) => l.line_id === lid), pid = meta?.product_id, pm = ov.production_monthly.slice(-6);
    const body = [h("h3", {}, cx0(lid), meta ? badge(`${meta.utilization_pct}%`, meta.utilization_pct >= 95 ? "bad" : meta.utilization_pct >= 85 ? "warn" : "acc") : badge("계획", "acc"))];
    if (rows && lid !== "F2-DERMA-NEW") body.push(h("div", { class: "grid" }, tile("주간 최대", fmt(rows[0].full_capacity, 0), "EA"), tile("평소 부하", fmt(rows[30].baseline_load, 0), "EA"), tile("초과 주", `${rows.filter((r) => r.overload > 0).length}개`)));
    if (pid) body.push(bars({ title: `${ov.products[pid]} 월 생산`, labels: pm.map((m) => m.month), tick: 1, short: (l) => `${Number(l.slice(5))}월`, series: [{ label: ov.products[pid], values: pm.map((m) => m[pid]), color: "--s1" }] }));
    if (meta && !rows) body.push(h("p", {}, "이번 입찰과 직접 관련 없는 라인입니다."));
    const cx = bd.capex_info?.[lid];
    if (cx) body.push(h("div", { class: "grid" }, tile("투자", `${fmt(cx.capex_krw / 1e8)}억원`), tile("준비", `${cx.lead_time_weeks}주`), tile("추가 능력", `연 ${fmt(cx.added_capacity_qty / 10000)}만 EA`)),
      h("p", {}, `${cx.new_line ? "새 라인" : cx.line_id + " 증설"}${cx.requires.length ? `, 선행 요건: ${cx.requires.join(", ")}` : ", 선행 요건 없음"}`));
    body.push(h("div", { class: "act-row" }, h("a", { href: "#board" }, h("button", {}, icon("grid"), "주별 여력"))));
    detail.replaceChildren(...body);
  };
  const renderList = () => listBox.replaceChildren(...issues.map((it, i) => h("div", { class: `issue-card ${it.kind}${state.sel === it.id ? " sel" : ""}`, tabindex: 0, role: "button",
    onclick: () => state.pick({ issue: it.id }), onkeydown: (e) => { if (e.key === "Enter") state.pick({ issue: it.id }); } }, h("span", { class: "no" }, i + 1), h("div", {}, h("b", {}, it.title), h("span", {}, it.sub)), icon("chev"))));
  state.pick = ({ line, issue }) => { state.sel = line || issue; S.homeSel = state.sel; twinBox.replaceChildren(digitalTwin(w, bd, ov, issues, state)); renderList(); renderDetail(); };
  twinBox.replaceChildren(digitalTwin(w, bd, ov, issues, state)); renderList(); renderDetail();
  const cnt = { assume: (w.requests || []).filter((q) => ["DRAFT", "SENT", "ANSWERED"].includes(q.status)).length, opts: (w.options || []).filter((o) => o.status === "PROPOSED").length,
    appr: (w.approvals || []).filter((a) => a.status === "PENDING").length, task: (w.tasks || []).filter((t) => ["OPEN", "IN_PROGRESS"].includes(t.status)).length };
  const qa = h("div", { class: "qa" }, [["#bid", "compare", "대안과 승인", cnt.opts || cnt.appr ? `${cnt.opts + cnt.appr}` : ""], ["#assume", "inbox", "확인요청", cnt.assume ? `${cnt.assume}` : ""],
    ["#tasks", "list", "작업", cnt.task ? `${cnt.task}` : ""], ["#board", "grid", "여력 보드", ""], ["#partners", "partners", "거래처", ""], ["#history", "audit", "이력", ""]]
    .map(([href, ic, t, c]) => h("a", { href }, h("span", { class: "ic-bub" }, icon(ic)), t, c ? h("span", { class: "cnt" }, c) : null)));
  return [w.bid ? statusFlow(w.bid) : null, changeBanner(w),
    h("div", { class: "cols" }, h("div", {}, twinBox, qa),
      h("div", {}, h("div", { class: "section" }, h("div", { class: "section-head" }, h("h2", {}, "핵심 이슈")), listBox), detail,
        h("div", { class: "section" }, h("div", { class: "section-head" }, h("h2", {}, "내 할 일"), items.length ? badge(`${items.length}`, "bad") : null), todoList(items))))];
}

// ════════════════════════════════════════════════════════════════════════════
// 입찰: 진행 상태 + 대안 비교 + 승인을 한 화면에
async function pageBidW(w, arg) {
  setTitle("입찰 C-201");
  if (!w.bid) return [h("div", { class: "card empty-state" }, h("h3", {}, "접수된 입찰이 없습니다"), h("p", { class: "muted" }, "C-201 해외 신규 고객, 기초와 더마"),
    capBtn("w05_receive", "RFP 접수", () => act("RFP 접수", () => post("/api/w05/receive")), { primary: true }))];
  const view = arg === "graph" ? "graph" : arg === "summary" ? "summary" : "options";
  const tabs = h("nav", { class: "steps" }, [["options", "대안과 승인", "compare"], ["graph", "전파 그래프", "graph"], ["summary", "요약", "file"]]
    .map(([k, t, ic]) => h("a", { href: `#bid/${k}`, "aria-current": view === k ? "page" : null }, icon(ic), t)));
  const b = w.bid;
  const head = [statusFlow(b), changeBanner(w),
    h("div", { class: "grid" }, tile("제출 마감", `D-${b.days_left}`, b.deadline), tile("결제", b.terms.currency, `페널티 ${b.terms.penalty}`), tile("리콜 비용", b.terms.recall),
      tile("선택 대안", b.selected_option_id ? `대안 ${w.options.find((o) => o.id === b.selected_option_id)?.label}` : "없음")),
    b.status === "READY_TO_SUBMIT" ? h("div", { class: "banner good-b" }, icon("check"), h("span", { class: "grow" }, "제출 준비가 끝났습니다"), capBtn("w05_submit_bid", "입찰 제출", () => act("입찰 제출", () => post("/api/w05/submit-bid")), { primary: true, icon: "send" })) : null,
    b.status === "ON_HOLD" ? h("div", { class: "banner bad" }, icon("alert"), h("span", { class: "grow" }, "참여 보류"), capBtn("w05_receive", "새로 접수", () => act("RFP 접수", () => post("/api/w05/receive")))) : null,
    tabs];
  if (view === "graph") return [...head, propagationGraph(w)];
  if (view === "summary") return [...head, summaryPanel(w)];
  return [...head, ...optionsPanel(w), ...approvalsPanel(w)];
}
const pageOptions = (w) => pageBidW(w, "options");
const pageApprovals = (w) => pageBidW(w, "options");

function summaryPanel(w) {
  const ag = w.agent, out = ag?.output;
  return h("div", { class: "cols" },
    h("div", {}, h("div", { class: "reading" }, h("h3", {}, badge("AI 작성", "test", "ai"), "상황 요약"),
      out ? [h("p", {}, out.situation_summary), out.ai_opinion ? h("p", {}, h("b", {}, "AI 의견 "), out.ai_opinion.reasoning) : null,
        out.impact_of_change ? h("p", {}, h("b", {}, "변경 영향 "), out.impact_of_change.text) : null,
        h("ul", { class: "list-plain" }, (out.next_actions || []).map((a) => h("li", {}, `${a.actor_role}: ${a.action}`))),
        (out.open_risks || []).length ? h("p", { class: "small muted" }, `확인 필요: ${out.open_risks.join(" / ")}`) : null]
        : h("p", {}, ag ? "AI 결과 없음. 사람이 그대로 진행합니다." : "AI 실행 전"), aiMark(ag), h("p", { class: "small muted" }, "선택은 승인권자가 합니다."))),
    h("div", {}, section("입찰 품목", null, null, table([{ label: "품목", get: (l) => nm(l.product_id) }, { label: "연 물량", num: 1, get: (l) => `${fmt(l.annual_qty)} EA` },
      { label: "단가", num: 1, get: (l) => (l.unit_price_usd == null ? h("span", { class: "muted small row" }, icon("lock"), "제한") : `${l.unit_price_usd} USD`) }, { label: "", get: () => srcBadge("ORIGINAL") }], w.lines))));
}

function optionsPanel(w) {
  S.showStale ??= false; S.naive ??= false;
  const live = w.options.filter((o) => (S.showStale ? true : ["PROPOSED", "SELECTED"].includes(o.status)));
  if (!live.length) return [h("div", { class: "card empty-state" }, h("h3", {}, "대안 준비 중"), h("p", { class: "muted" }, "납품 개시일과 부분 물량 허용이 확인되면 자동으로 계산됩니다"),
    h("a", { href: "#assume" }, h("button", { class: "primary" }, "확인요청 보기")))];
  const rows = [
    ["판정", (o) => h("span", { class: "row" }, stB(o.feasibility), o.status !== "PROPOSED" ? stB(o.status) : null)],
    ["제안 물량", (o) => h("div", {}, Object.entries(o.offered).map(([p, q]) => h("div", { class: "num" }, `${PNAME2[p]} ${fmt(q / 10000)}만 EA`)))],
    ["설비안", (o) => (o.capex_ids.length ? o.capex_ids.join(", ") : "없음")],
    [term("충족률"), (o) => h("div", {}, Object.keys(o.offered).map((p) => h("div", { class: "row" }, h("span", {}, PNAME2[p]), mcell(o.metrics[`fill_${p}`]), S.naive ? badge(o.naive_annual[p] ? "총량상 가능" : "총량상 불가", o.naive_annual[p] ? "" : "bad") : null)))],
    ["전량 납품 시작", (o) => h("div", {}, Object.keys(o.offered).map((p) => h("div", { class: "row" }, h("span", {}, PNAME2[p]), o.metrics[`first_full_${p}`] ? mcell(o.metrics[`first_full_${p}`]) : "-")))],
    [term("페널티", "페널티 노출"), (o) => mcell(o.metrics.penalty)],
    ["매출", (o) => mcell(o.metrics.revenue)],
    [term("재료비 차감 이익"), (o) => mcell(o.metrics.margin)],
    ["설비 회수", (o) => (o.metrics.payback ? mcell(o.metrics.payback) : "-")],
    ["막는 조건", (o) => (o.blocking_reasons.length ? h("div", { class: "small" }, o.blocking_reasons.map((x) => h("div", {}, x))) : "없음")],
  ];
  const head = h("tr", {}, h("th", {}, ""), live.map((o) => h("th", { class: o.unconfirmed_critical.length ? "unconf" : null }, h("div", { class: "row" }, h("b", {}, `대안 ${o.label}`), o.unconfirmed_critical.length ? badge("핵심 미확인", "warn") : null),
    h("div", { class: "small muted" }, o.title))));
  const submitBtn = (o) => {
    const block = o.flags.find((f) => f.severity === "BLOCK");
    const why = o.status !== "PROPOSED" ? "제시 상태가 아닙니다" : w.bid.status !== "IN_REVIEW" ? `지금은 ${w.bid.status_ko} 단계입니다` : block ? `${block.rule_key} 차단: ${block.text}` : o.feasibility === "INFEASIBLE" ? "불가 판정입니다" : o.unconfirmed_critical.length ? `미확인: ${o.unconfirmed_critical.join(", ")}` : null;
    return capBtn("w05_submit", "상신", () => act("상신", () => post("/api/w05/submit", { option_id: o.id })), { primary: true, icon: "send", disabledReason: why });
  };
  const body = rows.map(([lab, get]) => h("tr", {}, h("th", { class: "small" }, lab), live.map((o) => h("td", { class: o.unconfirmed_critical.length ? "unconf" : null }, get(o)))));
  // 모든 대안에 같은 문구로 붙은 표시는 한 번만(공통), 나머지는 대안별로
  const fkey = (f) => `${f.rule_key}|${f.severity}|${f.text}`;
  const shared = new Set(live.length > 1 ? [...new Set(live.flatMap((o) => o.flags.map(fkey)))].filter((k) => live.every((o) => o.flags.some((f) => fkey(f) === k))) : []);
  const flagRows = (groups, title) => {
    const by = {}; for (const g of groups) (by[g[0].lens] ||= []).push(g);
    return h("div", { class: "card" }, h("h3", {}, title),
      Object.entries(by).map(([lens, gs]) => h("div", { class: "flag-group" }, h("b", { class: "small" }, LENS_KO[lens]),
        gs.map((fs) => { const f = fs[0], done = fs.every((x) => x.acknowledged_by);
          return h("div", { class: "flag" }, badge(ST[f.severity], stKind(f.severity)), h("span", { class: "small grow" }, f.text), f.explained_by_agent ? badge("AI", "test", "ai") : null,
            f.severity === "WARN" ? (done ? badge(`확인 ${f.acknowledged_by}`, "good") : capBtn("w05_ack", "확인함", () => act("확인", async () => { for (const x of fs) if (!x.acknowledged_by) await post(`/api/w05/flags/${x.id}/ack`); }))) : null); }))));
  };
  const sharedPanel = shared.size ? flagRows([...shared].map((k) => live.flatMap((o) => o.flags.filter((f) => fkey(f) === k))), "모든 대안 공통 확인") : null;
  const flagPanel = (o) => { const own = o.flags.filter((f) => !shared.has(fkey(f)));
    return own.length ? flagRows(own.map((f) => [f]), `대안 ${o.label}만 해당`) : null; };
  const grid = (o) => { const g = ["0", "5", "10"], cell = (c, d) => o.metrics[`grid_${c}/${d}`];
    if (!cell("0", "0") || cell("0", "0").hidden) return null;
    return h("div", { class: "card" }, h("h3", {}, `대안 ${o.label} 재료비 차감 이익`, info("단가 인하율과 환율 하락률 조합")),
      h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "인하 \\ 환율"), g.map((d) => h("th", { class: "num" }, `-${d}%`)))), h("tbody", {}, g.map((c) => h("tr", {}, h("th", {}, `-${c}%`), g.map((d) => h("td", { class: "num" }, mcell(cell(c, d))))))))));
  };
  const note = (o) => (can("w05_note") ? (() => { const t = h("input", { placeholder: "재무 의견", maxlength: 500 }); return h("div", { class: "row" }, t, capBtn("w05_note", "첨부", () => act("의견 첨부", () => post(`/api/w05/options/${o.id}/note`, { text: t.value })))); })() : null);
  const opinion = w.agent?.output?.ai_opinion;
  return [h("div", { class: "toolbar" }, h("label", { class: "inline small" }, h("input", { type: "checkbox", checked: S.naive ? true : null, onchange: (e) => { S.naive = e.target.checked; route(); } }), "연간 총량 비교 같이 보기"),
    h("label", { class: "inline small" }, h("input", { type: "checkbox", checked: S.showStale ? true : null, onchange: (e) => { S.showStale = e.target.checked; route(); } }), "무효 대안 포함"),
    info("총량 비교는 연 여력과 설비 추가 능력 합만 봅니다. 원장은 설비 준비 주, 납품 개시 주, 주문이 몰린 주를 주 단위로 대조합니다.")),
    h("div", { class: "table-wrap" }, h("table", { class: "compare" }, h("thead", {}, head), h("tbody", {}, body, h("tr", {}, h("th", {}, ""), live.map((o) => h("td", {}, h("div", { class: "row" }, submitBtn(o)), note(o))))))),
    opinion ? h("div", { class: "card accent" }, h("h3", {}, badge("AI 의견", "test", "ai"), `대안 ${w.options.find((o) => o.id === opinion.preferred_option_id)?.label ?? "없음"}`), h("p", { class: "small" }, opinion.reasoning)) : null,
    sharedPanel, h("div", { class: "cards" }, live.map(flagPanel).filter(Boolean)), h("details", {}, h("summary", {}, "재료비 차감 이익 9칸 보기"), h("div", { class: "cards" }, live.map(grid).filter(Boolean))),
    (w.notes || []).length ? h("div", { class: "card" }, h("h3", {}, "재무 의견"), w.notes.map((n) => h("p", { class: "small" }, `대안 ${w.options.find((o) => o.id === n.option_id)?.label}: ${n.text}`))) : null];
}

function approvalsPanel(w) {
  if (!w.approvals.length) return [];
  const r = ROLE(), proc = w.requests.find((q) => q.assumption_keys.includes("mb_recontract"));
  const card = (a) => {
    const o = w.options.find((x) => x.id === a.option_id), t = h("input", { placeholder: "조건 또는 거절 사유", maxlength: 500 });
    const pend = a.status === "PENDING", dis = !pend ? "결정 완료" : a.required_role !== r ? `${a.required_role} 권한입니다` : null;
    const decide = (dec) => () => { if (dec !== "APPROVED" && !t.value.trim()) return toast(dec === "CONDITIONAL" ? "조건을 입력하세요" : "거절 사유를 입력하세요", true);
      act(dec === "APPROVED" ? "승인" : dec === "CONDITIONAL" ? "조건부 승인" : "거절", () => post(`/api/w05/approvals/${a.id}/decide`, { decision: dec, text: t.value || null })); };
    return h("div", { class: `card ${pend ? "accent" : a.status === "VOIDED" || a.status === "REJECTED" ? "soft" : ""}` }, h("h3", {}, DEC_KO[a.decision_type], badge(`대안 ${o?.label}`, "acc"), stB(a.status)),
      h("dl", { class: "kv" }, h("dt", {}, "권한자"), h("dd", {}, a.required_role), h("dt", {}, "설비안"), h("dd", {}, o?.capex_ids.join(", ") || "없음"),
        h("dt", {}, "M-B 회신"), h("dd", {}, proc ? ST[proc.status] : "해당 없음"),
        a.conditions_text ? [h("dt", {}, "조건"), h("dd", {}, a.conditions_text)] : null, a.reject_reason ? [h("dt", {}, "사유"), h("dd", {}, a.reject_reason)] : null),
      pend ? h("div", { class: "form-inline" }, t, h("div", { class: "row" }, roleBtn(a.required_role, "승인", decide("APPROVED"), { cls: "ok", disabledReason: dis, icon: "check" }),
        roleBtn(a.required_role, "조건부", decide("CONDITIONAL"), { disabledReason: dis }), roleBtn(a.required_role, "거절", decide("REJECTED"), { cls: "danger", disabledReason: dis }))) : null);
  };
  return [h("div", { class: "section-head" }, h("h2", {}, "승인")), h("div", { class: "cards" }, w.approvals.slice().reverse().map(card))];
}

function propagationGraph(w) {
  const now = w.graph_now, before = w.change_banner?.before_graph, holder = h("div");
  const draw = (mode) => {
    const G = mode === "before" && before ? before : now;
    const cols = [["가정", w.assumptions.filter((a) => (a.is_critical || w.deps.some((d) => d.assumption_id === a.id)) && !(a.status === "SUPERSEDED" && a.value == null)).map((a) => ({ k: `A${a.id}`, label: a.label, st: G.assumptions[a.id] }))],
      ["대안", w.options.filter((o) => G.options[o.id]).map((o) => ({ k: `O${o.id}`, label: `대안 ${o.label}`, st: G.options[o.id] }))],
      ["승인", w.approvals.filter((a) => G.approvals[a.id]).map((a) => ({ k: `P${a.id}`, label: DEC_KO[a.decision_type], st: G.approvals[a.id], opt: a.option_id }))],
      ["작업과 예약", [...w.tasks.filter((t) => G.tasks[t.id]).map((t) => ({ k: `T${t.id}`, label: TASK_KO[t.type], st: G.tasks[t.id], opt: t.option_id })),
        ...Object.entries(G.reservations || {}).map(([oid, st]) => ({ k: `R${oid}`, label: "생산 예약", st, opt: Number(oid) }))]]];
    const W = 980, colW = W / 4, nodeH = 32, gap = 8, pos = {};
    const Hh = Math.max(1, ...cols.map(([, ns]) => ns.length)) * (nodeH + gap) + 40;
    const svg = h("svg", { class: "plot graph", viewBox: `0 0 ${W} ${Hh}`, role: "img", "aria-label": "전파 그래프" });
    cols.forEach(([title, ns], ci) => { svg.append(h("text", { x: ci * colW + 10, y: 16, class: "g-title" }, title));
      ns.forEach((n, i) => { const x = ci * colW + 10, y = 26 + i * (nodeH + gap); pos[n.k] = { x, y };
        const key = { A: "assumptions", O: "options", P: "approvals", T: "tasks", R: "reservations" }[n.k[0]];
        const changed = before && mode === "after" && before[key]?.[n.k.slice(1)] !== n.st;
        svg.append(h("g", { class: `gnode ${stKind(n.st) || "idle"}${changed ? " changed" : ""}` }, h("rect", { x, y, width: colW - 24, height: nodeH, rx: 10 }),
          h("text", { x: x + 12, y: y + 20 }, `${n.label.slice(0, 13)}  ${ST[n.st] || n.st || ""}`))); }); });
    const edge = (a, b) => { if (!pos[a] || !pos[b]) return; svg.insertBefore(h("path", { class: "gedge", d: `M${pos[a].x + colW - 24} ${pos[a].y + 16} C ${pos[a].x + colW} ${pos[a].y + 16}, ${pos[b].x - 10} ${pos[b].y + 16}, ${pos[b].x} ${pos[b].y + 16}` }), svg.firstChild); };
    for (const d of w.deps) if (d.dependent_type === "bid_option") edge(`A${d.assumption_id}`, `O${d.dependent_id}`);
    cols[2][1].forEach((n) => edge(`O${n.opt}`, n.k)); cols[3][1].forEach((n) => { const ap = w.approvals.find((a) => a.option_id === n.opt && a.decision_type === "BID_PARTICIPATION"); edge(ap ? `P${ap.id}` : `O${n.opt}`, n.k); });
    holder.replaceChildren(h("div", { class: "chart scroll-x" }, svg));
  };
  const seg = h("div", { class: "seg", role: "group", "aria-label": "변경 전후" });
  const mk = (label, mode, on) => h("button", { "aria-pressed": on ? "true" : "false", disabled: mode === "before" && !before ? true : null,
    onclick: (e) => { seg.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", x === e.currentTarget ? "true" : "false")); draw(mode); } }, label);
  seg.append(mk("변경 전", "before", false), mk("변경 후", "after", true));
  draw("after");
  return h("div", { class: "section" }, h("div", { class: "toolbar" }, seg, info("가정에서 대안, 승인, 작업과 예약으로 이어집니다. 초록 정상, 빨강 무효, 노랑 미확인, 파랑 이번에 바뀜.")), holder);
}

// ════════════════════════════════════════════════════════════════════════════
// 확인요청(가정 포함)
const ANSWER_FIELDS = { mb_recontract: [["possible", "bool", "재계약 가능"], ["conditions", "text", "조건"]],
  unknown_status_orders: [["unknown_status_orders", "text", "확인 결과"]], line_capacity_basis: [["line_capacity_basis", "text", "확인 결과"]] };
function answerForm(q) {
  const fields = q.assumption_keys.flatMap((k) => (ANSWER_FIELDS[k] || [[k, "text", k]]).map((f) => [k, ...f]));
  const inputs = fields.map(([k, name, type, label]) => [k, name, type, h("input", { type: type === "bool" ? "checkbox" : "text", checked: type === "bool" ? true : null, "aria-label": label }), label]);
  const txt = h("input", { placeholder: "회신 원문", maxlength: 500 });
  return h("div", { class: "form-inline" }, h("div", { class: "fields" }, inputs.map(([, , type, el, label]) => (type === "bool" ? h("label", { class: "inline" }, el, label) : h("label", {}, label, el)))), txt,
    h("button", { class: "primary", onclick: () => { const ans = {}; for (const [k, name, type, el] of inputs) {
      const v = type === "bool" ? el.checked : el.value; if (k === "mb_recontract") ans[k] = { ...(ans[k] || {}), [name]: v }; else ans[k] = v; }
      act("회신 저장", () => post(`/api/w05/requests/${q.id}/answer`, { answer: ans, text: txt.value || "회신" })); } }, "회신 저장"));
}
async function pageAssume(w) {
  setTitle("확인요청");
  if (!w.bid) return h("div", { class: "card empty-state" }, h("p", { class: "muted" }, "입찰 접수 후 표시됩니다"), h("a", { href: "#bid" }, h("button", { class: "primary" }, "입찰로")));
  const r = ROLE(), live = w.assumptions.filter((a) => a.status !== "SUPERSEDED");
  const reqCard = (q) => {
    const who = q.to_external ? PARTY_KO[q.to_external] : q.to_role;
    const dir = q.to_role === r ? "나에게 온 질의" : q.to_external ? `영업 → ${who}` : `영업 → ${who}`;
    let actions = null;
    if (q.status === "DRAFT") { const ta = h("textarea", { rows: 3, maxlength: 1000 }, q.question);
      actions = h("div", { class: "form-inline" }, ta, h("div", { class: "row" }, capBtn("w05_send", "발송", () => act("발송", async () => { if (ta.value !== q.question) await post(`/api/w05/requests/${q.id}/edit`, { question: ta.value }); await post(`/api/w05/requests/${q.id}/send`); }), { primary: true, icon: "send" }))); }
    else if (q.status === "SENT" && q.to_role) actions = q.to_role === r ? answerForm(q) : badge(`${q.to_role} 회신 대기`, "warn");
    else if (q.status === "SENT") actions = badge("외부 회신 대기", "warn");
    else if (q.status === "ANSWERED" && q.to_external && q.assumption_keys.some((k) => live.find((a) => a.key === k && a.status === "UNCONFIRMED")))
      actions = capBtn("w05_confirm", "이 회신으로 가정 확인", () => act("가정 확인", () => post(`/api/w05/requests/${q.id}/confirm`)), { primary: true, icon: "check" });
    const cls = q.status === "ANSWERED" ? "good-card" : q.status === "DRAFT" ? "" : "soft";
    return h("div", { class: `card ${cls}` }, h("div", { class: "small muted row" }, icon(q.to_role === r ? "inbox" : "send"), dir), h("h3", {}, who, stB(q.status), q.is_mock ? badge("모의", "test") : null, q.drafted_by_agent ? badge("AI", "test", "ai") : null),
      q.status !== "DRAFT" ? h("p", { class: "small" }, q.question) : null, q.answer_text ? h("p", { class: "small" }, h("b", {}, "회신 "), q.answer_text) : null,
      h("div", { class: "row small muted" }, icon("clock"), `기한 ${md(q.due_on)}`), actions);
  };
  const notices = (w.notices || []).filter((n) => n.status === "OPEN").map((n) => {
    const v = h("input", { value: n.value ?? "", "aria-label": "새 값" }), why = h("input", { value: n.text, "aria-label": "사유" });
    return h("div", { class: "card warn-card" }, h("h3", {}, icon("alert"), "외부 추가 통지", badge("모의", "test")), h("p", { class: "small" }, n.text),
      n.key ? h("div", { class: "form-inline" }, h("label", {}, `${live.find((a) => a.key === n.key)?.label || n.key} 새 값`, v), h("label", {}, "사유", why),
        capBtn("w05_supersede", "가정 교체", () => act("가정 교체", () => post(`/api/w05/assumptions/${n.key}/supersede`, { value: v.value, reason: why.value, notice_id: n.id })), { primary: true })) : null);
  });
  const sales = r === "영업" || r === "영업임원";
  const groups = [
    ["내가 답할 질의", w.requests.filter((q) => q.status === "SENT" && q.to_role === r), "inbox"],
    ["보낼 질의", sales ? w.requests.filter((q) => q.status === "DRAFT") : [], "send"],
    ["반영할 회신", sales ? w.requests.filter((q) => q.status === "ANSWERED" && q.to_external && q.assumption_keys.some((k) => live.find((a) => a.key === k && a.status === "UNCONFIRMED"))) : [], "check"],
    ["회신 기다리는 중", w.requests.filter((q) => q.status === "SENT" && q.to_role !== r), "clock"],
    ["끝난 질의", w.requests.filter((q) => q.status === "ANSWERED" && !(sales && q.to_external && q.assumption_keys.some((k) => live.find((a) => a.key === k && a.status === "UNCONFIRMED")))), "audit"]];
  const crit = live.filter((a) => a.is_critical), rest = live.filter((a) => !a.is_critical);
  const critVal = (a) => (a.value == null ? "미확인" : typeof a.value === "object" ? (a.value.possible ? "가능" : "불가") : a.value === true ? "허용" : a.value === false ? "불허" : String(a.value));
  return [notices.length ? h("div", { class: "cards" }, notices) : null,
    h("div", { class: "section-head" }, h("h2", {}, "핵심 정보"), info("확인되기 전에는 상신할 수 없습니다")),
    h("div", { class: "grid" }, crit.map((a) => h("div", { class: "tile" }, h("span", { class: "k" }, a.label), h("b", { class: "v" }, critVal(a)), h("span", { class: "row" }, stB(a.status), srcBadge(a.source_type))))),
    ...groups.filter(([, qs]) => qs.length).map(([title, qs, ic]) => h("div", { class: "section" }, h("div", { class: "section-head" }, icon(ic), h("h2", {}, title), badge(`${qs.length}`, "acc")), h("div", { class: "cards" }, qs.map(reqCard)))),
    groups.every(([, qs]) => !qs.length) ? h("p", { class: "empty" }, "내 앞으로 온 질의가 없습니다") : null,
    h("details", {}, h("summary", {}, `기타 가정 ${rest.length}건`),
      table([{ label: "번호", get: (a) => a.code }, { label: "가정", get: (a) => term(a.label) }, { label: "값", get: (a) => (a.value == null ? "-" : typeof a.value === "object" ? JSON.stringify(a.value) : String(a.value)) },
        { label: "상태", get: (a) => stB(a.status) }, { label: "출신", get: (a) => srcBadge(a.source_type) }, { label: "담당", get: (a) => a.owner_role }, { label: "이유", get: (a) => h("span", { class: "small" }, a.rationale) }], rest)),
    !w.messages.length ? null : h("details", {}, h("summary", {}, `외부 수신함 ${w.messages.length}건`), table([{ label: "일자", get: (m) => m.on }, { label: "상대", get: (m) => PARTY_KO[m.party] || m.party },
      { label: "방향", get: (m) => (m.direction === "OUT" ? "발송" : "수신") }, { label: "내용", get: (m) => m.body }, { label: "", get: () => badge("모의", "test") }], w.messages.slice().reverse(), { empty: "없음" }))];
}

// ════════════════════════════════════════════════════════════════════════════
// 여력 보드
async function pageBoard(w) {
  setTitle("여력 보드");
  const os = w.options || [], sel = os.find((o) => o.id === w.bid?.selected_option_id) || os.find((o) => o.status === "PROPOSED" && o.label === "B");
  S.boardCapex ??= (sel?.capex_ids || []).join(",");
  const bd = await api(`/api/w05/board?capex=${encodeURIComponent(S.boardCapex)}`);
  const seg = h("div", { class: "seg", role: "group", "aria-label": "설비안" }, ["", "OPT-01", "OPT-02", "OPT-01,OPT-02"].map((o) => h("button", { "aria-pressed": S.boardCapex === o ? "true" : "false", onclick: () => { S.boardCapex = o; route(); } },
    o ? o.replace(",", " + ") : "설비안 없음")));
  const lines = Object.keys(bd.lines), weeks = bd.weeks, cw = 12, ch = 36, L = 110, Wd = L + weeks.length * cw + 10, Hh = lines.length * (ch + 8) + 28;
  const svg = h("svg", { class: "plot board", viewBox: `0 0 ${Wd} ${Hh}`, role: "img", "aria-label": "라인별 주간 약속 가능 물량" });
  svg.append(h("defs", {}, h("pattern", { id: "hatch", width: 4, height: 4, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, h("rect", { width: 4, height: 4, class: "hatch-bg" }), h("line", { x1: 0, y1: 0, x2: 0, y2: 4, class: "hatch-line" }))));
  weeks.forEach((wk, i) => { if (i % 8 === 0) svg.append(h("text", { x: L + i * cw, y: 12, class: "axis-t" }, `${wk.slice(2, 4)}.${wk.slice(5, 7)}`)); });
  const short = { "L-01": "L-01 기초", "L-02": "L-02 더마", "F2-DERMA-NEW": "F2 신설 더마" };
  lines.forEach((ln, j) => {
    const y = 20 + j * (ch + 8);
    svg.append(h("text", { x: 4, y: y + 22, class: "row-t" }, short[ln] || ln));
    bd.lines[ln].forEach((c, i) => {
      const cap = c.full_capacity + c.added_capacity, ratio = cap ? c.atp / cap : 0;
      const r = h("rect", { x: L + i * cw, y, width: cw - 2, height: ch, rx: 3, class: c.overload > 0 ? "cell over" : "cell", fill: c.overload > 0 ? "url(#hatch)" : null });
      if (c.overload <= 0) r.style.fillOpacity = String(0.1 + 0.9 * Math.min(1, ratio * 4));
      r.addEventListener("mousemove", (ev) => tipAt(ev, [`${short[ln] || ln}, ${c.week}`, `약속 가능 ${fmt(c.atp, 0)} EA`, `부하 ${fmt(c.baseline_load, 0)} / 능력 ${fmt(c.full_capacity + c.added_capacity, 0)}`,
        c.reserved_qty ? `예약 ${fmt(c.reserved_qty, 0)}` : "", c.overload > 0 ? `초과 ${fmt(c.overload, 0)} EA` : ""].filter(Boolean)));
      r.addEventListener("mouseleave", hideTip);
      if (ln !== "F2-DERMA-NEW") r.addEventListener("click", () => weekOrders(ln, c.week));
      svg.append(r);
    });
  });
  const unk = bd.unknown_total.orders;
  return [h("div", { class: "grid" }, tile("L-02 더마", `${bd.line_util["L-02"]}%`, "병목 라인"), tile("F1 평균", `${fmt(bd.f1_util)}%`, "평균만 보면 병목이 가려짐"),
      tile("L-01 기초", `${bd.line_util["L-01"]}%`), tile("상태 불명", `${fmt(unk)}건`, "부하 제외, 확인 필요")),
    h("div", { class: "toolbar" }, seg, info("칸이 진할수록 새로 약속할 수 있는 물량이 많습니다. 빗금은 기존 주문만으로 능력을 넘는 주. 칸을 누르면 그 주의 주문.")),
    h("div", { class: "chart scroll-x" }, svg), h("div", { id: "week-orders" })];
}
async function weekOrders(line, week) {
  const box2 = $("week-orders"); if (!box2) return;
  const rows = await api(`/api/w05/board/orders?line=${line}&week=${week}`);
  box2.replaceChildren(section(`${nm(line)} ${week} 주 주문`, "출하 예정 주는 수주일 + 60일(가정 A-02)", null, table([{ label: "주문", get: (r) => r.order_id }, { label: "고객", get: (r) => h("a", { href: `#partners/${r.customer_id}` }, r.customer_id) },
    { label: "수주일", get: (r) => r.order_date }, { label: "수량", num: 1, get: (r) => fmt(r.qty) }, { label: "원본 행", num: 1, get: (r) => r.source_row }], rows)));
  box2.scrollIntoView({ block: "nearest" });
}

// ════════════════════════════════════════════════════════════════════════════
// 작업
async function pageTasks(w) {
  setTitle("작업");
  if (!w.bid) return h("div", { class: "card empty-state" }, h("p", { class: "muted" }, "입찰 접수 후 표시됩니다"));
  const r = ROLE();
  const card = (t) => {
    const open = ["OPEN", "IN_PROGRESS"].includes(t.status);
    const conds = t.type === "PROPOSAL_TERMS" && (t.payload.conditions || []).length && !t.payload.conditions_acknowledged;
    const ck = conds ? h("input", { type: "checkbox", "aria-label": "조건 반영" }) : null;
    const pl = Object.entries(t.payload).filter(([k, v]) => v != null && v !== "" && k !== "conditions_acknowledged");
    return h("div", { class: `card ${t.status === "BLOCKED" ? "bad-card" : t.status === "DONE" ? "soft" : ""}` }, h("h3", {}, TASK_KO[t.type] || t.type, stB(t.status)),
      h("div", { class: "row small muted" }, icon("user"), t.owner_role, icon("clock"), md(t.due_on)),
      conds ? h("div", { class: "card warn-card small" }, `조건: ${t.payload.conditions.join(", ")}`) : null,
      pl.length ? h("details", {}, h("summary", {}, "내용"), h("dl", { class: "kv small" }, pl.map(([k, v]) => [h("dt", {}, k), h("dd", {}, typeof v === "object" ? JSON.stringify(v) : String(v))]))) : null,
      t.status === "BLOCKED" ? h("div", { class: "row small" }, icon("lock"), "전제가 바뀌어 잠김") : null,
      open ? h("div", { class: "row" }, conds ? h("label", { class: "inline small" }, ck, "조건 반영함") : null,
        roleBtn(t.owner_role, "완료", () => act("작업 완료", () => post(`/api/w05/tasks/${t.id}/complete`, { acknowledge_conditions: ck ? ck.checked : false })), { cls: "primary", icon: "check", disabledReason: t.owner_role === r ? null : `담당 ${t.owner_role}` })) : null);
  };
  const open = w.tasks.filter((t) => ["OPEN", "IN_PROGRESS", "BLOCKED"].includes(t.status)), done = w.tasks.filter((t) => !["OPEN", "IN_PROGRESS", "BLOCKED"].includes(t.status));
  return [open.length ? h("div", { class: "cards" }, open.slice().reverse().map(card)) : h("div", { class: "card empty-state" }, h("p", { class: "muted" }, "열린 작업이 없습니다")),
    w.reservations.length ? section("생산 예약", null, null, table([{ label: "대안", get: (x) => w.options.find((o) => o.id === x.option_id)?.label }, { label: "라인", get: (x) => nm(x.line_id) },
      { label: "기간", get: (x) => `${x.from} ~ ${x.to}` }, { label: "수량", num: 1, get: (x) => `${fmt(x.qty, 0)} EA` }, { label: "상태", get: (x) => stB(x.status) }], w.reservations)) : null,
    done.length ? h("details", {}, h("summary", {}, `끝난 작업 ${done.length}건`), h("div", { class: "cards" }, done.map(card))) : null];
}

// ════════════════════════════════════════════════════════════════════════════
// 이력
const EV_KO = { BID_RECEIVED: "RFP 접수", GAPS_DETECTED: "공백 탐지", INFO_REQUEST_SENT: "확인요청 발송", INFO_REQUEST_EDITED: "확인요청 수정", INFO_REQUEST_ANSWERED: "회신 도착",
  ANSWER_ACCEPTED: "회신으로 가정 확인", ASSUMPTION_CONFIRMED: "가정 확인", ASSUMPTION_REFUTED: "가정 반박", ASSUMPTION_SUPERSEDED: "가정 교체", ASSUMPTION_PROPOSED: "가정 제안",
  ASSUMPTION_CHANGE_REQUESTED: "가정 교체 요청", OPTION_PROPOSED: "대안 제시", OPTION_SUBMITTED: "상신", APPROVAL_REQUESTED: "승인 요청", APPROVAL_DECIDED: "승인 결정",
  BID_STATUS_CHANGED: "상태 변경", OPTION_STALE: "대안 무효", APPROVAL_VOIDED: "승인 무효", TASK_BLOCKED: "작업 막힘", TASK_CREATED: "작업 생성", TASK_DONE: "작업 완료",
  RESERVATIONS_RELEASED: "예약 해제", RESERVATION_CONFIRMED: "예약 확정", REVALIDATED_NO_CHANGE: "재검증 결론 유지", DEADLINE_TICK: "마감 임박", OPEN_WORK_CANCELLED: "열린 작업 취소",
  BID_SUBMITTED: "입찰 제출", FINANCE_NOTE: "재무 의견", RISK_FLAG_ACKNOWLEDGED: "위험 표시 확인", EXTERNAL_NOTICE: "외부 통지" };
const EV_IC = { BID_RECEIVED: "bid", INFO_REQUEST_SENT: "send", APPROVAL_DECIDED: "check", OPTION_SUBMITTED: "send", ASSUMPTION_SUPERSEDED: "alert", BID_STATUS_CHANGED: "dot", EXTERNAL_NOTICE: "alert" };
async function pageHistory(w, arg) {
  setTitle("이력");
  const tabs = can("audit_view") ? h("nav", { class: "steps" }, h("a", { href: "#history", "aria-current": arg !== "audit" ? "page" : null }, icon("audit"), "업무 사건"), h("a", { href: "#history/audit", "aria-current": arg === "audit" ? "page" : null }, icon("lock"), "감사 기록")) : null;
  if (arg === "audit" && can("audit_view")) { const a = await pageAudit(); setTitle("이력"); return [tabs, ...[a].flat()]; }
  const ev = w.events || [], kids = {};
  for (const e of ev) if (e.caused_by) (kids[e.caused_by] ||= []).push(e);
  const kind = (t) => (/VOIDED|STALE|BLOCKED|RELEASED|REFUTED|CANCELLED/.test(t) ? "bad" : /SUPERSEDED|TICK|NOTICE|CHANGE/.test(t) ? "warn" : /DONE|CONFIRMED|SUBMITTED|ACCEPTED/.test(t) ? "good" : "");
  const line = (e, depth) => h("li", { class: depth ? "child" : null }, h("span", { class: `ic-bub ${kind(e.event_type)}` }, icon(EV_IC[e.event_type] || "dot")),
    h("span", { class: "t" }, h("b", {}, EV_KO[e.event_type] || e.event_type), h("div", { class: "small muted" }, `${e.actor_id === "agent" && w.agent?.mode === "mock" ? "AI(모의)" : ACTOR_KO[e.actor_id] || e.actor_id}${e.acting_as ? ` (${e.acting_as})` : ""}${e.reason ? `, ${e.reason}` : ""}`)),
    e.actor_type === "EXTERNAL_MOCK" ? badge("모의", "test") : null, h("span", { class: "d" }, md(e.ts.slice(0, 10))));
  const render = (e, depth) => [line(e, depth), ...(kids[e.id] || []).map((k) => render(k, depth + 1))];
  const roots = ev.filter((e) => !e.caused_by || !ev.find((x) => x.id === e.caused_by)).slice().reverse();
  return [tabs, roots.length ? h("ul", { class: "list timeline" }, roots.map((e) => render(e, 0)).flat(Infinity)) : h("p", { class: "empty" }, "아직 사건이 없습니다")];
}

// ════════════════════════════════════════════════════════════════════════════
// 시연
async function pageTestW(d) {
  const w = await getW();
  const old = await pageTest(d);
  setTitle("시연");
  const ext = (w.requests || []).filter((q) => q.status === "SENT" && q.to_external);
  const demo = ROLE() === "시연 진행자" || S.me.real_role === "시연 진행자";
  const buyerForm = (q) => { const dt = h("input", { type: "date", value: "2027-04-01" }), pa = h("input", { type: "checkbox", checked: true }), sp = h("input", { type: "checkbox", checked: true }),
    tx = h("input", { value: "납품 개시 2027-04-01, 부분 물량 제안 허용, 분할 납품 허용" });
    return h("div", { class: "card" }, h("h3", {}, PARTY_KO[q.to_external], badge("모의", "test")),
      h("div", { class: "form-inline" }, h("div", { class: "fields" }, h("label", {}, "납품 개시일", dt), h("label", { class: "inline" }, pa, "부분 물량 허용"), h("label", { class: "inline" }, sp, "분할 납품 허용")), tx,
        capBtn("w05_answer", "모의 회신 입력", () => act("모의 회신", () => post(`/api/w05/requests/${q.id}/answer`,
          { answer: { supply_start_date: dt.value, partial_award_allowed: pa.checked, split_delivery: sp.checked }, text: tx.value })), { primary: true, disabledReason: demo ? null : "시연 진행자만" }))); };
  const nDate = h("input", { type: "date", value: "2026-10-01" }), nTxt = h("input", { value: "납품 개시를 2026-10-01로 앞당겨 달라" });
  const today = h("input", { type: "date", value: w.today });
  return [h("div", { class: "banner test" }, icon("test"), h("span", { class: "grow" }, "시연 전용. 외부 상대의 회신과 시계는 모의 입력으로 기록됩니다")),
    h("div", { class: "test-zone" },
      h("div", { class: "cards" },
        h("div", { class: "card" }, h("h3", {}, icon("clock"), "가상 시계"), h("div", { class: "row" }, today, capBtn("test_clock", "이동", () => act("시계 이동", () => post("/api/w05/clock", { today: today.value }))))),
        w.bid ? h("div", { class: "card" }, h("h3", {}, icon("alert"), "바이어 추가 통지"), h("div", { class: "row" }, nDate), nTxt,
          capBtn("w05_answer", "통지 입력", () => act("통지 입력", () => post("/api/w05/notice", { party: "C-201_BUYER", text: nTxt.value, key: "supply_start_date", value: nDate.value })), { disabledReason: demo ? null : "시연 진행자만" })) : null,
        h("div", { class: "card" }, h("h3", {}, icon("audit"), "처음부터"), h("p", { class: "small muted" }, `현재 ${S.me.workspace}`),
          h("div", { class: "row" }, capBtn("test_reset", "S1, S3-a 시작(빈 회차)", () => { if (!confirm("새 검토 회차를 엽니다. 이전 기록은 남습니다.")) return; act("새 회차", async () => { const r = await post("/api/test/reset"); resetUi(r.workspace); }); }, { cls: "danger" })),
          h("div", { class: "row" }, [["s2", "S2 시작 상태(7/7, 승인과 예약 완료)"], ["s3b", "S3-b 시작 상태(7/4, 상신 직후)"]].map(([k, t]) =>
            capBtn("test_reset", t, () => { if (!confirm(`${t}로 새 회차를 엽니다.`)) return; act("시연 준비", async () => { const r = await post("/api/test/prepare", { scenario: k }); resetUi(r.workspace); }); }))))),
      ext.length ? h("div", { class: "cards" }, ext.map(buyerForm)) : h("p", { class: "empty" }, "외부 회신을 기다리는 확인요청이 없습니다"),
      h("details", {}, h("summary", {}, "솔버 분석용 모의 회신"), old.slice(1))),
    h("p", { class: "small muted" }, "실제 작동: 계산, 위험 표시, 근거 추적, 권한, 전파, 마감, 이력 / 모의: 외부 회신, 입찰 제출 / 설계: ERP 연동")];
}
