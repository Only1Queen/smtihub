import { useState, useEffect, useMemo } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from "recharts";
import {
  ShieldCheck, Lock, User, Plus, Trash2, ChevronLeft,
  Save, Eye, EyeOff, LogOut, Loader,
} from "lucide-react";

// ── Constants ────────────────────────────────────────────────
const MONTHS = [
  "May-26","Jun-26","Jul-26","Aug-26","Sep-26","Oct-26",
  "Nov-26","Dec-26","Jan-27","Feb-27","Mar-27","Apr-27",
];
// Month indices that are quarter-end (Jun, Sep, Dec, Mar)
const QUARTER_END = new Set([1, 4, 7, 10]);

// Goals and KPIs with max marks per KPI
// Total per goal = 20, grand total = 100
const GOALS = [
  {
    id: "A", name: "Operational Tasks",
    kpis: [
      { id: "A1", text: "Timely and efficient completion of assigned tasks", max: 10 },
      { id: "A2", text: "Maintain task completion rate of 99% within agreed timelines", max: 5 },
      { id: "A3", text: "Prompt escalation of issues or blockers affecting task delivery", max: 5 },
    ],
  },
  {
    id: "B", name: "Threat Intelligence Dissemination",
    kpis: [
      { id: "B1", text: "Threat intel reports disseminated (min. 2/month)", max: 10 },
      { id: "B2", text: "New detection rules deployed based on threat intel insights", max: 5, quarterly: true },
      { id: "B3", text: "Shared intel includes validated IOCs and actionable recommendations", max: 5 },
    ],
  },
  {
    id: "C", name: "Security Monitoring",
    kpis: [
      { id: "C1", text: "Daily monitoring of activities to identify anomalies or suspicious behaviour", max: 10 },
      { id: "C2", text: "Timely detection and escalation of security incidents", max: 5 },
      { id: "C3", text: "Tuning/optimization improvements based on identified gaps (min. 2/quarter)", max: 5, quarterly: true },
    ],
  },
  {
    id: "D", name: "Security Posture Improvement",
    kpis: [
      { id: "D1", text: "Tool configuration reviews and quarterly health checks completed", max: 5 },
      { id: "D2", text: "New or improved detection rules/reports implemented (min. 2/month)", max: 5 },
      { id: "D3", text: "Identified security gaps tracked and remediated within agreed timelines", max: 5 },
      { id: "D4", text: "Actionable recommendations provided to enhance security controls", max: 5 },
    ],
  },
  {
    id: "E", name: "Team Collaboration & Communication",
    kpis: [
      { id: "E1", text: "SOC requests from DFIR team efficiently completed", max: 4 },
      { id: "E2", text: "Assigned monthly practical sessions delivered", max: 4 },
      { id: "E3", text: "Bi-weekly knowledge transfer sessions conducted", max: 4 },
      { id: "E4", text: "Professional security course or certification progress", max: 4 },
      { id: "E5", text: "Active participation in team discussions, incident reviews and process improvements", max: 4 },
    ],
  },
];

// ── Scoring helpers ──────────────────────────────────────────

// Max marks achievable in a given month (quarterly KPIs only count at quarter-end)
function monthMaxMarks(mi) {
  return GOALS.reduce((s, g) =>
    s + g.kpis.reduce((ss, k) =>
      (!k.quarterly || QUARTER_END.has(mi)) ? ss + k.max : ss, 0), 0);
}

// Whether any score has been entered for a month
function monthHasData(scores, mi) {
  return GOALS.some(g => g.kpis.some(k => {
    if (k.quarterly && !QUARTER_END.has(mi)) return false;
    const v = scores?.[k.id]?.[mi];
    return v !== "" && v !== null && v !== undefined;
  }));
}

// Sum of raw scores entered for a month
function monthRawScore(scores, mi) {
  return GOALS.reduce((s, g) =>
    s + g.kpis.reduce((ss, k) => {
      if (k.quarterly && !QUARTER_END.has(mi)) return ss;
      const v = scores?.[k.id]?.[mi];
      return ss + (v !== "" && v !== null && v !== undefined ? Number(v) : 0);
    }, 0), 0);
}

// Raw score for a single goal in a given month (out of 20)
function goalMonthRaw(scores, goal, mi) {
  return goal.kpis.reduce((s, k) => {
    if (k.quarterly && !QUARTER_END.has(mi)) return s;
    const v = scores?.[k.id]?.[mi];
    return s + (v !== "" && v !== null && v !== undefined ? Number(v) : 0);
  }, 0);
}

// Annual score = average of monthly % scores (scored months only), expressed as a %
function annualScorePct(scores) {
  const monthly = MONTHS.map((_, mi) => {
    if (!monthHasData(scores, mi)) return null;
    const max = monthMaxMarks(mi);
    return max > 0 ? (monthRawScore(scores, mi) / max) * 100 : 0;
  }).filter(v => v !== null);
  if (!monthly.length) return null;
  return monthly.reduce((a, b) => a + b, 0) / monthly.length;
}

// ── Storage (localStorage, persists in Electron) ─────────────
function lsGet(key) { try { return localStorage.getItem(key); } catch { return null; } }
function lsSet(key, val) { try { localStorage.setItem(key, val); } catch { /* silent */ } }

function uid() { return Math.random().toString(36).slice(2, 9); }

const DEFAULT_CONFIG = { managerPin: "2026", members: [] };

// ── Colour helpers ────────────────────────────────────────────
function scoreColor(val) {
  if (val === null || val === undefined) return "text-slate-500";
  if (val >= 90) return "text-emerald-400";
  if (val >= 70) return "text-amber-400";
  return "text-rose-400";
}

function ScoreBadge({ value, size = "md" }) {
  const sz = size === "lg" ? "text-3xl" : size === "sm" ? "text-xs" : "text-base";
  const display = value !== null && value !== undefined ? `${Math.round(value)}%` : "—";
  return <span className={`font-mono font-bold ${sz} ${scoreColor(value)}`}>{display}</span>;
}

// ════════════════════════════════════════════════════════════
export default function App() {
  const [screen, setScreen]               = useState("home");
  const [config, setConfig]               = useState(null);
  const [error, setError]                 = useState("");
  const [pinInput, setPinInput]           = useState("");
  const [showPin, setShowPin]             = useState(false);
  const [nameInput, setNameInput]         = useState("");
  const [memPin, setMemPin]               = useState("");
  const [activeMember, setActiveMember]   = useState(null);
  const [scoresCache, setScoresCache]     = useState({});
  const [newName, setNewName]             = useState("");
  const [newPin, setNewPin]               = useState("");
  const [saving, setSaving]               = useState(false);

  useEffect(() => {
    const raw = lsGet("smti_config");
    const cfg = raw ? JSON.parse(raw) : DEFAULT_CONFIG;
    if (!raw) lsSet("smti_config", JSON.stringify(cfg));
    setConfig(cfg);
  }, []);

  function persistConfig(next) {
    setConfig(next);
    lsSet("smti_config", JSON.stringify(next));
  }

  function loadScores(memberId) {
    if (scoresCache[memberId]) return scoresCache[memberId];
    const raw = lsGet(`smti_scores:${memberId}`);
    const parsed = raw ? JSON.parse(raw) : {};
    setScoresCache(prev => ({ ...prev, [memberId]: parsed }));
    return parsed;
  }

  function saveScores(memberId, scores) {
    setScoresCache(prev => ({ ...prev, [memberId]: scores }));
    setSaving(true);
    lsSet(`smti_scores:${memberId}`, JSON.stringify(scores));
    setTimeout(() => setSaving(false), 700);
  }

  function resetAuth() {
    setPinInput(""); setNameInput(""); setMemPin(""); setError(""); setActiveMember(null);
  }

  // Loading
  if (!config) return (
    <div className="min-h-screen bg-slate-950 flex items-center justify-center">
      <Loader className="w-6 h-6 text-amber-400 animate-spin" />
    </div>
  );

  // ── HOME ──
  if (screen === "home") return (
    <Shell>
      <div className="flex flex-col items-center justify-center min-h-screen px-6 text-center">
        <div className="w-16 h-16 rounded-2xl bg-amber-500/10 border border-amber-500/20 flex items-center justify-center mb-5">
          <ShieldCheck className="w-8 h-8 text-amber-400" />
        </div>
        <h1 className="text-2xl font-bold text-slate-100">SMTI Appraisal Tracker</h1>
        <p className="text-slate-400 text-sm mt-2">Track your performance in the team.</p>
        <div className="mt-10 flex flex-col gap-3 w-full max-w-xs">
          <button onClick={() => { resetAuth(); setScreen("mgrLogin"); }}
            className="flex items-center justify-center gap-2 bg-amber-500 hover:bg-amber-400 text-slate-950 font-semibold py-3 rounded-xl transition-colors">
            <Lock className="w-4 h-4" /> Manager Login
          </button>
          <button onClick={() => { resetAuth(); setScreen("memLogin"); }}
            className="flex items-center justify-center gap-2 border border-slate-700 hover:border-amber-500/40 hover:text-amber-400 text-slate-200 font-semibold py-3 rounded-xl transition-colors">
            <User className="w-4 h-4" /> Team Member Login
          </button>
        </div>
        <p className="text-slate-700 text-xs mt-8">May 2026 – April 2027 · 100 marks total</p>
      </div>
    </Shell>
  );

  // ── MANAGER LOGIN ──
  if (screen === "mgrLogin") return (
    <Shell>
      <AuthCard title="Manager Access" icon={<Lock className="w-5 h-5" />} onBack={() => setScreen("home")}>
        <label className="text-xs text-slate-400 uppercase tracking-wide">Manager PIN</label>
        <div className="relative mt-1">
          <input
            type={showPin ? "text" : "password"}
            value={pinInput}
            onChange={e => setPinInput(e.target.value)}
            onKeyDown={e => e.key === "Enter" && doMgrLogin()}
            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2.5 text-slate-100 font-mono focus:outline-none focus:ring-2 focus:ring-amber-500"
            placeholder="Enter PIN"
          />
          <button onClick={() => setShowPin(s => !s)} className="absolute right-3 top-3 text-slate-500">
            {showPin ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
          </button>
        </div>
        {error && <p className="text-rose-400 text-xs mt-2">{error}</p>}
        <button onClick={doMgrLogin} className="mt-4 w-full bg-amber-500 hover:bg-amber-400 text-slate-950 font-semibold py-2.5 rounded-lg">
          Enter
        </button>
        <p className="text-slate-600 text-xs mt-4">Default PIN is 2026. Change it from Settings after logging in.</p>
      </AuthCard>
    </Shell>
  );

  function doMgrLogin() {
    if (pinInput === config.managerPin) { setError(""); setScreen("mgrDash"); }
    else setError("Incorrect PIN. Please try again.");
  }

  // ── MEMBER LOGIN ──
  if (screen === "memLogin") return (
    <Shell>
      <AuthCard title="My Appraisal" icon={<User className="w-5 h-5" />} onBack={() => setScreen("home")}>
        <label className="text-xs text-slate-400 uppercase tracking-wide">Full Name</label>
        <input
          value={nameInput}
          onChange={e => setNameInput(e.target.value)}
          className="w-full mt-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2.5 text-slate-100 focus:outline-none focus:ring-2 focus:ring-amber-500"
          placeholder="As registered by your manager"
        />
        <label className="text-xs text-slate-400 uppercase tracking-wide mt-3 block">PIN</label>
        <input
          type="password"
          value={memPin}
          onChange={e => setMemPin(e.target.value)}
          onKeyDown={e => e.key === "Enter" && doMemLogin()}
          className="w-full mt-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2.5 text-slate-100 font-mono focus:outline-none focus:ring-2 focus:ring-amber-500"
          placeholder="Your PIN"
        />
        {error && <p className="text-rose-400 text-xs mt-2">{error}</p>}
        <button onClick={doMemLogin} className="mt-4 w-full bg-amber-500 hover:bg-amber-400 text-slate-950 font-semibold py-2.5 rounded-lg">
          View My Score
        </button>
      </AuthCard>
    </Shell>
  );

  function doMemLogin() {
    const m = config.members.find(mm =>
      mm.name.trim().toLowerCase() === nameInput.trim().toLowerCase() && mm.pin === memPin
    );
    if (!m) { setError("No match found. Check your name and PIN, or ask your manager."); return; }
    setError(""); setActiveMember(m); loadScores(m.id); setScreen("memView");
  }

  // ── MANAGER DASHBOARD ──
  if (screen === "mgrDash") return (
    <ManagerDashboard
      config={config}
      persistConfig={persistConfig}
      scoresCache={scoresCache}
      loadScores={loadScores}
      saveScores={saveScores}
      activeMember={activeMember}
      setActiveMember={setActiveMember}
      newName={newName} setNewName={setNewName}
      newPin={newPin} setNewPin={setNewPin}
      saving={saving}
      onLogout={() => { resetAuth(); setScreen("home"); }}
    />
  );

  // ── MEMBER VIEW ──
  if (screen === "memView" && activeMember) {
    const scores = scoresCache[activeMember.id] || {};
    return <MemberView member={activeMember} scores={scores} onLogout={() => { resetAuth(); setScreen("home"); }} />;
  }

  return null;
}

// ── Shell & AuthCard ─────────────────────────────────────────
function Shell({ children }) {
  return <div className="min-h-screen bg-slate-950 font-sans text-slate-100">{children}</div>;
}

function AuthCard({ title, icon, children, onBack }) {
  return (
    <div className="min-h-screen flex items-center justify-center px-6">
      <div className="w-full max-w-sm">
        <button onClick={onBack} className="flex items-center gap-1 text-slate-500 hover:text-slate-300 text-sm mb-6">
          <ChevronLeft className="w-4 h-4" /> Back
        </button>
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6">
          <div className="flex items-center gap-2 text-slate-100 font-semibold mb-5">{icon}{title}</div>
          {children}
        </div>
      </div>
    </div>
  );
}

// ── MANAGER DASHBOARD COMPONENT ──────────────────────────────
function ManagerDashboard({ config, persistConfig, scoresCache, loadScores, saveScores, activeMember, setActiveMember, newName, setNewName, newPin, setNewPin, saving, onLogout }) {
  const [view, setView]           = useState("list");
  const [pinChange, setPinChange] = useState("");
  const [pinSaved, setPinSaved]   = useState(false);

  function selectMember(m) { setActiveMember(m); loadScores(m.id); setView("score"); }

  function addMember() {
    if (!newName.trim() || !newPin.trim()) return;
    persistConfig({ ...config, members: [...config.members, { id: uid(), name: newName.trim(), pin: newPin.trim() }] });
    setNewName(""); setNewPin("");
  }

  function removeMember(id) {
    persistConfig({ ...config, members: config.members.filter(m => m.id !== id) });
    if (activeMember?.id === id) { setActiveMember(null); setView("list"); }
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      {/* Header */}
      <header className="border-b border-slate-800 px-6 py-4 flex items-center justify-between sticky top-0 bg-slate-950/95 backdrop-blur z-10">
        <div className="flex items-center gap-3">
          <ShieldCheck className="w-5 h-5 text-amber-400" />
          <span className="font-semibold">SMTI Appraisal Tracker</span>
          <span className="text-xs text-slate-600 hidden sm:block">· Manager</span>
          {saving && <span className="text-xs text-amber-500/70 flex items-center gap-1"><Save className="w-3 h-3 animate-pulse" /> saving…</span>}
        </div>
        <button onClick={onLogout} className="flex items-center gap-1 text-sm text-slate-400 hover:text-slate-200">
          <LogOut className="w-4 h-4" /> Log out
        </button>
      </header>

      <div className="flex">
        {/* Sidebar */}
        <aside className="w-56 border-r border-slate-800 min-h-screen p-3 shrink-0">
          <nav className="space-y-0.5 mb-4">
            <button onClick={() => setView("list")}
              className={`w-full text-left text-sm px-3 py-2 rounded-lg ${view==="list" ? "bg-slate-800 text-amber-400" : "text-slate-400 hover:bg-slate-900 hover:text-slate-200"}`}>
              Team Members
            </button>
            <button onClick={() => setView("settings")}
              className={`w-full text-left text-sm px-3 py-2 rounded-lg ${view==="settings" ? "bg-slate-800 text-amber-400" : "text-slate-400 hover:bg-slate-900 hover:text-slate-200"}`}>
              Settings
            </button>
          </nav>

          {config.members.length > 0 && (
            <div className="pt-3 border-t border-slate-800">
              <p className="text-xs text-slate-600 uppercase tracking-wide px-3 mb-2">Score Entry</p>
              {config.members.map(m => {
                const annual = scoresCache[m.id] ? annualScorePct(scoresCache[m.id]) : null;
                return (
                  <button key={m.id} onClick={() => selectMember(m)}
                    className={`w-full text-left text-xs px-3 py-2 rounded-lg flex items-center justify-between gap-2 ${activeMember?.id===m.id&&view==="score" ? "bg-slate-800 text-amber-400" : "text-slate-300 hover:bg-slate-900"}`}>
                    <span className="truncate">{m.name}</span>
                    {annual !== null && <span className={`font-mono font-bold shrink-0 ${scoreColor(annual)}`}>{Math.round(annual)}%</span>}
                  </button>
                );
              })}
            </div>
          )}
        </aside>

        {/* Main */}
        <main className="flex-1 p-5 overflow-auto">

          {/* ── Team Members list ── */}
          {view === "list" && (
            <div>
              <h2 className="text-lg font-semibold mb-4">Team Members</h2>
              <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 mb-6 max-w-lg">
                <p className="text-sm text-slate-400 mb-3">Add a team member and share their PIN privately so they can view their scores.</p>
                <div className="flex gap-2 flex-wrap">
                  <input value={newName} onChange={e => setNewName(e.target.value)} placeholder="Full name"
                    onKeyDown={e => e.key === "Enter" && addMember()}
                    className="flex-1 min-w-[130px] bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500" />
                  <input value={newPin} onChange={e => setNewPin(e.target.value)} placeholder="PIN"
                    onKeyDown={e => e.key === "Enter" && addMember()}
                    className="w-24 bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-amber-500" />
                  <button onClick={addMember}
                    className="flex items-center gap-1 bg-amber-500 hover:bg-amber-400 text-slate-950 font-semibold px-4 py-2 rounded-lg text-sm">
                    <Plus className="w-4 h-4" /> Add
                  </button>
                </div>
              </div>

              {config.members.length === 0
                ? <p className="text-slate-600 text-sm">No team members added yet.</p>
                : <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {config.members.map(m => {
                      const s = scoresCache[m.id];
                      const annual = s ? annualScorePct(s) : null;
                      return (
                        <div key={m.id} className="bg-slate-900 border border-slate-800 rounded-2xl p-4">
                          <div className="flex items-start justify-between mb-3">
                            <div>
                              <p className="font-semibold">{m.name}</p>
                              <p className="text-xs text-slate-500 font-mono mt-0.5">PIN: {m.pin}</p>
                            </div>
                            <button onClick={() => removeMember(m.id)} className="text-slate-700 hover:text-rose-400 transition-colors">
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </div>
                          <div className="flex items-center justify-between text-xs text-slate-500 mb-3">
                            <span>Annual score (YTD)</span>
                            {annual !== null ? <ScoreBadge value={annual} /> : <span className="text-slate-600">not scored yet</span>}
                          </div>
                          <button onClick={() => selectMember(m)}
                            className="w-full text-sm border border-slate-700 hover:border-amber-500/50 hover:text-amber-400 rounded-lg py-2 transition-colors">
                            Enter scores →
                          </button>
                        </div>
                      );
                    })}
                  </div>
              }
            </div>
          )}

          {/* ── Settings ── */}
          {view === "settings" && (
            <div className="max-w-sm">
              <h2 className="text-lg font-semibold mb-4">Settings</h2>
              <label className="text-xs text-slate-400 uppercase tracking-wide">Change Manager PIN</label>
              <div className="flex gap-2 mt-1">
                <input value={pinChange} onChange={e => { setPinChange(e.target.value); setPinSaved(false); }} placeholder="New PIN"
                  className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-amber-500" />
                <button onClick={() => { if (pinChange.trim()) { persistConfig({ ...config, managerPin: pinChange.trim() }); setPinChange(""); setPinSaved(true); } }}
                  className="bg-amber-500 hover:bg-amber-400 text-slate-950 font-semibold px-4 py-2 rounded-lg text-sm">
                  Save
                </button>
              </div>
              {pinSaved && <p className="text-emerald-400 text-xs mt-2">PIN updated successfully.</p>}
              <p className="text-slate-600 text-xs mt-6 leading-relaxed">
                All data is stored locally on this machine and persists between sessions automatically. No internet connection required.
              </p>
            </div>
          )}

          {/* ── Score Entry ── */}
          {view === "score" && activeMember && (
            <ScoreEntry
              member={activeMember}
              scores={scoresCache[activeMember.id] || {}}
              onSave={s => saveScores(activeMember.id, s)}
            />
          )}
        </main>
      </div>
    </div>
  );
}

// ── SCORE ENTRY ──────────────────────────────────────────────
function ScoreEntry({ member, scores, onSave }) {
  const [local, setLocal]           = useState(scores);
  const [activeMonth, setActiveMonth] = useState(0);
  const [saved, setSaved]           = useState(false);

  useEffect(() => { setLocal(scores); }, [member.id]);

  function setVal(kpiId, mi, raw) {
    const kpi = GOALS.flatMap(g => g.kpis).find(k => k.id === kpiId);
    const capped = raw === "" ? "" : Math.max(0, Math.min(kpi.max, Number(raw)));
    setLocal(prev => ({ ...prev, [kpiId]: { ...(prev[kpiId] || {}), [mi]: capped } }));
    setSaved(false);
  }

  function handleSave() { onSave(local); setSaved(true); setTimeout(() => setSaved(false), 2000); }

  const mi        = activeMonth;
  const mRaw      = monthRawScore(local, mi);
  const mMax      = monthMaxMarks(mi);
  const mPct      = monthHasData(local, mi) && mMax > 0 ? (mRaw / mMax) * 100 : null;
  const annual    = useMemo(() => annualScorePct(local), [local]);

  return (
    <div>
      {/* Top bar */}
      <div className="flex items-start justify-between mb-5 flex-wrap gap-3">
        <div>
          <h2 className="text-lg font-semibold">{member.name}</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Enter raw scores up to each KPI's max mark. Quarterly KPIs only scored in Jun, Sep, Dec, Mar.
          </p>
        </div>
        <div className="flex items-center gap-5">
          <div className="text-center">
            <p className="text-xs text-slate-500 uppercase tracking-wide">Annual (YTD)</p>
            <ScoreBadge value={annual} size="lg" />
          </div>
          <button onClick={handleSave}
            className={`flex items-center gap-1.5 font-semibold px-5 py-2.5 rounded-xl text-sm transition-colors ${saved ? "bg-emerald-500 text-slate-950" : "bg-amber-500 hover:bg-amber-400 text-slate-950"}`}>
            <Save className="w-4 h-4" /> {saved ? "Saved!" : "Save"}
          </button>
        </div>
      </div>

      {/* Month tabs */}
      <div className="flex gap-1.5 flex-wrap mb-4">
        {MONTHS.map((m, i) => {
          const has = monthHasData(local, i);
          const raw = monthRawScore(local, i);
          return (
            <button key={i} onClick={() => setActiveMonth(i)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors
                ${activeMonth === i ? "bg-amber-500 text-slate-950" :
                  has ? "border border-amber-500/40 text-amber-400/80 hover:border-amber-500 hover:text-amber-400" :
                  "border border-slate-700 text-slate-400 hover:border-slate-500 hover:text-slate-200"}`}>
              {m}{has && <span className="ml-1 opacity-60">·{raw}</span>}
            </button>
          );
        })}
      </div>

      {/* Month summary banner */}
      <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 mb-5 flex items-center justify-between flex-wrap gap-4">
        <div>
          <p className="font-semibold">{MONTHS[mi]}</p>
          <p className="text-xs text-slate-500 mt-0.5">Available marks this month: {mMax}</p>
        </div>
        <div className="flex items-center gap-8">
          <div className="text-center">
            <p className="text-xs text-slate-500 uppercase tracking-wide mb-1">Raw Score</p>
            <p className="font-mono font-bold text-2xl text-slate-100">
              {mRaw}<span className="text-slate-500 text-base font-normal">/{mMax}</span>
            </p>
          </div>
          <div className="text-center">
            <p className="text-xs text-slate-500 uppercase tracking-wide mb-1">Month %</p>
            <ScoreBadge value={mPct} size="lg" />
          </div>
        </div>
      </div>

      {/* KPI input cards per goal */}
      <div className="space-y-4">
        {GOALS.map(g => {
          const gRaw = goalMonthRaw(local, g, mi);
          const gPct = (gRaw / 20) * 100;
          return (
            <div key={g.id} className="bg-slate-900 border border-slate-800 rounded-2xl overflow-hidden">
              {/* Goal header */}
              <div className="flex items-center justify-between px-4 py-3 border-b border-slate-800 bg-slate-900/80">
                <div className="flex items-center gap-2">
                  <span className="text-amber-400 font-bold text-sm">Goal {g.id}</span>
                  <span className="text-slate-200 text-sm">{g.name}</span>
                  <span className="text-slate-600 text-xs">(20 marks)</span>
                </div>
                <div className="text-right">
                  <p className="text-xs text-slate-500 mb-0.5">This month</p>
                  <p className="font-mono font-bold text-sm text-slate-100">
                    {gRaw}<span className="text-slate-500 font-normal">/20</span>
                    <span className={`ml-2 ${scoreColor(gPct)}`}>({Math.round(gPct)}%)</span>
                  </p>
                </div>
              </div>

              {/* KPI rows */}
              <div className="divide-y divide-slate-800/60">
                {g.kpis.map(k => {
                  const isQOnly = k.quarterly && !QUARTER_END.has(mi);
                  const val = local[k.id]?.[mi];
                  return (
                    <div key={k.id} className={`flex items-center gap-4 px-4 py-3 ${isQOnly ? "opacity-35" : ""}`}>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm text-slate-200 leading-snug">{k.text}</p>
                        <p className="text-xs text-slate-600 mt-0.5">
                          Max: <span className="text-slate-400">{k.max} marks</span>
                          {k.quarterly && <span className="ml-2 text-amber-600">· quarter-end months only</span>}
                        </p>
                      </div>
                      <div className="shrink-0 flex items-center gap-2">
                        {isQOnly
                          ? <span className="text-xs text-slate-600 w-28 text-center">N/A this month</span>
                          : <>
                              <input
                                type="number" min={0} max={k.max}
                                value={val ?? ""}
                                onChange={e => setVal(k.id, mi, e.target.value)}
                                placeholder="0"
                                className="w-20 bg-slate-950 border border-slate-700 rounded-lg px-2 py-2 text-center font-mono text-slate-100 text-sm focus:outline-none focus:ring-2 focus:ring-amber-500"
                              />
                              <span className="text-slate-500 text-xs w-8">/ {k.max}</span>
                            </>
                        }
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      {/* Annual goal averages */}
      <div className="mt-5 grid grid-cols-5 gap-3">
        {GOALS.map(g => {
          const vals = MONTHS.map((_, i) => {
            if (!monthHasData(local, i)) return null;
            return (goalMonthRaw(local, g, i) / 20) * 100;
          }).filter(v => v !== null);
          const avg = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
          return (
            <div key={g.id} className="bg-slate-900 border border-slate-800 rounded-xl p-3">
              <p className="text-xs text-slate-500 leading-tight">{g.id} · {g.name}</p>
              <p className="text-xs text-slate-600 mt-1">Annual avg</p>
              <div className="mt-0.5"><ScoreBadge value={avg} size="sm" /></div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── MEMBER VIEW ──────────────────────────────────────────────
function MemberView({ member, scores, onLogout }) {
  const annual = annualScorePct(scores);

  const chartData = MONTHS.map((m, mi) => {
    if (!monthHasData(scores, mi)) return { month: m, score: null };
    const raw = monthRawScore(scores, mi);
    const max = monthMaxMarks(mi);
    return { month: m, score: Math.round((raw / max) * 100) };
  });

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 px-6 py-4 flex items-center justify-between sticky top-0 bg-slate-950/95 backdrop-blur z-10">
        <div className="flex items-center gap-2">
          <ShieldCheck className="w-5 h-5 text-amber-400" />
          <span className="font-semibold">SMTI Appraisal Tracker</span>
          <span className="text-slate-600 text-sm hidden sm:block">· {member.name}</span>
        </div>
        <button onClick={onLogout} className="flex items-center gap-1 text-sm text-slate-400 hover:text-slate-200">
          <LogOut className="w-4 h-4" /> Log out
        </button>
      </header>

      <main className="p-5 max-w-4xl mx-auto space-y-6">

        {/* Annual score card */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6">
          <p className="text-sm text-slate-400">Track your performance in the team.</p>
          <p className="text-xs text-slate-600 mt-0.5">May 2026 – April 2027 · Next appraisal: May 2027</p>
          <div className="mt-4">
            <p className="text-xs text-slate-500 uppercase tracking-wide">Annual Score (YTD)</p>
            <ScoreBadge value={annual} size="lg" />
          </div>
          <p className="text-xs text-slate-600 mt-3">Scores are entered by your manager monthly and averaged across the appraisal period.</p>
        </div>

        {/* Monthly scores grid */}
        <div>
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-3">Monthly Scores</h3>
          <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-2">
            {MONTHS.map((m, mi) => {
              const has  = monthHasData(scores, mi);
              const raw  = monthRawScore(scores, mi);
              const max  = monthMaxMarks(mi);
              const pct  = has && max > 0 ? Math.round((raw / max) * 100) : null;
              return (
                <div key={mi} className="bg-slate-900 border border-slate-800 rounded-xl p-3 text-center">
                  <p className="text-xs text-slate-500">{m}</p>
                  {has
                    ? <>
                        <p className={`font-mono font-bold text-xl mt-1 ${scoreColor(pct)}`}>{pct}%</p>
                        <p className="text-xs text-slate-600 mt-0.5">{raw}/{max}</p>
                      </>
                    : <p className="text-slate-700 text-lg mt-1">—</p>
                  }
                </div>
              );
            })}
          </div>
        </div>

        {/* Trend chart */}
        <div>
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-3">Monthly Trend</h3>
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-4 h-56">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                <XAxis dataKey="month" stroke="#475569" fontSize={10} />
                <YAxis stroke="#475569" fontSize={10} domain={[0, 100]} tickFormatter={v => `${v}%`} />
                <Tooltip
                  contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", fontSize: 12, borderRadius: 8 }}
                  formatter={(v) => v !== null ? [`${v}%`, "Score"] : ["—", "Score"]}
                />
                <ReferenceLine y={70} stroke="#334155" strokeDasharray="4 4" />
                <Bar dataKey="score" radius={[4, 4, 0, 0]} fill="#f59e0b" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Goal breakdown */}
        <div>
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-3">Goal Breakdown</h3>
          <div className="space-y-3">
            {GOALS.map(g => {
              const monthlyPcts = MONTHS.map((_, mi) => {
                if (!monthHasData(scores, mi)) return null;
                return (goalMonthRaw(scores, g, mi) / 20) * 100;
              }).filter(v => v !== null);
              const goalAnnual = monthlyPcts.length
                ? monthlyPcts.reduce((a, b) => a + b, 0) / monthlyPcts.length
                : null;

              return (
                <div key={g.id} className="bg-slate-900 border border-slate-800 rounded-2xl overflow-hidden">
                  <div className="flex items-center justify-between px-4 py-3 border-b border-slate-800">
                    <div>
                      <span className="text-amber-400 font-semibold text-sm">Goal {g.id}</span>
                      <span className="text-slate-200 text-sm ml-2">{g.name}</span>
                      <span className="text-slate-600 text-xs ml-2">(20 marks · 20% weight)</span>
                    </div>
                    <div className="text-right">
                      <p className="text-xs text-slate-500">Annual avg</p>
                      <ScoreBadge value={goalAnnual} />
                    </div>
                  </div>
                  <div className="divide-y divide-slate-800/50">
                    {g.kpis.map(k => {
                      const kpiVals = MONTHS.map((_, mi) => {
                        if (k.quarterly && !QUARTER_END.has(mi)) return null;
                        if (!monthHasData(scores, mi)) return null;
                        const v = scores?.[k.id]?.[mi];
                        return (v !== "" && v !== null && v !== undefined) ? Number(v) : null;
                      }).filter(v => v !== null);
                      const kpiAvg = kpiVals.length ? kpiVals.reduce((a, b) => a + b, 0) / kpiVals.length : null;
                      return (
                        <div key={k.id} className="flex items-center justify-between px-4 py-2.5 gap-3">
                          <p className="text-xs text-slate-400 flex-1 leading-snug">
                            {k.text}
                            {k.quarterly && <span className="text-slate-600"> · quarterly</span>}
                          </p>
                          <div className="text-right shrink-0">
                            <p className="text-xs text-slate-600">avg score</p>
                            {kpiAvg !== null
                              ? <span className="text-xs font-mono font-semibold text-slate-300">{kpiAvg.toFixed(1)}/{k.max}</span>
                              : <span className="text-slate-600 text-xs">—</span>
                            }
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </main>
    </div>
  );
}
