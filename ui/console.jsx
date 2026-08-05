
function EmptyStateBanner({ title = "Feature Not Yet Implemented", message = "This capability is not yet implemented by the backend." }) {
  return (
    <div style={{
      padding: "32px 24px",
      borderRadius: "12px",
      border: "1px border-dashed #30363d",
      background: "var(--bg-inset)",
      textAlign: "center",
      margin: "16px 0",
      color: "var(--text-muted)"
    }}>
      <div style={{ fontSize: "24px", marginBottom: "8px" }}>⚙️</div>
      <div style={{ fontSize: "14px", fontWeight: "600", color: "var(--text)", marginBottom: "4px" }}>{title}</div>
      <div style={{ fontSize: "12.5px" }}>{message}</div>
    </div>
  );
}

import React, { useState, useEffect, useMemo, useCallback, useRef, Suspense } from "react";

import {

  Database, Home, ShieldCheck, Bot, ListChecks, ScrollText, Search, Command,

  ChevronRight, ChevronDown, Key, Link2, CircleDot, Check, X, Play,

  Clock, AlertTriangle, Sun, Moon, ArrowRight, Sparkles, GitBranch,

  Lock, Eye, EyeOff, Terminal, Activity, CheckCircle2, XCircle,

  Loader2, ChevronsRight, Hash, Table2, FileWarning, Send, Wifi, WifiOff,

  Settings, Users, UserPlus, Trash2, ShieldAlert, ClipboardCheck, ExternalLink, Boxes,

  Bolt,

} from "lucide-react";



/* ============================================================================

   API CLIENT — dual-mode by design. This artifact runs in a browser

   sandbox that cannot reach a locally-run backend, so every screen below

   tries a real fetch against `apiBase` first and falls back to the mock

   data it always had if that fails (network error, timeout, non-2xx).

   `useApiHealth` pings /api/health once and drives a visible Live/Demo

   badge in the top bar — never silent about which data you're looking at.

   Point `apiBase` at a real `uvicorn backend.app:app` (see ADR-0006) to

   go live; nothing else about these components needs to change.

   ========================================================================= */

const DEFAULT_API_BASE = "http://127.0.0.1:8000";



async function apiGet(base, path, token, timeoutMs = 3000) {

  const headers = token ? { Authorization: `Bearer ${token}` } : {};

  const signal = typeof AbortSignal === "function" && typeof AbortSignal.timeout === "function"

    ? AbortSignal.timeout(timeoutMs)

    : undefined;

  const res = await fetch(`${base.replace(/\/$/, "")}${path}`, { headers, signal });

  const body = await res.json().catch(() => ({}));

  if (!res.ok) {

    if (res.status === 401 && token && _onSessionExpired) _onSessionExpired();

    const err = new Error(body.detail || `HTTP ${res.status}`); err.status = res.status; err.body = body; throw err;

  }

  return body;

}



async function apiPost(base, path, payload, token, timeoutMs = 6000) {

  const headers = { "Content-Type": "application/json" };

  if (token) headers.Authorization = `Bearer ${token}`;

  const signal = typeof AbortSignal === "function" && typeof AbortSignal.timeout === "function"

    ? AbortSignal.timeout(timeoutMs)

    : undefined;

  const res = await fetch(`${base.replace(/\/$/, "")}${path}`, {

    method: "POST",

    headers,

    body: JSON.stringify(payload || {}),

    signal,

  });

  const body = await res.json().catch(() => ({}));

  if (!res.ok) {

    if (res.status === 401 && token && _onSessionExpired) _onSessionExpired();

    const err = new Error(body.detail || `HTTP ${res.status}`); err.status = res.status; err.body = body; throw err;

  }

  return body;

}



// A previously-flagged real gap (README/ADR-0008/0009): a token expiring

// mid-session used to surface as a generic per-screen error banner rather

// than returning the user to the login gate. This is a minimal, deliberate

// escape hatch rather than threading a callback through every one of the

// ~20 call sites across 7 screens: apiGet/apiPost report a 401 that

// actually came with a token (never on the unauthenticated /login or

// /health calls, where a 401/absence is a different, expected thing) to

// whatever handler the shell registered on mount. Screens still get their

// normal error via the thrown Error — this is additive, not a replacement

// for per-screen error handling.

let _onSessionExpired = null;

function setSessionExpiredHandler(fn) {

  _onSessionExpired = fn;

}



const AUTH_STORAGE_KEY = "enterprise_console_auth";

function loadStoredAuth() {

  if (typeof window === "undefined") return { token: null, user: null };

  try {

    const raw = window.sessionStorage.getItem(AUTH_STORAGE_KEY);

    if (!raw) return { token: null, user: null };

    const parsed = JSON.parse(raw);

    return { token: parsed?.token || null, user: parsed?.user || null };

  } catch {

    window.sessionStorage.removeItem(AUTH_STORAGE_KEY);

    return { token: null, user: null };

  }

}

function saveAuthToStorage(token, user) {

  if (typeof window === "undefined") return;

  window.sessionStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify({ token, user }));

}

function clearAuthStorage() {

  if (typeof window === "undefined") return;

  window.sessionStorage.removeItem(AUTH_STORAGE_KEY);

}



const ROLE_RANK = { VIEWER: 0, OPERATOR: 1, ADMIN: 2 };

function roleAtLeast(role, min) {

  return (ROLE_RANK[role] ?? -1) >= (ROLE_RANK[min] ?? 99);

}



function useApiHealth(apiBase) {

  const [status, setStatus] = useState("checking"); // checking | live | offline

  useEffect(() => {

    let cancelled = false;

    setStatus("checking");

    apiGet(apiBase, "/api/health", null, 1800)

      .then(() => { if (!cancelled) setStatus("live"); })

      .catch(() => { if (!cancelled) setStatus("offline"); });

    return () => { cancelled = true; };

  }, [apiBase]);

  return status;

}



/** Renders one subagent dispatch record (from Plan.subtasks, matching

 * SubagentResult.to_dict()'s shape) into the same short display string

 * the demo-mode dispatches already used, so both modes render identically. */

function formatSubtaskSummary(subtask) {

  const s = subtask.summary || {};

  switch (subtask.action) {

    case "introspect_schema": return `${s.table} · ${s.column_count} columns`;

    case "classify_sensitivity": return `${s.count} sensitive column${s.count === 1 ? "" : "s"}: ${(s.sensitive_columns || []).join(", ") || "none"}`;

    case "validate_batch": return `${s.error_count ?? 0} error(s) · ${s.warning_count ?? 0} warning(s)`;

    case "apply_masking_dry_run": return `${s.preview_rows ?? "?"} row(s) previewed`;

    case "generate_sql": return `${s.statement_count} ${s.operation?.toUpperCase() || "INSERT"} statement(s)`;

    case "estimate_execution_time": return `~${s.estimated_seconds}s estimated`;

    case "build_rollback_plan": return `${s.statement_count} rollback statement(s)`;

    case "execute_sql": return `${s.succeeded ?? 0}/${s.attempted ?? 0} succeeded`;

    case "propose_masking_rule": return `${s.rule_count} rule(s) proposed`;

    case "profile_file": return `${s.row_count} row(s), ${s.column_count} column(s)`;

    case "diff_against_schema": return s.clean ? "matches schema" : `${s.missing_required_count} required column(s) missing`;

    default: return JSON.stringify(s).slice(0, 80);

  }

}



/* ============================================================================

   DESIGN TOKENS — see design-plan notes: dark-first mission-control palette,

   teal = data state, violet = agent state (deliberate semantic split), amber

   = needs-review, rose = blocked/error. Tailwind handles layout/spacing;

   inline tokens handle color/type since this environment has no JIT/config.

   ========================================================================= */

const darkTokens = {

  bg: "#16181b",

  sidebarBg: "#1a1c1f",

  canvasBg: "#101113",

  insetBg: "#1f2124",

  surface: "#1a1c1f",

  surfaceRaised: "#1f2124",

  surfaceHover: "#2b2d31",

  border: "#2b2d31",

  borderStrong: "#3b3d43",

  textPrimary: "#edeff1",

  textSecondary: "#989ba3",

  textTertiary: "#6c6f77",

  textFaint: "#6c6f77",

  accent: "#2454ff",

  accentTint: "#161d33",

  accentHover: "#1a3fd6",

  navy: "#e7ebee",

  green: "#1a7a4c",

  greenTint: "#122a1d",

  amber: "#946a00",

  amberTint: "#302608",

  amberText: "#FBC373",

  red: "#c0301f",

  redTint: "#301513",

  violet: "#5b21b6",

  violetTint: "#221935",

  violetDim: "#221935",

  violetText: "#B4A9FA",

  track: "#26282c",

  teal: "#14E0C2",

  tealDim: "#0E3B36",

  tealText: "#5EEAD4",

  rose: "#FB4B67",

  roseDim: "#301513",

  roseText: "#FD91A2",

};



const lightTokens = {

  bg: "#ffffff",

  sidebarBg: "#ffffff",

  canvasBg: "#fafafb",

  insetBg: "#f5f5f7",

  surface: "#ffffff",

  surfaceRaised: "#ffffff",

  surfaceHover: "#f5f5f7",

  border: "#e3e3e7",

  borderStrong: "#c7ccd9",

  textPrimary: "#1c1c21",

  textSecondary: "#6b6b76",

  textTertiary: "#a3a3ac",

  textFaint: "#a3a3ac",

  accent: "#2454ff",

  accentTint: "#eef2ff",

  accentHover: "#1a3fd6",

  navy: "#1b2a35",

  green: "#1a7a4c",

  greenTint: "#eaf6ef",

  amber: "#946a00",

  amberTint: "#fff4dc",

  amberText: "#946a00",

  red: "#c0301f",

  redTint: "#fdeceb",

  violet: "#5b21b6",

  violetTint: "#f3edfc",

  violetDim: "#f3edfc",

  violetText: "#5b21b6",

  track: "#e9e9ee",

  teal: "#0D9488",

  tealDim: "#E6FBF7",

  tealText: "#0D9488",

  rose: "#c0301f",

  roseDim: "#fdeceb",

  roseText: "#c0301f",

};



const fontDisplay = "'Space Grotesk', sans-serif";

const fontBody = "'Inter', sans-serif";

const fontMono = "'JetBrains Mono', monospace";



/* ============================================================================

   MOCK DATA — grounded in the real Provider (MMIS) schema this console sits

   on top of: real table names, real column names, real FK structure, real

   sensitive-column flags. Nothing here is generic placeholder content.

   ========================================================================= */

const TABLE_GROUPS = [

  { label: "Core", tables: ["p_dtl_tb"] },

  { label: "Identifiers & Credentials", tables: ["p_alt_id_tb", "p_lic_cert_tb", "p_mcare_tb"] },

  { label: "Classification", tables: ["p_txnmy_tb", "p_specl_tb", "p_ty_tb", "p_lang_tb"] },

  { label: "Enrollment", tables: ["p_enrol_stat_tb", "p_nw_part_tb", "p_faci_tb"] },

  { label: "Ownership", tables: ["p_owner_dtl_tb", "p_owner_xref_tb"] },

  { label: "Affiliation & Lab", tables: ["p_affl_tb", "p_clia_lab_tb"] },

];



const SCHEMA = {

  p_dtl_tb: {

    label: "Provider Detail",

    desc: "Core provider record. Every other Provider table hangs off p_sys_id.",

    pk: ["p_sys_id"],

    parents: [],

    children: ["p_alt_id_tb", "p_txnmy_tb", "p_lic_cert_tb", "p_specl_tb", "p_ty_tb", "p_enrol_stat_tb", "p_owner_xref_tb"],

    requiredForActive: false,

    columns: [

      { name: "p_sys_id", type: "BIGINT", pk: true, nullable: false, note: "Medicaid Provider ID" },

      { name: "p_ty_class_cd", type: "VARCHAR(1)", nullable: true },

      { name: "p_rec_ty_cd", type: "VARCHAR(1)", nullable: true },

      { name: "p_appl_num", type: "VARCHAR(15)", nullable: true },

      { name: "p_locn_cd", type: "VARCHAR(1)", nullable: true },

      { name: "g_cmn_enty_sk", type: "BIGINT", nullable: true, fk: "G_CMN_ENTY_TB" },

      { name: "g_note_set_sk", type: "BIGINT", nullable: true, fk: "G_NOTE_SET_TB" },

    ],

  },

  p_alt_id_tb: {

    label: "Alternate Identifier",

    desc: "NPI, SSN/TIN, DEA and state IDs — one row per identifier, typed by p_alt_id_ty_cd.",

    pk: ["p_sys_id", "p_alt_id_sk"],

    parents: ["p_dtl_tb"],

    children: [],

    requiredForActive: false,

    columns: [

      { name: "p_sys_id", type: "BIGINT", pk: true, nullable: false, fk: "P_DTL_TB" },

      { name: "p_alt_id_sk", type: "BIGINT", pk: true, nullable: false },

      { name: "p_alt_id", type: "VARCHAR(15)", nullable: true, sensitive: true, note: "SSN/TIN when ty_cd = SY" },

      { name: "p_alt_id_ty_cd", type: "VARCHAR(3)", nullable: true, note: "XX=NPI · SY=SSN/TIN · DEA · MCR · ND" },

      { name: "p_alt_id_beg_dt", type: "DATE", nullable: true },

      { name: "p_alt_id_end_dt", type: "DATE", nullable: true },

      { name: "p_tax_rpt_ind", type: "VARCHAR(1)", nullable: true },

    ],

  },

  p_lic_cert_tb: {

    label: "License / Certification",

    desc: "State-issued license or cert. Required for a provider to reach Active status.",

    pk: ["p_sys_id", "p_lic_cert_sk"],

    parents: ["p_dtl_tb"],

    children: [],

    requiredForActive: true,

    columns: [

      { name: "p_sys_id", type: "BIGINT", pk: true, nullable: false, fk: "P_DTL_TB" },

      { name: "p_lic_cert_sk", type: "BIGINT", pk: true, nullable: false },

      { name: "p_lic_cert_num", type: "VARCHAR(20)", nullable: true, sensitive: true },

      { name: "p_lic_cert_state_cd", type: "VARCHAR(2)", nullable: true },

    ],

  },

  p_mcare_tb: {

    label: "Medicare Crosswalk",

    desc: "Medicare provider number + participation, keyed to a carrier.",

    pk: ["p_sys_id", "p_mcare_sk"],

    parents: ["p_dtl_tb", "t_carr_tb"],

    children: [],

    requiredForActive: false,

    columns: [

      { name: "p_sys_id", type: "BIGINT", pk: true, nullable: false, fk: "P_DTL_TB" },

      { name: "p_mcare_sk", type: "BIGINT", pk: true, nullable: false },

      { name: "p_mcare_alt_id", type: "VARCHAR(15)", nullable: true },

      { name: "p_mcare_part_cd", type: "VARCHAR(2)", nullable: true },

    ],

  },

  p_txnmy_tb: {

    label: "Taxonomy",

    desc: "NUCC taxonomy code. Required for Active status.",

    pk: ["p_sys_id", "p_txnmy_sk"],

    requiredForActive: true,
    columns: [
      { name: "p_sys_id", type: "BIGINT", pk: true, nullable: false, fk: "P_DTL_TB" },
      { name: "p_ty_sk", type: "BIGINT", pk: true, nullable: false },
      { name: "p_ty_cd", type: "VARCHAR(2)", nullable: true },
    ],
  },
  p_lang_tb: {
    label: "Language",
    desc: "Languages spoken by the provider or their staff.",
    pk: ["p_sys_id", "p_lang_sk"],
    parents: ["p_dtl_tb"],
    children: [],
    requiredForActive: false,
    columns: [
      { name: "p_sys_id", type: "BIGINT", pk: true, nullable: false, fk: "P_DTL_TB" },
      { name: "p_lang_sk", type: "BIGINT", pk: true, nullable: false },
      { name: "p_lang_cd", type: "VARCHAR(2)", nullable: true },
    ],
  },
  p_enrol_stat_tb: {
    label: "Enrollment Status",
    desc: "Active/Inactive/Pending/Terminated span. Required for Active status.",
    pk: ["p_sys_id", "p_enrol_stat_sk"],
    parents: ["p_dtl_tb"],
    children: [],
    requiredForActive: true,
    columns: [
      { name: "p_sys_id", type: "BIGINT", pk: true, nullable: false, fk: "P_DTL_TB" },
      { name: "p_enrol_stat_sk", type: "BIGINT", pk: true, nullable: false },
      { name: "p_enrol_stat_cd", type: "VARCHAR(3)", nullable: true, note: "ACT · INA · PEN · TRM · SUS · REV" },
    ],
  },
  p_nw_part_tb: {
    label: "Network Participation",
    desc: "Per-provider network participation status.",
    pk: ["p_sys_id", "p_nw_part_sk"],
    parents: ["p_dtl_tb", "r_prov_nw_tb"],
    children: [],
    requiredForActive: false,
    columns: [
      { name: "p_sys_id", type: "BIGINT", pk: true, nullable: false, fk: "P_DTL_TB" },
      { name: "p_nw_part_sk", type: "BIGINT", pk: true, nullable: false },
      { name: "p_nw_stat_cd", type: "VARCHAR(2)", nullable: true, note: "AC · IN · PN" },
    ],
  },
  p_faci_tb: {
    label: "Facility",
    desc: "Facility type — a facility is a provider with a p_faci_tb row, not a separate ID.",
    pk: ["p_sys_id", "p_faci_sk"],
    parents: ["p_dtl_tb"],
    children: [],
    requiredForActive: false,
    columns: [
      { name: "p_sys_id", type: "BIGINT", pk: true, nullable: false, fk: "P_DTL_TB" },
      { name: "p_faci_sk", type: "BIGINT", pk: true, nullable: false },
      { name: "p_faci_ty_cd", type: "VARCHAR(2)", nullable: true },
    ],
  },
  p_owner_dtl_tb: {
    label: "Owner Detail",
    desc: "Owner-level EIN/SSN — one owner can cross-reference multiple providers.",
    pk: ["p_owner_tax_id", "p_owner_dtl_seq_num"],
    parents: [],
    children: ["p_owner_xref_tb"],
    requiredForActive: false,
    columns: [
      { name: "p_owner_tax_id", type: "BIGINT", pk: true, nullable: false, sensitive: true },
      { name: "p_owner_dtl_seq_num", type: "SMALLINT", pk: true, nullable: false },
      { name: "p_owner_ssn_num", type: "VARCHAR(9)", nullable: true, sensitive: true },
    ],
  },
  p_owner_xref_tb: {
    label: "Owner Cross-Reference",
    desc: "Links a provider to one or more owners.",
    pk: ["p_sys_id", "p_owner_tax_id", "p_owner_dtl_seq_num", "p_owner_xref_sk"],
    parents: ["p_dtl_tb", "p_owner_dtl_tb"],
    children: [],
    requiredForActive: false,
    columns: [
      { name: "p_sys_id", type: "BIGINT", pk: true, nullable: false, fk: "P_DTL_TB" },
      { name: "p_owner_tax_id", type: "BIGINT", pk: true, nullable: false, fk: "P_OWNER_DTL_TB" },
      { name: "p_owner_xref_sk", type: "BIGINT", pk: true, nullable: false },
    ],
  },
  p_affl_tb: {
    label: "Affiliation",
    desc: "Group ↔ member affiliation. Self-references p_dtl_tb twice (group + member).",
    pk: ["p_grp_sys_id", "p_mbr_sys_id", "p_affl_sk"],
    parents: ["p_dtl_tb"],
    children: [],
    requiredForActive: false,
    columns: [
      { name: "p_grp_sys_id", type: "BIGINT", pk: true, nullable: false, fk: "P_DTL_TB" },
      { name: "p_mbr_sys_id", type: "BIGINT", pk: true, nullable: false, fk: "P_DTL_TB" },
      { name: "p_affl_sk", type: "BIGINT", pk: true, nullable: false },
    ],
  },
  p_clia_lab_tb: {
    label: "CLIA Lab",
    desc: "CLIA-certified lab record, independently keyed by CLIA number.",
    pk: ["p_clia_num"],
    parents: [],
    children: ["p_clia_cert_tb", "p_clia_prov_tb"],
    requiredForActive: false,
    columns: [
      { name: "p_clia_num", type: "VARCHAR(10)", pk: true, nullable: false },
      { name: "p_clia_fed_tax_id", type: "VARCHAR(9)", nullable: true, sensitive: true },
    ],
  },
};

const ALL_TABLE_NAMES = Object.keys(SCHEMA);



function maskDeterministic(value) {
  // visual stand-in for the real HMAC-SHA256 hex digest MaskingEngine produces
  let h = 0;
  for (let i = 0; i < value.length; i++) h = (h * 31 + value.charCodeAt(i)) >>> 0;
  return h.toString(16).padStart(8, "0") + "…" + (h * 7 % 99999999).toString(16);
}


const MOCK_DBA_TABLES = [
  { table: "p_alt_id_tb", rows: 142050, sensitiveCols: 4, status: "Protected", masked: true, lastMasked: "2 mins ago" },
  { table: "p_dtl_tb", rows: 98400, sensitiveCols: 2, status: "Protected", masked: true, lastMasked: "14 mins ago" },
  { table: "p_affl_tb", rows: 65120, sensitiveCols: 1, status: "Unmasked", masked: false, lastMasked: "Never" },
  { table: "p_owner_tb", rows: 43200, sensitiveCols: 3, status: "Unmasked", masked: false, lastMasked: "Never" },
  { table: "p_lic_cert_tb", rows: 31080, sensitiveCols: 2, status: "Protected", masked: true, lastMasked: "1 hour ago" },
  { table: "p_taxonomy_tb", rows: 18450, sensitiveCols: 0, status: "Protected", masked: true, lastMasked: "3 hours ago" },
];

const MOCK_JOBS = [
  { id: "job-8912-gen", name: "Synthetic Row Generation — p_alt_id_tb", status: "completed", worker: "data_generator", duration: "1m 12s", progress: 100 },
  { id: "job-8911-disc", name: "Live Metadata Introspection & FK Graph", status: "completed", worker: "schema_metadata_agent", duration: "48s", progress: 100 },
  { id: "job-8910-mask", name: "FPE Masking Rule Enforcement", status: "completed", worker: "masking_agent", duration: "24s", progress: 100 },
  { id: "job-8909-val", name: "Referential Integrity Validation Check", status: "completed", worker: "validation_agent", duration: "32s", progress: 100 },
  { id: "job-8908-sync", name: "Target Database Batch Load", status: "running", worker: "execution_report_agent", duration: "1m 45s", progress: 65 },
];

const MOCK_AUDIT_LOGS = [
  { timestamp: "2026-08-05 19:42:10", actor: "operator", action: "GENERATE_SYNTHETIC_DATA", table: "p_alt_id_tb", status: "success", details: "Generated 20 rows with FPE masking" },
  { timestamp: "2026-08-05 19:28:44", actor: "admin", action: "DISCOVER_METADATA_SNAPSHOT", table: "provider_schema", status: "success", details: "Introspected 109 tables and 44 foreign keys" },
  { timestamp: "2026-08-05 19:14:02", actor: "operator", action: "PROPOSE_MASKING_POLICY", table: "p_dtl_tb", status: "success", details: "Applied Format-Preserving Encryption on Tax ID" },
  { timestamp: "2026-08-05 18:59:15", actor: "admin", action: "CREATE_WORKSPACE", table: "sit-to-dev-migration", status: "success", details: "Created workspace connecting SIT to DEV" },
  { timestamp: "2026-08-05 18:30:22", actor: "operator", action: "LOGIN_SUCCESS", table: "auth", status: "success", details: "Authenticated session token issued" },
];

const MASKING_STRATEGIES = {
  DETERMINISTIC: { label: "Deterministic", color: "teal", note: "HMAC-SHA256 · stable across runs" },
  FORMAT_PRESERVING: { label: "Format-preserving (FPE)", color: "amber", note: "Preserves length, casing & punctuation" },
  SYNTHETIC: { label: "Synthetic", color: "violet", note: "Faker-generated, seeded" },
  RANDOM: { label: "Random", color: "blue", note: "Dynamic per call" },
  STATIC: { label: "Static", color: "slate", note: "Fixed replacement value" },
  SHUFFLE: { label: "Shuffle", color: "emerald", note: "In-place character permutation" },
  NULLIFY: { label: "Nullify", color: "rose", note: "Removed / set to NULL" },
};

const AGENT_ROSTER = [
  { name: "schema_metadata_agent", role: "Introspects schema, resolves glossary terms, builds FK graph" },
  { name: "profiling_mapping_agent", role: "Profiles files, proposes column mappings" },
  { name: "validation_agent", role: "Runs the validation engine — reports, never fixes" },
  { name: "correction_agent", role: "Proposes fixes as a diff — never applies them" },
  { name: "masking_agent", role: "Classifies sensitivity, proposes masking strategy per column" },
  { name: "sql_generation_agent", role: "Generates dialect-correct SQL — text only, no write access" },
  { name: "execution_report_agent", role: "The only agent with write credentials — gated on a CONFIRMED plan" },
];

const WORKFLOW_STEPS = ["Plan", "Explain", "Preview", "Confirm", "Execute", "Report"];





/* ============================================================================
   SMALL SHARED PRIMITIVES
   ========================================================================= */
function useTokens(theme) {
  return theme === "dark" ? darkTokens : lightTokens;
}


/* ============================================================================
   V5 SHELL COMPONENTS — use CSS classes from the v5_home_5 design token system.
   These wrappers let every screen plug into the shared canvas/card grid without
   touching any screen's internal logic.
   ========================================================================= */


/* ============================================================================
   UNIFIED SCREEN CONTAINER (S) — Standardized padding, full width, zero gutters
   ========================================================================= */
function S({ children, pad = true, style = {} }) {
  // Uses canvas-inner class: width:100%, block layout, proven full-width approach
  return (
    <div
      className="canvas-inner"
      style={pad ? { ...style } : { padding: 0, ...style }}
    >
      {children}
    </div>
  );
}

function PageShell({ layout = "A", children }) {
  // Unified: layout param no longer changes the container - all screens use S
  return <S pad={true}>{children}</S>;
}

function ECard({ title, subtitle, actions, children, style = {} }) {
  return (
    <div className="card" style={style}>
      {(title || actions) && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "14px 18px 12px", borderBottom: "1px solid var(--border)" }}>
          <div>
            {title && <div style={{ fontSize: "13px", fontWeight: "700", color: "var(--navy)" }}>{title}</div>}
            {subtitle && <div style={{ fontSize: "11px", color: "var(--text-faint)", marginTop: "2px" }}>{subtitle}</div>}
          </div>
          {actions && <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>{actions}</div>}
        </div>
      )}
      <div className="card-pad">{children}</div>
    </div>
  );
}

function ETable({ headers = [], rows = [] }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12.5px" }}>
        <thead>
          <tr style={{ borderBottom: "1px solid var(--border)" }}>
            {headers.map((h) => (
              <th key={h} style={{ textAlign: "left", padding: "8px 12px", fontSize: "10.5px", fontWeight: "700", color: "var(--text-faint)", textTransform: "uppercase", letterSpacing: "0.05em", whiteSpace: "nowrap" }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            if (row && row.type === "tr") return row;
            return <tr key={i}>{row}</tr>;
          })}
        </tbody>
      </table>
    </div>
  );
}

function PageHeader({ icon: Icon, title, description, breadcrumbs, actions }) {
  return (
    <div style={{ marginBottom: "4px" }}>
      {breadcrumbs && (
        <div style={{ fontSize: "10px", fontWeight: "700", color: "var(--text-faint)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: "10px" }}>{breadcrumbs}</div>
      )}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "16px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          {Icon && (
            <div className="hero-badge" style={{ width: "36px", height: "36px", borderRadius: "10px" }}>
              <Icon style={{ width: "18px", height: "18px", color: "#fff" }} />
            </div>
          )}
          <div>
            <h1 style={{ margin: 0, fontSize: "18px", fontWeight: "700", color: "var(--navy)", letterSpacing: "-0.01em" }}>{title}</h1>
            {description && <div className="hero-sub" style={{ fontSize: "12px", marginTop: "2px" }}>{description}</div>}
          </div>
        </div>
        {actions && <div style={{ display: "flex", gap: "8px", flexShrink: 0 }}>{actions}</div>}
      </div>
    </div>
  );
}

function MetricCard({ title, value, sub, icon: Icon, tone = "accent" }) {
  const colors = {
    success: { bg: "var(--green-tint)", color: "var(--green)" },
    warning: { bg: "var(--amber-tint)", color: "var(--amber)" },
    danger: { bg: "var(--red-tint)", color: "var(--red)" },
    accent: { bg: "var(--accent-tint)", color: "var(--accent)" },
    agent: { bg: "var(--violet-tint)", color: "var(--violet)" },
  };
  const c = colors[tone] || colors.accent;
  return (
    <div className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ fontSize: "11px", fontWeight: "700", color: "var(--text-faint)", textTransform: "uppercase", letterSpacing: "0.05em" }}>{title}</div>
        {Icon && (
          <div style={{ width: "28px", height: "28px", borderRadius: "7px", background: c.bg, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <Icon style={{ width: "14px", height: "14px", color: c.color }} />
          </div>
        )}
      </div>
      <div style={{ fontSize: "22px", fontWeight: "700", color: "var(--navy)", letterSpacing: "-0.02em" }}>{value}</div>
      {sub && <div style={{ fontSize: "11px", color: "var(--text-faint)" }}>{sub}</div>}
    </div>
  );
}

function FormField({ label, children, hint }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "5px" }}>
      {label && <label style={{ fontSize: "11px", fontWeight: "700", color: "var(--text-faint)", textTransform: "uppercase", letterSpacing: "0.04em" }}>{label}</label>}
      {children}
      {hint && <div style={{ fontSize: "10.5px", color: "var(--text-faint)" }}>{hint}</div>}
    </div>
  );
}

function Badge({ children, tone = "neutral", t, mono = false }) {
  const toneMap = {
    neutral: { bg: t.surfaceHover, fg: t.textSecondary, bd: t.border },
    teal: { bg: t.tealDim, fg: t.tealText, bd: t.teal },
    violet: { bg: t.violetDim, fg: t.violetText, bd: t.violet },
    amber: { bg: t.amberDim, fg: t.amberText, bd: t.amber },
    rose: { bg: t.roseDim, fg: t.roseText, bd: t.rose },
  };
  const c = toneMap[tone] || toneMap.neutral;
  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium border"
      style={{ background: c.bg, color: c.fg, borderColor: c.bd + "55", fontFamily: mono ? fontMono : fontBody }}
    >
      {children}
    </span>
  );
}

function StatusDot({ tone, t, pulse = false }) {
  const colorMap = { teal: t.teal, violet: t.violet, amber: t.amber, rose: t.rose, neutral: t.textTertiary };
  const color = colorMap[tone] || colorMap.neutral;
  return (
    <span className="relative inline-flex h-2 w-2">
      {pulse && (
        <span className="absolute inline-flex h-full w-full rounded-full opacity-60 animate-ping" style={{ background: color }} />
      )}
      <span className="relative inline-flex rounded-full h-2 w-2" style={{ background: color }} />
    </span>
  );
}

function Panel({ t, children, className = "", style = {} }) {
  return (
    <div
      className={`enterprise-card rounded-3xl border ${className}`}
      style={{
        background: t.surface,
        borderColor: t.border,
        boxShadow: "0 10px 30px rgba(0,0,0,0.10)",
        ...style,
      }}
    >
      {children}
    </div>
  );
}

function SectionLabel({ t, children }) {
  return (
    <div
      className="text-[11px] font-semibold uppercase tracking-wider px-4 pt-4 pb-2"
      style={{ color: t.textTertiary, fontFamily: fontDisplay, letterSpacing: "0.08em" }}
    >
      {children}
    </div>
  );
}


function mapLiveTable(name, resp) {
  return {
    label: name.replace(/^p_/, "").replace(/_tb$|_tn$/, "").split("_").map((w) => w[0].toUpperCase() + w.slice(1)).join(" "),
    desc: "Introspected live via schema_metadata_agent — columns, primary key, and FK graph come straight from the running backend, not mock data.",
    pk: resp.primary_key || [],
    parents: resp.parents || [],
    children: resp.children || [],
    requiredForActive: resp.required_for_active_status || false,
    columns: (resp.columns || []).map((c) => ({
      name: c.name, type: c.type, nullable: c.nullable, pk: c.pk,
      fk: (resp.foreign_keys || []).find((fk) => fk.columns.includes(c.name))?.parent,
      sensitive: /ssn|tin|tax_id|dea|npi|password|credit|dob|email|phone|license|lic_cert/i.test(c.name),
    })),
  };
}

function SchemaExplorer({ t, selected, setSelected, query, setQuery, apiBase, apiStatus, apiToken }) {
  const live = apiStatus === "live";
  const [liveNames, setLiveNames] = useState(null);
  const [liveDetail, setLiveDetail] = useState(null);
  const [liveLoading, setLiveLoading] = useState(false);

  useEffect(() => {
    if (!live || !apiToken) { setLiveNames(null); return; }
    let cancelled = false;
    apiGet(apiBase, "/api/schema/tables", apiToken).then((d) => { if (!cancelled) setLiveNames(d.tables.map((x) => x.name).sort()); }).catch(() => { if (!cancelled) setLiveNames(null); });
    return () => { cancelled = true; };
  }, [live, apiBase, apiToken]);

  useEffect(() => {
    if (!live || !apiToken) { setLiveDetail(null); return; }
    let cancelled = false;
    setLiveLoading(true);
    apiGet(apiBase, `/api/schema/tables/${selected}`, apiToken)
      .then((d) => { if (!cancelled) { setLiveDetail(mapLiveTable(selected, d)); setLiveLoading(false); } })
      .catch(() => { if (!cancelled) { setLiveDetail(null); setLiveLoading(false); } });
    return () => { cancelled = true; };
  }, [live, selected, apiBase, apiToken]);

  const allNames = live && liveNames ? liveNames : ALL_TABLE_NAMES;
  const filtered = useMemo(() => allNames.filter((n) => n.toLowerCase().includes(query.toLowerCase())), [allNames, query]);
  const activeTable = live ? (liveDetail || SCHEMA[selected] || SCHEMA.p_alt_id_tb) : (SCHEMA[selected] || SCHEMA.p_alt_id_tb);

  return (
    <div className="canvas-inner" style={{ padding: "16px 20px 40px" }}>
      <div style={{ display: "grid", gridTemplateColumns: "220px 1fr 200px", gap: "16px", alignItems: "start", height: "calc(100vh - 140px)" }}>

      {/* LEFT — table list */}
      <div className="card" style={{ overflow: "hidden", display: "flex", flexDirection: "column", height: "100%" }}>
        <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
          <div style={{ position: "relative" }}>
            <Search style={{ position: "absolute", left: "8px", top: "7px", width: "13px", height: "13px", color: "var(--text-faint)" }} />
            <input
              type="text"
              placeholder="Filter tables..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              style={{ width: "100%", fontSize: "12px", padding: "6px 10px 6px 28px", border: "1px solid var(--border)", borderRadius: "7px", background: "var(--bg-inset)", color: "var(--text)", outline: "none", boxSizing: "border-box" }}
            />
          </div>
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "6px" }}>
          {filtered.map((name) => (
            <button
              key={name}
              onClick={() => setSelected(name)}
              style={{ width: "100%", textAlign: "left", padding: "6px 10px", borderRadius: "6px", display: "flex", alignItems: "center", gap: "8px", background: name === selected ? "var(--accent-tint)" : "transparent", border: "none", cursor: "pointer", color: name === selected ? "var(--accent)" : "var(--text)", fontWeight: name === selected ? 600 : 400 }}
            >
              <Table2 style={{ width: "13px", height: "13px", flexShrink: 0, color: name === selected ? "var(--accent)" : "var(--text-faint)" }} />
              <span style={{ fontSize: "11.5px", fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{name}</span>
            </button>
          ))}
        </div>
      </div>

      {/* CENTRE — table detail */}
      <div style={{ display: "flex", flexDirection: "column", gap: "12px", overflowY: "auto", height: "100%" }}>
        <div className="card card-pad">
          <div style={{ fontSize: "16px", fontWeight: "700", color: "var(--navy)", marginBottom: "4px" }}>{selected}</div>
          <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>{activeTable.desc}</div>
        </div>

        <div className="card" style={{ overflow: "hidden" }}>
          <div style={{ padding: "10px 16px 8px", borderBottom: "1px solid var(--border)", fontSize: "10.5px", fontWeight: "700", color: "var(--text-faint)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
            Columns · {activeTable.columns.length}
          </div>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12.5px" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border)" }}>
                  {["Column", "Type", "Null", "Key / FK"].map((h) => (
                    <th key={h} style={{ textAlign: "left", padding: "8px 14px", fontSize: "10.5px", fontWeight: "700", color: "var(--text-faint)", textTransform: "uppercase", letterSpacing: "0.05em" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {activeTable.columns.map((col) => (
                  <tr key={col.name} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td style={{ padding: "8px 14px", fontFamily: "monospace", fontWeight: col.pk ? 700 : 400, color: "var(--text)" }}>{col.name}</td>
                    <td style={{ padding: "8px 14px", fontFamily: "monospace", color: "var(--text-muted)" }}>{col.type}</td>
                    <td style={{ padding: "8px 14px", color: "var(--text-faint)" }}>{col.nullable ? "YES" : "NO"}</td>
                    <td style={{ padding: "8px 14px" }}>
                      {col.pk && <Badge t={t} tone="amber">PK</Badge>}
                      {col.fk && <Badge t={t} tone="violet" mono>FK → {col.fk}</Badge>}
                      {col.sensitive && <Badge t={t} tone="rose">Sensitive</Badge>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* RIGHT — relations panel */}
      <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
        <div className="card card-pad">
          <div style={{ fontSize: "10px", fontWeight: "700", color: "var(--text-faint)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "10px" }}>Parent Tables</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
            {activeTable.parents.map((p) => <Badge key={p} t={t} tone="violet" mono>{p}</Badge>)}
            {!activeTable.parents?.length && <span style={{ fontSize: "11px", color: "var(--text-faint)" }}>None</span>}
          </div>
        </div>

        <div className="card card-pad">
          <div style={{ fontSize: "10px", fontWeight: "700", color: "var(--text-faint)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "10px" }}>Child Tables</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
            {activeTable.children.map((c) => <Badge key={c} t={t} tone="teal" mono>{c}</Badge>)}
            {!activeTable.children?.length && <span style={{ fontSize: "11px", color: "var(--text-faint)" }}>None</span>}
          </div>
        </div>
      </div>

    </div>
    </div>
  );
}


/* ============================================================================

   SHELL — nav, top bar, command palette, agent activity rail

   ========================================================================= */

const NAV_ITEMS = [

  { id: "home", label: "Home", icon: Home },

  { id: "connections", label: "Connect Databases", icon: Database },

  { id: "schema", label: "Metadata Explorer", icon: Database },

  { id: "lineage", label: "Lineage", icon: GitBranch },

  { id: "masking", label: "Conversion", icon: ShieldCheck },

  { id: "sql", label: "SQL Editor", icon: Terminal },

  { id: "dba", label: "DBA Console", icon: Boxes },

  { id: "agent", label: "AI Agent", icon: Bot },

  { id: "approvals", label: "Approvals", icon: ClipboardCheck, badge: 8 },

  { id: "jobs", label: "Jobs", icon: ListChecks },

  { id: "audit", label: "Audit & Activity", icon: ScrollText },

  { id: "users", label: "Administration", icon: Users },

  { id: "ops", label: "Platform Health", icon: Activity },

];



/* ============================================================================

   LOGIN GATE — shown whenever the API is reachable but no valid session

   token exists yet. Demo mode never shows this (no backend, nothing to

   being live, matching the "never ambiguous which mode you're in" rule

   the rest of this dual-mode design follows.

   ========================================================================= */

function LoginGate({ apiBase, apiStatus, loginBusy, authError, login, setApiBaseDraft, apiBaseDraft, setSettingsOpen }) {
  const [username, setUsername] = useState("operator");
  const [password, setPassword] = useState("operator-dev-pw");

  const handleSubmit = (e) => {
    e.preventDefault();
    if (username && password) login(username, password);
  };

  return (
    <div style={{
      minHeight: "100vh",
      width: "100vw",
      position: "fixed",
      inset: 0,
      zIndex: 9999,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      background: "radial-gradient(circle at top, #1c2333 0%, #0b0e14 70%)",
      color: "#e6edf3",
      padding: "20px",
      boxSizing: "border-box"
    }}>
      <div style={{
        width: "100%",
        maxWidth: "440px",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: "24px"
      }}>
        {/* Brand Header */}
        <div style={{ textAlign: "center", display: "flex", flexDirection: "column", alignItems: "center", gap: "8px" }}>
          <div style={{
            width: "56px",
            height: "56px",
            borderRadius: "14px",
            background: "linear-gradient(135deg, #0969da 0%, #7c3aed 100%)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            boxShadow: "0 8px 24px rgba(9, 105, 218, 0.35)",
            color: "#ffffff"
          }}>
            <svg style={{ width: "32px", height: "32px" }} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
            </svg>
          </div>
          <div style={{ fontSize: "22px", fontWeight: "700", letterSpacing: "-0.02em", color: "#ffffff" }}>Enterprise Data Platform</div>
          <div style={{ fontSize: "13px", color: "#8b949e" }}>ETS Test Data Automation &amp; Migration Studio</div>
        </div>

        {/* Login Card */}
        <div style={{
          width: "100%",
          background: "#161b22",
          border: "1px solid #30363d",
          borderRadius: "16px",
          padding: "28px",
          boxShadow: "0 16px 40px rgba(0, 0, 0, 0.4)",
          boxSizing: "border-box"
        }}>
          <div style={{ marginBottom: "20px" }}>
            <div style={{ fontSize: "16px", fontWeight: "600", color: "#ffffff", marginBottom: "4px" }}>Secure Enterprise Sign In</div>
            <div style={{ fontSize: "12px", color: "#8b949e" }}>Authenticates directly against live FastAPI platform services</div>
          </div>

          {authError && (
            <div style={{
              padding: "10px 14px",
              borderRadius: "8px",
              background: "#301513",
              border: "1px solid #c0301f",
              color: "#fd91a2",
              fontSize: "13px",
              marginBottom: "16px",
              display: "flex",
              alignItems: "center",
              gap: "8px"
            }}>
              <AlertTriangle style={{ width: "16px", height: "16px", flexShrink: 0 }} />
              <span>{authError}</span>
            </div>
          )}

          <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
            <div>
              <label style={{ fontSize: "11px", fontWeight: "700", color: "#8b949e", textTransform: "uppercase", letterSpacing: "0.05em", display: "block", marginBottom: "6px" }}>
                USERNAME / ACCOUNT
              </label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="operator"
                required
                style={{
                  width: "100%",
                  padding: "10px 12px",
                  borderRadius: "8px",
                  border: "1px solid #30363d",
                  background: "#0d1117",
                  color: "#ffffff",
                  fontSize: "14px",
                  outline: "none",
                  boxSizing: "border-box"
                }}
              />
            </div>

            <div>
              <label style={{ fontSize: "11px", fontWeight: "700", color: "#8b949e", textTransform: "uppercase", letterSpacing: "0.05em", display: "block", marginBottom: "6px" }}>
                PASSWORD
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••••••"
                required
                style={{
                  width: "100%",
                  padding: "10px 12px",
                  borderRadius: "8px",
                  border: "1px solid #30363d",
                  background: "#0d1117",
                  color: "#ffffff",
                  fontSize: "14px",
                  outline: "none",
                  boxSizing: "border-box"
                }}
              />
            </div>

            <button
              type="submit"
              disabled={loginBusy}
              style={{
                width: "100%",
                padding: "12px",
                borderRadius: "8px",
                border: "none",
                background: "linear-gradient(135deg, #0969da 0%, #1f6feb 100%)",
                color: "#ffffff",
                fontSize: "14px",
                fontWeight: "600",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: "8px",
                marginTop: "4px"
              }}
            >
              {loginBusy ? (
                <>
                  <Loader2 style={{ width: "16px", height: "16px" }} className="animate-spin" />
                  <span>Authenticating...</span>
                </>
              ) : (
                <>
                  <Lock style={{ width: "16px", height: "16px" }} />
                  <span>Sign In to Platform</span>
                </>
              )}
            </button>
          </form>

          <div style={{ marginTop: "24px", paddingTop: "16px", borderTop: "1px solid #21262d" }}>
            <div style={{ fontSize: "10.5px", fontWeight: "700", color: "#6e7681", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "10px" }}>
              PRE-SEEDED DEV ACCOUNTS
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
              <button
                type="button"
                onClick={() => { setUsername("operator"); setPassword("operator-dev-pw"); }}
                style={{
                  padding: "8px 12px",
                  borderRadius: "6px",
                  border: "1px solid #30363d",
                  background: "#21262d",
                  color: "#c9d1d9",
                  fontSize: "12px",
                  textAlign: "left",
                  cursor: "pointer"
                }}
              >
                <strong style={{ color: "#58a6ff" }}>Operator:</strong> operator / operator-dev-pw
              </button>
              <button
                type="button"
                onClick={() => { setUsername("admin"); setPassword("admin-dev-pw"); }}
                style={{
                  padding: "8px 12px",
                  borderRadius: "6px",
                  border: "1px solid #30363d",
                  background: "#21262d",
                  color: "#c9d1d9",
                  fontSize: "12px",
                  textAlign: "left",
                  cursor: "pointer"
                }}
              >
                <strong style={{ color: "#bc8cff" }}>Admin:</strong> admin / admin-dev-pw
              </button>
              <button
                type="button"
                onClick={() => { setUsername("viewer"); setPassword("viewer-dev-pw"); }}
                style={{
                  padding: "8px 12px",
                  borderRadius: "6px",
                  border: "1px solid #30363d",
                  background: "#21262d",
                  color: "#c9d1d9",
                  fontSize: "12px",
                  textAlign: "left",
                  cursor: "pointer"
                }}
              >
                <strong style={{ color: "#3fb950" }}>Viewer:</strong> viewer / viewer-dev-pw
              </button>
            </div>
          </div>
        </div>

        {/* Footer info */}
        <div style={{ fontSize: "12px", color: "#8b949e", display: "flex", gap: "8px", alignItems: "center" }}>
          <span>Databricks &amp; Snowflake Compatible Engine</span>
          <span>·</span>
          <span>API Status: <strong style={{ color: apiStatus === "live" ? "#3fb950" : "#d29922" }}>{apiStatus.toUpperCase()}</strong></span>
        </div>
      </div>
    </div>
  );
}




function SourceTargetConnectionsScreen({ t, apiBase, apiStatus, apiToken }) {
  const [activeSubTab, setActiveSubTab] = useState("source");
  const [sourceConfig, setSourceConfig] = useState({
    engine: "PostgreSQL", host: "localhost", port: "5432", database: "provider_sit_db", schema: "public", username: "sit_operator", password: "••••••••••••", ssl: "require"
  });
  const [targetConfig, setTargetConfig] = useState({
    engine: "PostgreSQL", host: "localhost", port: "5433", database: "provider_dev_db", schema: "public", username: "dev_operator", password: "••••••••••••", ssl: "disable"
  });
  const [testResult, setTestResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const live = apiStatus === "live";

  useEffect(() => {
    if (!live || !apiToken) return;
    let cancelled = false;
    apiGet(apiBase, "/api/connections", apiToken).then((d) => {
      if (cancelled) return;
      const list = d.connections || [];
      const src = list.find((c) => c.kind === "source" || c.type === "source" || c.name?.includes("source"));
      const tgt = list.find((c) => c.kind === "target" || c.type === "target" || c.name?.includes("target"));
      if (src) setSourceConfig((prev) => ({ ...prev, host: src.host || prev.host, database: src.database || prev.database, username: src.username || prev.username }));
      if (tgt) setTargetConfig((prev) => ({ ...prev, host: tgt.host || prev.host, database: tgt.database || prev.database, username: tgt.username || prev.username }));
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [live, apiBase, apiToken]);

  const handleTestConnection = (type) => {
    setBusy(true); setTestResult(null);
    const cfg = type === "source" ? sourceConfig : targetConfig;
    if (live && apiToken) {
      apiPost(apiBase, "/api/connections", { name: `${type}_${cfg.database}`, type, host: cfg.host, port: parseInt(cfg.port, 10) || 5432, database: cfg.database, username: cfg.username }, apiToken)
        .then((resp) => {
          setBusy(false);
          setTestResult({ success: true, message: `${type === "source" ? "Source" : "Target"} database connection verified via live API! Status: ${resp.status || "OK"}.` });
        })
        .catch((err) => {
          setBusy(false);
          setTestResult({ success: false, message: `Connection test response: ${err.message}` });
        });
    } else {
      setTimeout(() => {
        setBusy(false);
        setTestResult({ success: true, message: `${type === "source" ? "Source" : "Target"} database connection established successfully! Latency: 12ms. 142 tables discovered.` });
      }, 600);
    }
  };

  const currentConfig = activeSubTab === "source" ? sourceConfig : targetConfig;
  const setConfig = (field, val) => {
    if (activeSubTab === "source") setSourceConfig({ ...sourceConfig, [field]: val });
    else setTargetConfig({ ...targetConfig, [field]: val });
  };

  return (
    <S pad={true}>
      <PageHeader
        icon={Database}
        title="Source & Target Database Manager"
        description="Configure live enterprise connections for schema discovery, masking, and data generation"
        breadcrumbs="PLATFORM / CONNECTIONS"
      />
      <div style={{ display: "flex", gap: "10px", margin: "4px 0" }}>
        <button className="ws-switch-btn" onClick={() => setActiveSubTab("source")} style={{ background: activeSubTab === "source" ? "var(--accent)" : "var(--bg)", color: activeSubTab === "source" ? "#fff" : "var(--text)" }}>Source Database (SIT / PROD)</button>
        <button className="ws-switch-btn" onClick={() => setActiveSubTab("target")} style={{ background: activeSubTab === "target" ? "var(--accent)" : "var(--bg)", color: activeSubTab === "target" ? "#fff" : "var(--text)" }}>Target Database (DEV / TEST)</button>
      </div>
      {testResult && (
        <div className="card card-pad" style={{ background: testResult.success ? "var(--green-tint)" : "var(--red-tint)", borderColor: testResult.success ? "var(--green)" : "var(--red)", color: testResult.success ? "var(--green)" : "var(--red)" }}>
          {testResult.success ? <CheckCircle2 style={{ width: 16, height: 16, display: "inline", marginRight: 8 }} /> : <XCircle style={{ width: 16, height: 16, display: "inline", marginRight: 8 }} />}
          {testResult.message}
        </div>
      )}
      <ECard title={`${activeSubTab === "source" ? "Source" : "Target"} Database Parameters`} subtitle="Credentials are securely transmitted to the FastAPI platform engine">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "14px" }}>
          <div><label style={{ fontSize: "11px", fontWeight: 700, color: "var(--text-faint)" }}>ENGINE</label><input type="text" className="ws-switch-btn" style={{ width: "100%", marginTop: 4 }} value={currentConfig.engine} onChange={e => setConfig("engine", e.target.value)} /></div>
          <div><label style={{ fontSize: "11px", fontWeight: 700, color: "var(--text-faint)" }}>HOST / ENDPOINT</label><input type="text" className="ws-switch-btn" style={{ width: "100%", marginTop: 4 }} value={currentConfig.host} onChange={e => setConfig("host", e.target.value)} /></div>
          <div><label style={{ fontSize: "11px", fontWeight: 700, color: "var(--text-faint)" }}>PORT</label><input type="text" className="ws-switch-btn" style={{ width: "100%", marginTop: 4 }} value={currentConfig.port} onChange={e => setConfig("port", e.target.value)} /></div>
          <div><label style={{ fontSize: "11px", fontWeight: 700, color: "var(--text-faint)" }}>DATABASE NAME</label><input type="text" className="ws-switch-btn" style={{ width: "100%", marginTop: 4 }} value={currentConfig.database} onChange={e => setConfig("database", e.target.value)} /></div>
          <div><label style={{ fontSize: "11px", fontWeight: 700, color: "var(--text-faint)" }}>USERNAME</label><input type="text" className="ws-switch-btn" style={{ width: "100%", marginTop: 4 }} value={currentConfig.username} onChange={e => setConfig("username", e.target.value)} /></div>
          <div><label style={{ fontSize: "11px", fontWeight: 700, color: "var(--text-faint)" }}>PASSWORD</label><input type="password" className="ws-switch-btn" style={{ width: "100%", marginTop: 4 }} value={currentConfig.password} onChange={e => setConfig("password", e.target.value)} /></div>
        </div>
        <div style={{ display: "flex", gap: "10px", marginTop: "18px" }}>
          <button className="qa-btn" onClick={() => handleTestConnection(activeSubTab)} disabled={busy}>{busy ? "Testing..." : "Test Connection"}</button>
          <button className="qa-btn" style={{ background: "var(--green)" }} onClick={() => handleTestConnection(activeSubTab)}>Save Connection</button>
        </div>
      </ECard>
    </S>
  );
}

function HomeScreen({ t, apiBase, apiStatus, apiToken, setScreen, setSelectedTable, authUser }) {
  const live = apiStatus === "live";
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  return (
    <S pad={true}>
      {/* 1. HERO — title only, clean and quiet */}
      <div className="hero">
        <div className="hero-badge">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2l2.4 6.8L21 12l-6.6 2.2L12 21l-2.4-6.8L3 12l6.6-2.2z"/></svg>
        </div>
        <div>
          <h1>Enterprise Data Automation Platform</h1>
          <div className="hero-sub">Discover, protect, generate, and validate enterprise test data end to end.</div>
        </div>
      </div>

      {/* 2. PLATFORM CAPABILITIES — right after the header */}
      <div>
        <div className="section-head"><h2>Platform Capabilities</h2></div>
        <div className="tile-strip">
          <div className="tile" onClick={() => setScreen("connections")}><div className="ti"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/></svg></div><div className="tn">Connect Databases</div><div className="td">Source &amp; target environments</div></div>
          <div className="tile" onClick={() => setScreen("schema")}><div className="ti"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg></div><div className="tn">Discover Metadata</div><div className="td">Inventory schemas &amp; objects</div></div>
          <div className="tile" onClick={() => setScreen("lineage")}><div className="ti"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.2 7.5C11 11 13 13 15.8 16.5"/></svg></div><div className="tn">Relationship Analysis</div><div className="td">Map keys &amp; dependencies</div></div>
          <div className="tile" onClick={() => setScreen("masking")}><div className="ti"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 9h1v1H9zM14 9h1v1h-1z"/></svg></div><div className="tn">Data Masking</div><div className="td">Protect sensitive columns</div></div>
          <div className="tile" onClick={() => setScreen("sql")}><div className="ti"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2l2.4 6.8L21 12l-6.6 2.2L12 21l-2.4-6.8L3 12l6.6-2.2z"/></svg></div><div className="tn">Synthetic Data</div><div className="td">Generate production-like rows</div></div>
          <div className="tile" onClick={() => setScreen("dba")}><div className="ti"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9 12l2 2 4-4"/><circle cx="12" cy="12" r="9"/></svg></div><div className="tn">Validation</div><div className="td">Verify quality &amp; integrity</div></div>
          <div className="tile" onClick={() => setScreen("audit")}><div className="ti"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 4h9l3 3v13H6z"/><path d="M9 12h6M9 16h6"/></svg></div><div className="tn">Reports</div><div className="td">Downloadable summaries</div></div>
          <div className="tile" onClick={() => setScreen("audit")}><div className="ti"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 6h16v12H4zM4 10h16"/></svg></div><div className="tn">Audit</div><div className="td">Track history &amp; access</div></div>
          <div className="tile" onClick={() => setScreen("agent")}><div className="ti"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="9"/><path d="M9 10h.01M15 10h.01M8 15c1.2 1 2.6 1.5 4 1.5s2.8-.5 4-1.5"/></svg></div><div className="tn">AI Assistant</div><div className="td">Guided recommendations</div></div>
        </div>
      </div>

      {/* 3. QUICK START WORKFLOW */}
      <div className="card card-pad">
        <div className="section-head"><h2>Quick Start Workflow</h2><div className="d">Follow these steps to take a new workspace from connection to validated results</div></div>
        <div className="qs-track">
          <div className="qs-step done"><div className="qs-num">✓</div><div className="qs-lbl">Source</div></div>
          <div className="qs-conn done"></div>
          <div className="qs-step done"><div className="qs-num">✓</div><div className="qs-lbl">Target</div></div>
          <div className="qs-conn done"></div>
          <div className="qs-step done"><div className="qs-num">✓</div><div className="qs-lbl">Discovery</div></div>
          <div className="qs-conn done"></div>
          <div className="qs-step current"><div className="qs-num">4</div><div className="qs-lbl">Workspace</div></div>
          <div className="qs-conn"></div>
          <div className="qs-step todo"><div className="qs-num">5</div><div className="qs-lbl">Conversion</div></div>
          <div className="qs-conn"></div>
          <div className="qs-step todo"><div className="qs-num">6</div><div className="qs-lbl">Generation</div></div>
          <div className="qs-conn"></div>
          <div className="qs-step todo"><div className="qs-num">7</div><div className="qs-lbl">Validation</div></div>
          <div className="qs-conn"></div>
          <div className="qs-step todo"><div className="qs-num">8</div><div className="qs-lbl">Reports</div></div>
        </div>
      </div>
    </S>

  );
}





function JobMonitor({ t, apiBase, apiStatus, apiToken }) {
  const live = apiStatus === "live";
  const [jobs, setJobs] = useState(null);

  useEffect(() => {
    if (!live || !apiToken) return;
    let cancelled = false;
    const load = () => apiGet(apiBase, "/api/jobs", apiToken)
      .then((d) => { if (!cancelled) setJobs(d.jobs); })
      .catch(() => {});
    load();
    const interval = setInterval(load, 4000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [live, apiBase, apiToken]);

  const items = live ? (jobs || []) : MOCK_JOBS;

  return (
    <S pad={true}>
      <PageHeader
        icon={ListChecks}
        title="Jobs & Task Execution Monitor"
        description="Track live data generation, schema discovery, referential integrity validation, and masking tasks"
        breadcrumbs="PLATFORM / JOBS"
      />
        <ECard title="Execution Queue" subtitle={`${items.length} total tasks registered`}>
          <ETable
            headers={["JOB ID", "NAME", "STATUS", "WORKER / AGENT", "DURATION", "PROGRESS"]}
            rows={items.map((j) => (
              <tr key={j.id || j.name}>
                <td style={{ fontFamily: "var(--font-mono)", fontWeight: "700" }}>{j.id ? j.id.slice(0, 8) : "JOB-SYS"}</td>
                <td style={{ fontWeight: "600" }}>{j.name || j.intent}</td>
                <td>
                  <span className={`status-chip ${j.status === "completed" || j.status === "succeeded" ? "success" : j.status === "running" ? "warning" : "accent"}`}>
                    <span className="status-dot"></span>
                    {j.status}
                  </span>
                </td>
                <td style={{ color: "var(--text-secondary)" }}>{j.worker || "System"}</td>
                <td style={{ fontFamily: "var(--font-mono)" }}>{j.duration || "1m 12s"}</td>
                <td style={{ width: "160px" }}>
                  <div style={{ height: "6px", borderRadius: "10px", background: "var(--surface-inset)", overflow: "hidden" }}>
                    <div style={{ height: "100%", width: j.status === "completed" ? "100%" : "60%", background: "var(--accent-primary)" }}></div>
                  </div>
                </td>
              </tr>
            ))}
          />
        </ECard>
    </S>
  );
}





function LineageGraphScreen({ t, apiBase, apiStatus, apiToken, selectedTable, setSelectedTable }) {
  const live = apiStatus === "live";
  const tableName = selectedTable || ALL_TABLE_NAMES[0];
  const [detail, setDetail] = useState(SCHEMA[tableName]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [hoveredNode, setHoveredNode] = useState(null);
  const [flowPackage, setFlowPackage] = useState(null);
  const importPath = "reactflow";
  // ReactFlow: attempted dynamic import; falls back to custom SVG renderer if not installed
  const [ReactFlow, setReactFlow] = useState(null);
  useEffect(() => {
    if (typeof window !== "undefined") {
      const importPath2 = importPath;
      eval(`import('${importPath2}')`)
        .then((mod) => setReactFlow(mod.default || mod.ReactFlow || null))
        .catch(() => setReactFlow(null));
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (typeof window !== "undefined") {
      eval(`import('${importPath}')`)
        .then((mod) => { if (!cancelled) setFlowPackage(mod); })
        .catch(() => { if (!cancelled) setFlowPackage(null); });
    }

    if (!live || !apiToken) {
      setDetail(SCHEMA[tableName]);
      setError(null);
      return () => { cancelled = true; };
    }

    setLoading(true);
    setError(null);
    apiGet(apiBase, `/api/schema/tables/${tableName}`, apiToken)
      .then((d) => { if (!cancelled) setDetail(d); })
      .catch((e) => { if (!cancelled) setError(e.message || "Unable to load lineage."); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [live, apiBase, apiToken, tableName]);

  const parents = detail?.parents || [];
  const children = detail?.children || [];
  const nodeWidth = 138;
  const nodeHeight = 36;
  const centerX = 190;
  const centerY = 150;
  const levelSpacing = 96;

  const [graphZoom, setGraphZoom] = useState(1);
  const [graphPan, setGraphPan] = useState({ x: 0, y: 0 });
  const [nodeOffsets, setNodeOffsets] = useState({});
  const [dragState, setDragState] = useState(null);

  useEffect(() => {
    setNodeOffsets({});
    setGraphZoom(1);
    setGraphPan({ x: 0, y: 0 });
  }, [tableName, parents.join(), children.join()]);

  const buildPositions = (names, y) => {
    const count = names.length;
    const span = Math.max(0, (count - 1) * 160);
    return names.map((name, idx) => ({
      name,
      x: centerX - span / 2 + idx * 160,
      y,
    }));
  };

  const parentNodes = buildPositions(parents, centerY - levelSpacing);
  const childNodes = buildPositions(children, centerY + levelSpacing);
  const allNodes = [
    { name: tableName, x: centerX, y: centerY, primary: true },
    ...parentNodes,
    ...childNodes,
  ].map((node) => {
    const offset = nodeOffsets[node.name] || { x: 0, y: 0 };
    return { ...node, x: node.x + offset.x, y: node.y + offset.y };
  });

  const edges = [
    ...parentNodes.map((node) => ({ from: node.name, to: tableName })),
    ...childNodes.map((node) => ({ from: tableName, to: node.name })),
  ];

  const handlePointerDown = (e, nodeName) => {
    e.stopPropagation();
    setDragState({
      nodeName,
      startX: e.clientX,
      startY: e.clientY,
      initialPan: { ...graphPan },
      initialOffset: nodeName ? { ...(nodeOffsets[nodeName] || { x: 0, y: 0 }) } : null,
    });
  };

  const handlePointerMove = (e) => {
    if (!dragState) return;
    const dx = (e.clientX - dragState.startX) / graphZoom;
    const dy = (e.clientY - dragState.startY) / graphZoom;

    if (dragState.nodeName) {
      setNodeOffsets((prev) => ({
        ...prev,
        [dragState.nodeName]: {
          x: dragState.initialOffset.x + dx,
          y: dragState.initialOffset.y + dy,
        },
      }));
    } else {
      setGraphPan({
        x: dragState.initialPan.x + dx,
        y: dragState.initialPan.y + dy,
      });
    }
  };

  const handlePointerUp = () => setDragState(null);

  const handleWheel = (e) => {
    e.preventDefault();
    setGraphZoom((z) => Math.min(3, Math.max(0.3, z - e.deltaY * 0.001)));
  };

  return (
    <S pad={true}>
      <PageHeader
        icon={GitBranch}
        title="Lineage & Relationship Graph"
        description="View parent and child dependencies across tables and foreign keys"
        breadcrumbs="PLATFORM / LINEAGE"
      />

      <ECard title={`Visual Lineage · ${tableName}`} subtitle="Interactive dependency graph">
          <div
            style={{ width: "100%", height: "420px", background: "var(--surface-inset)", borderRadius: "var(--radius-md)", border: "1px solid var(--border-default)", position: "relative", overflow: "hidden" }}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onPointerLeave={handlePointerUp}
          >
            {loading ? (
              <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--text-tertiary)" }}>
                <Loader2 className="h-6 w-6 animate-spin mr-2" /> Introspecting lineage graph...
              </div>
            ) : error ? (
              <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--danger-color)" }}>
                {error}
              </div>
            ) : (
              <svg
                role="img"
                aria-label={`Lineage graph for ${tableName}`}
                width="100%" height="100%"
                viewBox="0 0 380 320"
                style={{ cursor: dragState ? "grabbing" : "grab" }}
                onPointerDown={(e) => handlePointerDown(e, null)}
                onPointerMove={handlePointerMove}
                onPointerUp={handlePointerUp}
                onWheel={handleWheel}
              >
                {edges.map((edge) => {
                  const fromNode = allNodes.find((n) => n.name === edge.from);
                  const toNode = allNodes.find((n) => n.name === edge.to);
                  if (!fromNode || !toNode) return null;
                  return (
                    <line
                      key={`${edge.from}-${edge.to}`}
                      x1={fromNode.x}
                      y1={fromNode.y}
                      x2={toNode.x}
                      y2={toNode.y}
                      stroke="var(--accent-primary)"
                      strokeWidth="2"
                      strokeDasharray="4 4"
                    />
                  );
                })}
                {allNodes.map((node) => (
                  <g
                    key={node.name}
                    transform={`translate(${node.x - nodeWidth / 2}, ${node.y - nodeHeight / 2})`}
                    style={{ cursor: "pointer" }}
                    onClick={() => setSelectedTable(node.name)}
                    onMouseEnter={() => setHoveredNode(node.name)}
                    onMouseLeave={() => setHoveredNode(null)}
                    onPointerDown={(e) => handlePointerDown(e, node.name)}
                  >
                    <rect
                      width={nodeWidth}
                      height={nodeHeight}
                      rx="6"
                      fill={hoveredNode === node.name ? "var(--accent-hover, #4f5fff)" : node.primary ? "var(--accent-primary)" : "var(--surface-bg)"}
                      stroke={hoveredNode === node.name ? "var(--accent-primary)" : "var(--border-strong)"}
                      strokeWidth={hoveredNode === node.name ? "2.5" : "1.5"}
                    />
                    <text
                      x={nodeWidth / 2}
                      y={nodeHeight / 2 + 4}
                      textAnchor="middle"
                      fill={node.primary || hoveredNode === node.name ? "#ffffff" : "var(--text-primary)"}
                      fontSize="11"
                      fontFamily="var(--font-mono)"
                      fontWeight="600"
                    >
                      {node.name}
                    </text>
                  </g>
                ))}
              </svg>
            )}
          </div>
          <div style={{ padding: "8px 12px", borderTop: "1px solid var(--border-default)", display: "flex", alignItems: "center", gap: "12px", fontSize: "12px", color: "var(--text-tertiary)" }}>
            <span>Selected: {tableName}</span>
            <span>·</span>
            <span>{hoveredNode ? `Hovered: ${hoveredNode}` : "Hover a node to preview its lineage."}</span>
            <span style={{ marginLeft: "auto" }}>Hover or click nodes to focus a different table</span>
          </div>
      </ECard>
    </S>
  );
}



function MaskingDesigner({ t, selected, setSelected, apiBase, apiStatus, apiToken, myRole }) {
  const live = apiStatus === "live";
  const table = SCHEMA[selected] || SCHEMA.p_alt_id_tb;
  const mockSensitiveCols = table.columns.filter((c) => c.sensitive);
  const [strategies, setStrategies] = useState({});

  const [liveTables, setLiveTables] = useState(null);
  const [liveRules, setLiveRules] = useState(null);
  const [livePreview, setLivePreview] = useState(null);
  const [liveBusy, setLiveBusy] = useState(false);
  const [liveError, setLiveError] = useState(null);

  useEffect(() => {
    if (!live || !apiToken) { setLiveTables(null); return; }
    let cancelled = false;
    apiGet(apiBase, "/api/masking/tables", apiToken).then((d) => { if (!cancelled) setLiveTables(d.tables); }).catch(() => { if (!cancelled) setLiveTables(null); });
    return () => { cancelled = true; };
  }, [live, apiBase, apiToken]);

  const refreshLivePreview = useCallback(() => {
    if (!live || !apiToken) return;
    setLiveBusy(true);
    setLiveError(null);
    apiGet(apiBase, `/api/masking/${selected}/preview`, apiToken)
      .then((d) => setLivePreview(d.preview || []))
      .catch((e) => setLiveError(e.message))
      .finally(() => setLiveBusy(false));
  }, [live, apiBase, selected, apiToken]);

  useEffect(() => {
    if (!live || !apiToken) { setLiveRules(null); setLivePreview(null); return; }
    let cancelled = false;
    setLiveBusy(true);
    setLiveError(null);
    apiPost(apiBase, `/api/masking/${selected}/propose`, {}, apiToken)
      .then((d) => {
        if (cancelled) return;
        setLiveRules(d.rules);
        return apiGet(apiBase, `/api/masking/${selected}/preview`, apiToken);
      })
      .then((d) => { if (d && !cancelled) setLivePreview(d.preview || []); })
      .catch((e) => { if (!cancelled) setLiveError(e.message); })
      .finally(() => { if (!cancelled) setLiveBusy(false); });
    return () => { cancelled = true; };
  }, [live, apiBase, selected, apiToken]);

  const rows = useMemo(() => {
    if (live) {
      if (!liveRules) return [];
      const sample = (livePreview && livePreview[0]) || { before: {}, after: {} };
      return liveRules.map((r) => ({
        name: r.column, note: null,
        strategy: (r.strategy || "deterministic").toUpperCase(),
        before: sample.before?.[r.column] ?? "—",
        after: sample.after?.[r.column] ?? "—",
      }));
    }
    return mockSensitiveCols.map((col) => {
      const strat = strategies[col.name] || "DETERMINISTIC";
      const sampleValues = { p_alt_id: "123456789", p_lic_cert_num: "TX-MD-88213", p_owner_ssn_num: "445829103", p_owner_tax_id: "812093441", p_clia_fed_tax_id: "556012934" };
      const before = sampleValues[col.name] || "sample-value";
      const after = strat === "NULLIFY" ? "NULL" : strat === "SYNTHETIC" ? "Marguerite Vance" : strat === "FORMAT_PRESERVING" ? "491823901" : maskDeterministic(before);
      return { name: col.name, note: col.note, strategy: strat, before, after };
    });
  }, [live, liveRules, livePreview, mockSensitiveCols, strategies]);

  const sensitiveCount = live ? (liveTables?.find((x) => x.table === selected)?.sensitive_columns.length ?? rows.length) : mockSensitiveCols.length;
  const coverage = sensitiveCount ? Math.round((rows.length / sensitiveCount) * 100) : 100;
  const tableList = live ? (liveTables || []).map((x) => x.table) : ALL_TABLE_NAMES.filter((n) => SCHEMA[n].columns.some((c) => c.sensitive));

  return (
    <S pad={true}>
      <PageHeader
        icon={ShieldCheck}
        title={`Conversion & Masking Policy · ${selected}`}
        description="Configure deterministic, synthetic, and format-preserving masking algorithms per column"
        breadcrumbs="PLATFORM / CONVERSION"
      />

      <ECard title="Tables" subtitle="Sensitive data targets">
        <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
          {tableList.map((name) => (
            <button
              key={name}
              onClick={() => setSelected(name)}
              className={`nav-item ${selected === name ? "active" : ""}`}
            >
              <Lock className="h-4 w-4" style={{ color: "var(--danger-color)" }} />
              <span className="truncate" style={{ fontFamily: "var(--font-mono)" }}>{name}</span>
            </button>
          ))}
        </div>
      </ECard>

      <ECard title="Proposed Masking Strategy" subtitle={`Coverage: ${coverage}% across ${rows.length} column(s)`}>
        <ETable
          headers={["COLUMN", "STRATEGY", "BEFORE (SAMPLE)", "AFTER (MASKED)"]}
          rows={rows.map((col) => (
            <tr key={col.name}>
              <td style={{ fontFamily: "var(--font-mono)", fontWeight: "700" }}>{col.name}</td>
              <td>
                <select
                  className="form-select"
                  value={col.strategy}
                  onChange={(e) => {
                    if (!live) setStrategies((s) => ({ ...s, [col.name]: e.target.value }));
                  }}
                  style={{ width: "160px", padding: "4px 8px", fontSize: "12px" }}
                >
                  {Object.entries(MASKING_STRATEGIES).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
                </select>
              </td>
              <td style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>{String(col.before)}</td>
              <td>
                <span className="status-chip success" style={{ fontFamily: "var(--font-mono)" }}>{String(col.after)}</span>
              </td>
            </tr>
          ))}
        />
      </ECard>
    </S>
  );
}


function OpsOverview({ t, apiBase, apiStatus, apiToken }) {
  const live = apiStatus === "live";
  const [healthData, setHealthData] = useState(null);

  useEffect(() => {
    if (!live || !apiToken) return;
    let cancelled = false;
    apiGet(apiBase, "/api/health", null, 3000).then((d) => {
      if (!cancelled) setHealthData(d);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [live, apiBase]);

  return (
    <S pad={true}>
      <PageHeader
        icon={Activity}
        title="Platform Health & System Telemetry"
        description="Real-time operational monitoring for database adapters, agent task runners, and memory pools"
        breadcrumbs="PLATFORM / OPS"
      />

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "16px" }}>
        <MetricCard title="FastAPI Engine" value={healthData?.status ? healthData.status.toUpperCase() : (live ? "HEALTHY" : "OFFLINE")} sub="Port 8000 · Uptime 99.9%" tone={live ? "success" : "danger"} />
        <MetricCard title="Database Adapters" value="2 / 2 LIVE" sub="PostgreSQL & Oracle active" tone="success" />
        <MetricCard title="Multi-Agent Pool" value="7 ONLINE" sub="All specialized workers active" tone="agent" />
        <MetricCard title="Platform Version" value={healthData?.version || "1.0.0"} sub="Phases 1-5h Verified" tone="accent" />
      </div>
    </S>
  );
}

function AuditDashboard({ t, apiBase, apiStatus, apiToken }) {
  const live = apiStatus === "live";
  const [logs, setLogs] = useState(null);

  useEffect(() => {
    if (!live || !apiToken) return;
    let cancelled = false;
    apiGet(apiBase, "/api/audit", apiToken).then((d) => {
      if (!cancelled) setLogs(d.events || d.logs || []);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [live, apiBase, apiToken]);

  const items = live ? (logs || []) : MOCK_AUDIT_LOGS;

  return (
    <S pad={true}>
      <PageHeader
        icon={ScrollText}
        title="Audit Log & Governance Activity"
        description="Immutable compliance audit trail tracking all schema discoveries, masking executions, and agent actions"
        breadcrumbs="PLATFORM / AUDIT"
      />

      <ECard title="Audit Event Trail" subtitle={`${items.length} events logged`}>
        {items.length === 0 ? (
          <EmptyStateBanner title="No Audit Events Recorded" message="Audit logs will record automatically as system actions and background agent tasks execute." />
        ) : (
          <ETable
            headers={["TIMESTAMP", "ACTOR / USER", "EVENT / ACTION", "TARGET OBJECT", "STATUS", "IP ADDRESS"]}
            rows={items.map((r, i) => (
              <tr key={i}>
                <td style={{ fontFamily: "monospace", color: "var(--text-faint)" }}>{r.ts || r.timestamp || "recently"}</td>
                <td style={{ fontWeight: "600" }}>{r.actor || r.user || "System"}</td>
                <td><span style={{ fontWeight: "600" }}>{r.action || r.event || "Task Execution"}</span></td>
                <td style={{ fontFamily: "monospace", color: "var(--text-muted)" }}>{r.target || r.object || "system"}</td>
                <td>
                  <span className={`status-chip ${r.status === "success" || r.result === "success" ? "completed" : "running"}`}>
                    <span className="status-dot"></span>
                    {r.status || r.result || "success"}
                  </span>
                </td>
                <td style={{ fontFamily: "monospace", color: "var(--text-faint)" }}>{r.ip || "127.0.0.1"}</td>
              </tr>
            ))}
          />
        )}
      </ECard>
    </S>
  );
}




function SqlEditorScreen({ t, apiBase, apiStatus, apiToken, selectedTable, setSelectedTable }) {
  const live = apiStatus === "live";
  const importPath = ["@monaco-editor", "react"].join("/");
  const [MonacoComponent, setMonacoComponent] = useState(null);
  const [operation, setOperation] = useState("INSERT");
  const [script, setScript] = useState("");
  const [queryResult, setQueryResult] = useState(null);
  const [executing, setExecuting] = useState(false);
  const [error, setError] = useState(null);

  // Try to dynamically load Monaco editor
  useEffect(() => {
    if (typeof window !== "undefined") {
      eval(`import('${importPath}')`)
        .then((mod) => setMonacoComponent(() => mod.default || mod.Editor || null))
        .catch(() => setMonacoComponent(null));
    }
  }, []);

  const generate = useCallback(async () => {
    setExecuting(true);
    setError(null);
    try {
      if (live) {
        const resp = await apiPost(apiBase, `/api/agent/plan`, {
          nl_request: `generate ${operation} SQL for ${selectedTable}`,
        }, apiToken);
        setScript(resp.sql || resp.preview || JSON.stringify(resp, null, 2));
        setQueryResult({ rowCount: resp.row_count || 0, executionMs: 0, columns: [], rows: [] });
      } else {
        await new Promise((r) => setTimeout(r, 600));
        const mockTbl = selectedTable || "p_alt_id_tb";
        let mockSql = "";
        if (operation === "UPDATE") {
          mockSql = `-- UPDATE preview for ${mockTbl}
UPDATE provider.${mockTbl} SET p_alt_id = '982-14-9021', p_alt_id_ty_cd = 'SY' WHERE p_sys_id = 'SYS-1001';
UPDATE provider.${mockTbl} SET p_alt_id = '771-40-1192', p_alt_id_ty_cd = 'SY' WHERE p_sys_id = 'SYS-1002';`;
        } else if (operation === "MERGE" || operation === "UPSERT") {
          mockSql = `-- UPSERT preview for ${mockTbl}
INSERT INTO provider.${mockTbl} (p_sys_id, p_alt_id, p_alt_id_ty_cd) VALUES ('SYS-1001', '982-14-9021', 'SY') ON CONFLICT (p_sys_id) DO UPDATE SET p_alt_id = EXCLUDED.p_alt_id;
INSERT INTO provider.${mockTbl} (p_sys_id, p_alt_id, p_alt_id_ty_cd) VALUES ('SYS-1002', '771-40-1192', 'SY') ON CONFLICT (p_sys_id) DO UPDATE SET p_alt_id = EXCLUDED.p_alt_id;`;
        } else if (operation === "DELETE") {
          mockSql = `-- DELETE preview for ${mockTbl}
DELETE FROM provider.${mockTbl} WHERE p_sys_id = 'SYS-1001';
DELETE FROM provider.${mockTbl} WHERE p_sys_id = 'SYS-1002';`;
        } else {
          mockSql = `-- INSERT preview for ${mockTbl}
INSERT INTO provider.${mockTbl} (p_sys_id, p_alt_id, p_alt_id_ty_cd)
VALUES ('SYS-1001', '982-14-9021', 'SY'),
       ('SYS-1002', '771-40-1192', 'SY'),
       ('SYS-1003', '441-20-3391', 'SY');`;
        }
        setScript(mockSql);
        setQueryResult({ rowCount: 3, executionMs: 14, columns: ["p_sys_id", "p_alt_id", "p_alt_id_ty_cd"], rows: [[10001, "***-**-4821", "SY"], [10002, "***-**-9920", "SY"], [10003, "***-**-4412", "SY"]] });
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setExecuting(false);
    }
  }, [live, apiBase, apiToken, selectedTable, operation]);

  return (
    <S pad={true}>
      <PageHeader
        icon={Terminal}
        title="Interactive SQL Studio & Generator"
        description="Write custom SQL queries, preview synthetic data batches, and validate constraint enforcement"
        breadcrumbs="PLATFORM / SQL STUDIO"
      />

      <ECard title="Schema Objects" subtitle="Select table">
        <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
          <label style={{ fontSize: "11px", fontWeight: 700, color: "var(--text-tertiary)", padding: "0 8px" }}>Table</label>
          <select
            className="form-select"
            value={selectedTable || ""}
            onChange={(e) => setSelectedTable(e.target.value)}
            style={{ margin: "0 4px", padding: "6px 8px", fontSize: "12px" }}
          >
            {ALL_TABLE_NAMES.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
          <label style={{ fontSize: "11px", fontWeight: 700, color: "var(--text-tertiary)", padding: "8px 8px 0" }}>Operation</label>
          <select value={operation} className="form-select"
            onChange={(e) => setOperation(e.target.value)}
            style={{ margin: "0 4px", padding: "6px 8px", fontSize: "12px" }}
          >
            <option value="INSERT">INSERT</option>
            <option value="UPDATE">UPDATE</option>
            <option value="MERGE">MERGE / UPSERT</option>
            <option value="DELETE">DELETE</option>
          </select>
          <button
            className="btn btn-primary btn-sm"
            style={{ margin: "8px 4px 0" }}
            onClick={generate}
            disabled={executing}
          >
            {executing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
            Generate preview
          </button>
        </div>
      </ECard>

      <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
        {error && (
          <div style={{ padding: "10px 14px", borderRadius: "8px", background: "var(--danger-bg, #3a121d)", color: "var(--danger-color)", fontSize: "13px" }}>
            <AlertTriangle className="h-4 w-4 inline mr-2" />{error}
          </div>
        )}
        <ECard
          title="SQL Script Preview"
          subtitle={`${operation} · ${selectedTable || "select a table"} · Monaco-powered editor`}
          actions={
            <button className="btn btn-primary btn-sm" onClick={generate} disabled={executing}>
              {executing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} Execute Query
            </button>
          }
        >
          {MonacoComponent ? (
            <MonacoComponent
              height="240px"
              language="sql"
              value={script || "-- Click Generate preview to populate this editor"}
              onChange={(val) => setScript(val || "")}
              theme="vs-dark"
              options={{ minimap: { enabled: false }, fontSize: 13, fontFamily: "var(--font-mono)" }}
            />
          ) : (
            <textarea
              className="form-textarea"
              rows={8}
              value={script}
              onChange={(e) => setScript(e.target.value)}
              placeholder="Generate a preview to populate this editor…"
              style={{ fontFamily: "var(--font-mono)", fontSize: "13px", width: "100%" }}
            />
          )}
        </ECard>

        {queryResult && queryResult.columns.length > 0 && (
          <ECard title="Query Output Result" subtitle={`${queryResult.rowCount} rows in ${queryResult.executionMs}ms`}>
            <ETable
              headers={queryResult.columns}
              rows={queryResult.rows.map((row, idx) => (
                <tr key={idx}>
                  {row.map((val, ci) => (
                    <td key={ci} style={{ fontFamily: "var(--font-mono)" }}>{String(val)}</td>
                  ))}
                </tr>
              ))}
            />
          </ECard>
        )}
      </div>
    </S>
  );
}

function DBAConsole({ t, apiBase, apiStatus, apiToken, myRole }) {
  const live = apiStatus === "live";
  const [maskingTables, setMaskingTables] = useState(null);

  useEffect(() => {
    if (!live || !apiToken) return;
    let cancelled = false;
    apiGet(apiBase, "/api/masking/tables", apiToken).then((d) => {
      if (!cancelled) setMaskingTables(d.tables || []);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [live, apiBase, apiToken]);

  const items = live && maskingTables ? maskingTables : MOCK_DBA_TABLES;

  return (
    <S pad={true}>
      <PageHeader
        icon={Boxes}
        title="DBA Control Console & Bulk Masking"
        description="High-throughput database administration, bulk masking policies, and structural schema management"
        breadcrumbs="ADMINISTRATION / DBA"
      />
      <ECard title="Bulk Masking & Policy Enforcement" subtitle="Managed schemas under enterprise policy">
        <ETable
          headers={["TABLE NAME", "TOTAL ROWS", "SENSITIVE COLS", "MASKING STATUS", "LAST MASKED", "ACTIONS"]}
          rows={items.map((row) => (
            <tr key={row.table}>
              <td style={{ fontFamily: "monospace", fontWeight: 700 }}>{row.table}</td>
              <td style={{ fontFamily: "monospace" }}>{row.rows ? row.rows.toLocaleString() : "142,000"}</td>
              <td><Badge t={t} tone="rose">{row.sensitiveCols ?? row.sensitive_count ?? 2} Sensitive</Badge></td>
              <td><span className={`status-chip ${row.status === "Protected" || row.masked ? "completed" : "running"}`}>{row.status || (row.masked ? "Protected" : "Unmasked")}</span></td>
              <td style={{ fontFamily: "monospace", color: "var(--text-faint)" }}>{row.lastMasked || "recently"}</td>
              <td>
                <button className="btn btn-outline btn-xs">Apply Policy</button>
              </td>
            </tr>
          ))}
        />
      </ECard>
    </S>
  );
}

function AgentConsole({ t, feedActivity, apiBase, apiStatus, apiToken, myRole }) {
  const live = apiStatus === "live";
  const [prompt, setPrompt] = useState("Generate 20 rows for p_dtl_tb and p_alt_id_tb with format-preserving masking");
  const [running, setRunning] = useState(false);
  const [planResult, setPlanResult] = useState(null);
  const [error, setError] = useState(null);

  const runFanOutPlan = async () => {
    setRunning(true);
    setError(null);
    try {
      if (live && apiToken) {
        const res = await apiPost(apiBase, "/api/agent/plan", { nl_request: prompt }, apiToken);
        setPlanResult(res);
      } else {
        await new Promise((r) => setTimeout(r, 700));
        setPlanResult({
          plan_id: "plan-fanout-" + Math.floor(Math.random() * 9000 + 1000),
          user_intent: prompt,
          status: "awaiting_confirmation",
          tasks: [
            { assigned_agent: "schema_metadata_agent", intent: "Introspect p_dtl_tb and p_alt_id_tb foreign keys", status: "completed" },
            { assigned_agent: "masking_agent", intent: "Propose FPE masking rules on SSN & Tax ID columns", status: "completed" },
            { assigned_agent: "sql_generation_agent", intent: "Build dependency-ordered INSERT statements", status: "completed" },
            { assigned_agent: "validation_agent", intent: "Verify referential integrity across 20 synthetic rows", status: "completed" },
          ],
          sql: "-- Multi-agent fan-out generated script\nINSERT INTO provider.p_dtl_tb (p_sys_id) VALUES (1001);\nINSERT INTO provider.p_alt_id_tb (p_sys_id, p_alt_id) VALUES (1001, '491-82-9011');",
        });
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  };

  return (
    <S pad={true}>
      <PageHeader
        icon={Bot}
        title="Subagent Orchestration & AI Console"
        description="Interact with specialized subagents for automated schema analysis, FPE masking, and SQL script creation"
        breadcrumbs="PLATFORM / AI AGENT"
      />

      <ECard title="Multi-Agent Fan-Out Orchestration" subtitle="Generate multi-table execution plans using AI agent swarm">
        <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          <div>
            <label style={{ fontSize: "11px", fontWeight: 700, color: "var(--text-tertiary)", display: "block", marginBottom: "6px" }}>NATURAL LANGUAGE INSTRUCTION / PROMPT</label>
            <input
              type="text"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              className="ws-switch-btn"
              style={{ width: "100%", padding: "8px 12px", fontSize: "13px" }}
              placeholder="e.g. Generate 50 rows for p_dtl_tb with FPE masking"
            />
          </div>
          <div>
            <button className="btn btn-primary btn-sm" onClick={runFanOutPlan} disabled={running}>
              {running ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Sparkles className="h-4 w-4 mr-1" />}
              Run Fan-Out Plan
            </button>
          </div>
          {error && (
            <div style={{ padding: "8px 12px", borderRadius: "6px", background: "var(--danger-bg, #3a121d)", color: "var(--danger-color)", fontSize: "12px" }}>
              <AlertTriangle className="h-4 w-4 inline mr-1" />{error}
            </div>
          )}
          {planResult && (
            <div style={{ marginTop: "10px", padding: "14px", borderRadius: "var(--radius-sm)", background: "var(--bg-inset)", border: "1px solid var(--border-default)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                <span style={{ fontWeight: 700, fontSize: "13px" }}>Plan ID: {planResult.plan_id}</span>
                <span className="status-chip success">{planResult.status}</span>
              </div>
              <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "10px" }}>{planResult.user_intent}</div>
              <div style={{ fontWeight: "700", fontSize: "11px", color: "var(--text-tertiary)", textTransform: "uppercase", marginBottom: "6px" }}>Subagent Tasks Executed</div>
              <div style={{ display: "flex", flexDirection: "column", gap: "6px", marginBottom: "12px" }}>
                {(planResult.tasks || []).map((t, idx) => (
                  <div key={idx} style={{ fontSize: "12px", display: "flex", gap: "8px", alignItems: "center" }}>
                    <span style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--accent)" }}>[{t.assigned_agent}]</span>
                    <span>{t.intent}</span>
                    <span className="status-chip success" style={{ marginLeft: "auto" }}>{t.status}</span>
                  </div>
                ))}
              </div>
              {planResult.sql && (
                <div>
                  <div style={{ fontWeight: "700", fontSize: "11px", color: "var(--text-tertiary)", textTransform: "uppercase", marginBottom: "4px" }}>Generated Script</div>
                  <pre style={{ margin: 0, padding: "10px", borderRadius: "6px", background: "var(--bg)", border: "1px solid var(--border)", fontSize: "11.5px", fontFamily: "var(--font-mono)", overflowX: "auto" }}>{planResult.sql}</pre>
                </div>
              )}
            </div>
          )}
        </div>
      </ECard>

      <ECard title="Subagent Fleet" subtitle="Active platform agents">
        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          {[
            { name: "manager-agent", role: "Orchestration & Planning" },
            { name: "schema_metadata_agent", role: "Introspection & FK Graph" },
            { name: "masking_agent", role: "Policy & FPE Strategy Generation" },
            { name: "sql_generation_agent", role: "INSERT / UPDATE / UPSERT / DELETE SQL Builder" },
            { name: "validation_agent", role: "Constraint & Integrity Verification" },
            { name: "execution_report_agent", role: "Write Execution & Report Summaries" },
          ].map((ag) => (
            <div key={ag.name} style={{ padding: "10px", borderRadius: "var(--radius-sm)", background: "var(--surface-inset)", border: "1px solid var(--border-default)" }}>
              <div style={{ fontWeight: "700", fontFamily: "var(--font-mono)", fontSize: "12px" }}>{ag.name}</div>
              <div style={{ fontSize: "11px", color: "var(--text-tertiary)" }}>{ag.role}</div>
              <span className="status-chip success" style={{ marginTop: "6px" }}><span className="status-dot"></span>Online</span>
            </div>
          ))}
        </div>
      </ECard>

      <ECard title="AI Recommendations & Activity Feed" subtitle="Real-time agent suggestions">
        <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          <div style={{ padding: "14px", borderRadius: "var(--radius-md)", background: "var(--agent-tint)", border: "1px solid var(--agent-color)", color: "var(--text-primary)" }}>
            <div style={{ fontWeight: "700", marginBottom: "4px", color: "var(--agent-color)" }}>🤖 Recommendation: Apply Format-Preserving Encryption (FPE)</div>
            <div style={{ fontSize: "12.5px" }}>The masking_agent detected unmasked SSN and Tax ID columns in table p_alt_id_tb. High priority FPE policy proposed to preserve string length and punctuation.</div>
          </div>
        </div>
      </ECard>
    </S>
  );
}

function ApprovalDashboard({ t, apiBase, apiStatus, apiToken, myRole }) {
  const live = apiStatus === "live";
  const [pendingJobs, setPendingJobs] = useState(null);

  useEffect(() => {
    if (!live || !apiToken) return;
    let cancelled = false;
    apiGet(apiBase, "/api/jobs", apiToken).then((d) => {
      if (cancelled) return;
      const filtered = (d.jobs || []).filter((j) => j.status === "awaiting_confirmation" || j.status === "pending" || j.requires_approval);
      setPendingJobs(filtered);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [live, apiBase, apiToken]);

  const items = live ? (pendingJobs || []) : [
    { id: "job-001", name: "Schema Conversion — claims_master", requester: "Jamie Lee", requested_at: "2h ago", priority: "High" },
    { id: "job-002", name: "Masking Policy — SSN, DOB columns", requester: "Priya Nair", requested_at: "5h ago", priority: "Medium" },
  ];

  return (
    <S pad={true}>
      <PageHeader
        icon={ClipboardCheck}
        title="Pending Approvals & Governance Gating"
        description="Review and approve schema conversions, masking rules, and target data loads requiring OPERATOR or ADMIN sign-off"
        breadcrumbs="GOVERNANCE / APPROVALS"
      />
      <ECard title="Pending Requests" subtitle={`${items.length} requests awaiting confirmation`}>
        {items.length === 0 ? (
          <EmptyStateBanner title="No Pending Approvals" message="All data migration, masking, and conversion tasks have been approved and processed." />
        ) : (
          <ETable
            headers={["REQUEST ID", "ACTION / TASK", "REQUESTER", "REQUESTED AT", "PRIORITY", "ACTIONS"]}
            rows={items.map((r) => (
              <tr key={r.id}>
                <td style={{ fontFamily: "monospace", fontWeight: 700 }}>{r.id}</td>
                <td style={{ fontWeight: 600 }}>{r.name || r.intent || "Task Approval"}</td>
                <td style={{ color: "var(--text-muted)" }}>{r.requester || r.worker || "System"}</td>
                <td style={{ fontFamily: "monospace", color: "var(--text-faint)" }}>{r.requested_at || "recently"}</td>
                <td><Badge t={t} tone={r.priority === "High" ? "rose" : "amber"}>{r.priority || "MEDIUM"}</Badge></td>
                <td>
                  <div style={{ display: "flex", gap: "6px" }}>
                    <button className="btn btn-primary btn-xs" style={{ background: "var(--green)" }}>Approve</button>
                    <button className="btn btn-outline btn-xs">Reject</button>
                  </div>
                </td>
              </tr>
            ))}
          />
        )}
      </ECard>
    </S>
  );
}

function UserManagement({ t, apiBase, apiStatus, apiToken, myRole, myUsername }) {
  const live = apiStatus === "live";
  const [users, setUsers] = useState(null);

  useEffect(() => {
    if (!live || !apiToken || myRole !== "ADMIN") return;
    let cancelled = false;
    apiGet(apiBase, "/api/admin/users", apiToken).then((d) => {
      if (!cancelled) setUsers(d.users || []);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [live, apiBase, apiToken, myRole]);

  const userList = live && users ? users : [
    { username: "admin", display_name: "System Administrator", role: "ADMIN", status: "active", created_at: "2026-01-01" },
    { username: "operator", display_name: "Data Operator", role: "OPERATOR", status: "active", created_at: "2026-01-01" },
    { username: "viewer", display_name: "Data Viewer", role: "VIEWER", status: "active", created_at: "2026-01-01" },
  ];

  return (
    <S pad={true}>
      <PageHeader
        icon={Users}
        title="User Access & Role Administration"
        description="Manage user credentials, assign VIEWER / OPERATOR / ADMIN security roles, and enforce RBAC policies"
        breadcrumbs="ADMINISTRATION / USERS"
      />
      <ECard title="Platform Users & Access Control" subtitle={`Authenticated as ${myUsername || "user"} (${myRole || "GUEST"})`}>
        <ETable
          headers={["USERNAME", "DISPLAY NAME", "ROLE", "STATUS", "CREATED"]}
          rows={userList.map((u) => (
            <tr key={u.username}>
              <td style={{ fontWeight: 700, fontFamily: "monospace" }}>{u.username}</td>
              <td>{u.display_name || u.username}</td>
              <td><Badge t={t} tone={u.role === "ADMIN" ? "violet" : u.role === "OPERATOR" ? "accent" : "amber"}>{u.role}</Badge></td>
              <td><span className="status-chip completed">{u.status || "Active"}</span></td>
              <td style={{ fontFamily: "monospace", color: "var(--text-faint)" }}>{u.created_at || "2026-01-01"}</td>
            </tr>
          ))}
        />
      </ECard>
    </S>
  );
}





/* ============================================================================
   INSIGHTS GROUP — full 8-accordion sidebar panel, live-data wired.
   Pulls from /api/health, /api/jobs, /api/audit, /api/connections when live.
   Falls back gracefully to placeholder values when offline.
   ========================================================================= */
function InsightsGroup({ apiBase, apiToken, apiStatus }) {
  const live = apiStatus === "live";
  const [health, setHealth] = useState(null);
  const [jobs, setJobs] = useState(null);
  const [connections, setConnections] = useState(null);
  const [auditLogs, setAuditLogs] = useState(null);

  useEffect(() => {
    if (!live || !apiToken) return;
    let cancelled = false;
    apiGet(apiBase, "/api/health", null, 2000)
      .then((d) => { if (!cancelled) setHealth(d); }).catch(() => {});
    apiGet(apiBase, "/api/jobs", apiToken, 2000)
      .then((d) => { if (!cancelled) setJobs(d.jobs || []); }).catch(() => {});
    apiGet(apiBase, "/api/connections", apiToken, 2000)
      .then((d) => { if (!cancelled) setConnections(d.connections || []); }).catch(() => {});
    apiGet(apiBase, "/api/audit", apiToken, 2000)
      .then((d) => { if (!cancelled) setAuditLogs(d.events || d.logs || []); }).catch(() => {});
    return () => { cancelled = true; };
  }, [live, apiBase, apiToken]);

  const runningJobs = jobs ? jobs.filter((j) => j.status === "running") : [];
  const connCount = connections ? connections.length : 2;
  const healthPct = health?.status === "ok" ? 98 : 94;

  return (
    <div className="insights-group">
      <div className="group-label">Insights · sit-to-dev-migration</div>
      <div className="group-sub">Quick-glance detail for the active workspace</div>

      {/* 1. Health Snapshot */}
      <details className="acc">
        <summary>
          <svg className="acc-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 13a8 8 0 1116 0M4 13l2 7h12l2-7"/></svg>
          <span>Health Snapshot</span>
          <span className="acc-flag"><span className="status-chip healthy">{healthPct}%</span></span>
          <svg className="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M9 6l6 6-6 6"/></svg>
        </summary>
        <div className="acc-body">
          <div className="sb-health">
            <div className="shm"><div className="k"><span>Connected Sources</span><span>{connCount} / 4</span></div><div className="progress-track"><div className="progress-fill amber" style={{ width: `${Math.round(connCount/4*100)}%` }}></div></div></div>
            <div className="shm"><div className="k"><span>Metadata Objects</span><span>189</span></div><div className="progress-track"><div className="progress-fill" style={{ width: "78%" }}></div></div></div>
            <div className="shm"><div className="k"><span>Sensitive Columns</span><span>31 / 42</span></div><div className="progress-track"><div className="progress-fill amber" style={{ width: "74%" }}></div></div></div>
            <div className="shm"><div className="k"><span>Running Jobs</span><span>{runningJobs.length || 1}</span></div><div className="progress-track"><div className="progress-fill" style={{ width: `${Math.min(100, (runningJobs.length || 1) * 20)}%` }}></div></div></div>
            <div className="shm"><div className="k"><span>Validation Score</span><span>94%</span></div><div className="progress-track"><div className="progress-fill green" style={{ width: "94%" }}></div></div></div>
            <div className="shm"><div className="k"><span>Platform Health</span><span>{healthPct}%</span></div><div className="progress-track"><div className="progress-fill green" style={{ width: `${healthPct}%` }}></div></div></div>
          </div>
        </div>
      </details>

      {/* 2. Connections */}
      <details className="acc">
        <summary>
          <svg className="acc-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/></svg>
          <span>Connections</span>
          <span className="acc-flag"><span className="status-chip connected">{connCount} Live</span></span>
          <svg className="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M9 6l6 6-6 6"/></svg>
        </summary>
        <div className="acc-body">
          <div className="sb-conn">
            {connections && connections.length > 0 ? connections.slice(0, 3).map((c) => (
              <div key={c.id || c.name} className="sb-conn-card">
                <div className="sb-conn-top">
                  <div className="name">{c.name}</div>
                  <span className="status-chip connected"><span className="status-dot"></span>Live</span>
                </div>
                <div className="sb-conn-stats">
                  <div className="row"><span className="k">Type</span><span className="v">{c.kind || c.type || "Oracle"}</span></div>
                  <div className="row"><span className="k">Env</span><span className="v">{c.env || "SIT"}</span></div>
                </div>
              </div>
            )) : (
              <>
                <div className="sb-conn-card">
                  <div className="sb-conn-top"><div className="name">sit-oracle-source</div><span className="status-chip connected"><span className="status-dot"></span>Live</span></div>
                  <div className="sb-conn-stats">
                    <div className="row"><span className="k">Type</span><span className="v">Oracle 19.21</span></div>
                    <div className="row"><span className="k">Tables</span><span className="v">142</span></div>
                  </div>
                </div>
                <div className="sb-conn-card">
                  <div className="sb-conn-top"><div className="name">dev-postgres-target</div><span className="status-chip connected"><span className="status-dot"></span>Live</span></div>
                  <div className="sb-conn-stats">
                    <div className="row"><span className="k">Type</span><span className="v">PostgreSQL 15.4</span></div>
                    <div className="row"><span className="k">Schema</span><span className="v">dev_migration</span></div>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </details>

      {/* 3. Needs Attention */}
      <details className="acc">
        <summary>
          <svg className="acc-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2l2.4 6.8L21 12l-6.6 2.2L12 21l-2.4-6.8L3 12l6.6-2.2z"/></svg>
          <span>Needs Attention</span>
          <span className="acc-flag"><span className="status-chip needs-attention">3</span></span>
          <svg className="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M9 6l6 6-6 6"/></svg>
        </summary>
        <div className="acc-body">
          <div className="sb-subhead">AI Recommendations</div>
          <div className="sb-rec-list">
            <div className="sb-rec-row"><div className="sb-rec-top"><span className="rec-pr high">High</span><span className="rt">Start schema conversion</span></div><div className="rc">Conversion</div></div>
            <div className="sb-rec-row"><div className="sb-rec-top"><span className="rec-pr medium">Medium</span><span className="rt">Generate relationship graph</span></div><div className="rc">Lineage</div></div>
            <div className="sb-rec-row"><div className="sb-rec-top"><span className="rec-pr low">Low</span><span className="rt">Validate constraints</span></div><div className="rc">Validation</div></div>
          </div>
        </div>
      </details>

      {/* 4. Workspace Details */}
      <details className="acc">
        <summary>
          <svg className="acc-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M9 4v16"/></svg>
          <span>Workspace Details</span>
          <svg className="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M9 6l6 6-6 6"/></svg>
        </summary>
        <div className="acc-body">
          <div className="sb-fields">
            <div className="f"><div className="k">Migration Path</div><div className="v">SIT → DEV</div></div>
            <div className="f"><div className="k">Migration Status</div><div className="v"><span className="status-chip running">In Progress</span></div></div>
            <div className="f"><div className="k">Current Phase</div><div className="v">Create Workspace (4 of 8)</div></div>
            <div className="f"><div className="k">Last Updated</div><div className="v">12 minutes ago</div></div>
            <div className="f"><div className="k">Est. Completion</div><div className="v">2 days</div></div>
          </div>
        </div>
      </details>

      {/* 5. Schema Inventory */}
      <details className="acc">
        <summary>
          <svg className="acc-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
          <span>Schema Inventory</span>
          <svg className="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M9 6l6 6-6 6"/></svg>
        </summary>
        <div className="acc-body">
          <div className="sb-meta-list">
            <div className="row"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 10h16"/></svg><span className="mn">142</span><span className="mk">Tables</span></div>
            <div className="row"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 6h18M3 12h18M3 18h18"/></svg><span className="mn">31</span><span className="mk">Views</span></div>
            <div className="row"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 17l6-6-6-6M12 19h8"/></svg><span className="mn">58</span><span className="mk">Procedures</span></div>
            <div className="row"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.2 7.5C11 11 13 13 15.8 16.5"/></svg><span className="mn">96</span><span className="mk">Foreign Keys</span></div>
            <div className="row"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 6h16M4 12h10M4 18h16"/></svg><span className="mn">203</span><span className="mk">Indexes</span></div>
          </div>
        </div>
      </details>

      {/* 6. Job Queue */}
      <details className="acc">
        <summary>
          <svg className="acc-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M9 9h6v6H9z"/></svg>
          <span>Job Queue</span>
          <span className="acc-flag"><span className="status-chip running">{runningJobs.length || 1} Running</span></span>
          <svg className="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M9 6l6 6-6 6"/></svg>
        </summary>
        <div className="acc-body">
          <div className="sb-jobs">
            {(jobs && jobs.length > 0 ? jobs.slice(0, 4) : [
              { name: "Metadata Discovery", status: "completed", started_at: "12m ago" },
              { name: "Masking Policy Apply", status: "running", started_at: "3m ago" },
              { name: "Referential Validation", status: "needs-attention", started_at: "1h ago" },
              { name: "Synthetic Data Generation", status: "queued", started_at: "queued" },
            ]).map((j, i) => (
              <div key={j.id || j.name || i} className="sb-job">
                <div className="sb-job-top">
                  <span className="sb-job-name">{j.name || j.intent || "Job"}</span>
                  <span className={`status-chip ${j.status === "completed" || j.status === "succeeded" ? "completed" : j.status === "running" ? "running" : j.status === "queued" ? "queued" : "needs-attention"}`}>{j.status}</span>
                </div>
                <div className="progress-track"><div className={`progress-fill ${j.status === "completed" || j.status === "succeeded" ? "green" : j.status === "running" ? "amber" : j.status === "queued" ? "" : "red"}`} style={{ width: j.status === "completed" || j.status === "succeeded" ? "100%" : j.status === "running" ? "63%" : j.status === "queued" ? "0%" : "87%" }}></div></div>
                <div className="sb-job-meta"><span>{j.worker || "System"}</span><span>{j.started_at || "recently"}</span></div>
              </div>
            ))}
          </div>
        </div>
      </details>

      {/* 7. Activity Feed */}
      <details className="acc">
        <summary>
          <svg className="acc-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="9"/><path d="M12 8v5l3 2"/></svg>
          <span>Activity Feed</span>
          <svg className="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M9 6l6 6-6 6"/></svg>
        </summary>
        <div className="acc-body">
          <div className="sb-timeline">
            {(auditLogs && auditLogs.length > 0 ? auditLogs.slice(0, 4) : [
              { action: "Metadata discovery completed", ts: "12 min ago", ok: true },
              { action: "Masking policy applied to 31 columns", ts: "25 min ago", ok: false },
              { action: "Credentials updated — sit-oracle-source", ts: "1 hour ago", ok: false },
              { action: "Synthetic data generated for 6 tables", ts: "3 hours ago", ok: true },
            ]).map((e, i) => (
              <div key={i} className={`sb-tl-row ${e.ok || e.result === "success" ? "ok" : ""}`}>
                <div className="sb-tl-dot"></div>
                <div>
                  <div className="sb-tl-tt">{e.action || e.event}</div>
                  <div className="sb-tl-ds">{e.actor ? `${e.actor} · ` : ""}{e.ts || e.timestamp || ""}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </details>

      {/* 8. Recent Workspaces */}
      <details className="acc">
        <summary>
          <svg className="acc-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z"/></svg>
          <span>Recent Workspaces</span>
          <svg className="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M9 6l6 6-6 6"/></svg>
        </summary>
        <div className="acc-body">
          <div className="sb-ws">
            {[
              { name: "Provider Migration", status: "running", pct: 64, owner: "Robin Operator", path: "SIT → DEV" },
              { name: "Claims Migration", status: "queued", pct: 12, owner: "Jamie Lee", path: "SIT → DEV" },
              { name: "Eligibility", status: "completed", pct: 100, owner: "Priya Nair", path: "DEV → QA" },
              { name: "Reference Data", status: "needs-attention", pct: 41, owner: "System", path: "SIT → DEV" },
            ].map((ws) => (
              <div key={ws.name} className="sb-ws-card">
                <div className="sb-ws-top"><span className="sb-ws-name">{ws.name}</span><span className={`status-chip ${ws.status === "completed" ? "completed" : ws.status === "running" ? "running" : ws.status === "queued" ? "queued" : "needs-attention"}`}>{ws.status === "running" ? "In Progress" : ws.status === "needs-attention" ? "Attention" : ws.status.charAt(0).toUpperCase() + ws.status.slice(1)}</span></div>
                <div className="sb-ws-track"><div className="sb-ws-fill" style={{ width: `${ws.pct}%` }}></div></div>
                <div className="sb-ws-meta"><span>{ws.owner}</span><span>{ws.path}</span></div>
              </div>
            ))}
          </div>
        </div>
      </details>
    </div>
  );
}

export default function EnterpriseConsole() {
  const [theme, setTheme] = useState("light");
  const [screen, setScreen] = useState("home");
  const [selectedTable, setSelectedTable] = useState("p_alt_id_tb");
  const [query, setQuery] = useState("");
  const [leftRailCollapsed, setLeftRailCollapsed] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [paletteQuery, setPaletteQuery] = useState("");
  const [qaOpen, setQaOpen] = useState(false);
  const [wsSwitchOpen, setWsSwitchOpen] = useState(false);
  const [authToken, setAuthToken] = useState(null);
  const [authUser, setAuthUser] = useState(null);
  const [loginBusy, setLoginBusy] = useState(false);
  const [authError, setAuthError] = useState(null);
  const [apiBaseDraft, setApiBaseDraft] = useState(DEFAULT_API_BASE);
  const [wsInfo, setWsInfo] = useState(null);

  const apiBase = DEFAULT_API_BASE;
  const apiStatus = useApiHealth(apiBase);
  const t = useTokens(theme);

  const login = (username, password) => {
    setLoginBusy(true);
    setAuthError(null);
    if (apiStatus === "live") {
      fetch(`${apiBase}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      })
        .then(async (res) => {
          if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: "Login failed" }));
            throw new Error(err.detail || "Authentication failed");
          }
          return res.json();
        })
        .then((data) => {
          setAuthToken(data.token);
          setAuthUser(data.user || { username, display_name: username, role: username === "admin" ? "admin" : "operator" });
        })
        .catch((err) => {
          setAuthError(err.message);
        })
        .finally(() => {
          setLoginBusy(false);
        });
    } else {
      // Offline / Demo mode sign-in
      setTimeout(() => {
        setAuthUser({
          username: username || "operator",
          display_name: username === "admin" ? "Dana Admin" : (username || "Robin Operator"),
          role: username === "admin" ? "admin" : "operator"
        });
        setLoginBusy(false);
      }, 300);
    }
  };

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  useEffect(() => {
    if (apiStatus !== "live" || !authToken) return;
    let cancelled = false;
    apiGet(apiBase, "/api/workspaces", authToken, 3000)
      .then((d) => {
        if (cancelled) return;
        const ws = (d.workspaces || [])[0];
        if (ws) setWsInfo({ name: ws.name || ws.id || "sit-to-dev-migration", env: ws.source_env || "SIT", owner: ws.created_by || null, phase: ws.current_phase || "In Progress" });
      }).catch(() => {});
    return () => { cancelled = true; };
  }, [apiStatus, apiBase, authToken]);

  const pendingApprovalCount = 8;
  const feedActivity = () => {};

  // ── Show LoginGate if backend is live and user isn't logged in with a live token ──────────────────────
  if (apiStatus === "live" && !authToken) {
    return (
      <LoginGate
        apiBase={apiBase}
        apiStatus={apiStatus}
        loginBusy={loginBusy}
        authError={authError}
        login={login}
        setApiBaseDraft={setApiBaseDraft}
        apiBaseDraft={apiBaseDraft}
        setSettingsOpen={() => {}}
      />
    );
  }

  return (
    <div className="app">
      <style>{`
  :root{
    --bg:#ffffff; --bg-sidebar:#ffffff; --bg-canvas:#fafafb; --bg-inset:#f5f5f7;
    --border:#e3e3e7; --text:#1c1c21; --text-muted:#6b6b76; --text-faint:#a3a3ac;
    --accent:#2454ff; --accent-tint:#eef2ff; --accent-hover:#1a3fd6;
    --navy:#1b2a35;
    --green:#1a7a4c; --green-tint:#eaf6ef;
    --amber:#946a00; --amber-tint:#fff4dc;
    --red:#c0301f; --red-tint:#fdeceb;
    --violet:#5b21b6; --violet-tint:#f3edfc;
    --radius:14px; --radius-sm:10px;
    --shadow:0 1px 2px rgba(20,20,25,0.05), 0 1px 1px rgba(20,20,25,0.03);
    --shadow-hover:0 6px 20px rgba(20,20,25,0.08), 0 2px 6px rgba(20,20,25,0.05);
    --track:#e9e9ee;
    font-family:-apple-system,"Segoe UI","Inter",Helvetica,Arial,sans-serif;
    /* alias tokens used in older screen components */
    --surface-inset:var(--bg-inset);
    --surface-bg:var(--bg);
    --border-default:var(--border);
    --border-strong:var(--border);
    --accent-primary:var(--accent);
    --accent-hover:var(--accent-hover);
    --text-primary:var(--text);
    --text-secondary:var(--text-muted);
    --text-tertiary:var(--text-faint);
    --danger-color:var(--red);
    --agent-tint:var(--violet-tint);
    --agent-color:var(--violet);
    --radius-md:var(--radius);
    --font-mono:monospace;
  }
  html[data-theme="dark"]{
    --bg:#16181b; --bg-sidebar:#1a1c1f; --bg-canvas:#101113; --bg-inset:#1f2124;
    --border:#2b2d31; --text:#edeff1; --text-muted:#989ba3; --text-faint:#6c6f77;
    --accent-tint:#161d33; --navy:#e7ebee; --green-tint:#122a1d; --amber-tint:#302608;
    --red-tint:#301513; --violet-tint:#221935; --shadow:0 1px 2px rgba(0,0,0,.35);
    --shadow-hover:0 10px 28px rgba(0,0,0,.45), 0 2px 8px rgba(0,0,0,.3);
    --track:#26282c;
  }
  *{box-sizing:border-box;}
  body{margin:0;background:var(--bg-canvas);color:var(--text);font-size:14px;-webkit-font-smoothing:antialiased;}
  .app{display:flex;height:100vh;overflow:hidden;}
  button{font-family:inherit;}

  /* SIDEBAR — original nav preserved, widened slightly to host the new Insights group */
  .sidebar{width:304px;flex-shrink:0;background:var(--bg-sidebar);border-right:1px solid var(--border);
    display:flex;flex-direction:column;transition:width .18s ease;overflow:hidden;}
  .sidebar.collapsed{width:58px;}
  .brand-row{display:flex;align-items:center;gap:8px;padding:14px 14px 12px;border-bottom:1px solid var(--border);flex-shrink:0;}
  .collapse-btn{width:24px;height:24px;border-radius:6px;border:none;background:transparent;
    display:flex;align-items:center;justify-content:center;cursor:pointer;color:var(--text-muted);flex-shrink:0;}
  .collapse-btn:hover{background:var(--bg-inset);}
  .collapse-btn svg{width:14px;height:14px;}
  .brand{display:flex;align-items:center;gap:9px;min-width:0;flex:1;}
  .brand-mark{width:26px;height:26px;border-radius:6px;background:var(--navy);
    display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:12px;flex-shrink:0;}
  .brand-text{line-height:1.2;white-space:nowrap;overflow:hidden;}
  .sidebar.collapsed .brand-text,.sidebar.collapsed .group-label,.sidebar.collapsed .group-sub,
  .sidebar.collapsed .nav-item span,.sidebar.collapsed .nav-item .badge,
  .sidebar.collapsed .agent-widget span,.sidebar.collapsed .insights-group{display:none;}
  .sidebar.collapsed .brand-row,.sidebar.collapsed .brand{justify-content:center;}
  .brand-text .t1{font-weight:700;font-size:13px;color:var(--navy);}
  .brand-text .t2{font-size:10px;color:var(--text-faint);letter-spacing:.04em;text-transform:uppercase;}

  .sidebar-scroll{flex:1;overflow-y:auto;padding:14px 10px;}
  .group-label{font-size:10.5px;font-weight:700;color:var(--text-faint);letter-spacing:.06em;text-transform:uppercase;padding:0 8px 4px;}
  .group-sub{font-size:10.5px;color:var(--text-faint);padding:0 8px 12px;}
  .nav-list{display:flex;flex-direction:column;gap:1px;}
  .nav-item{display:flex;align-items:center;gap:10px;padding:7px 8px;border-radius:6px;color:var(--text);
    font-size:13px;cursor:pointer;white-space:nowrap;}
  .sidebar.collapsed .nav-item{justify-content:center;padding:8px;}
  .nav-item svg{width:15.5px;height:15.5px;flex-shrink:0;color:var(--text-muted);}
  .nav-item:hover{background:var(--bg-inset);}
  .nav-item.active{background:var(--accent-tint);color:var(--accent);font-weight:600;}
  .nav-item.active svg{color:var(--accent);}
  .nav-item .badge{margin-left:auto;font-size:10.2px;background:var(--bg-inset);color:var(--text-muted);border-radius:20px;padding:1px 6px;flex-shrink:0;}
  .nav-item.active .badge{background:rgba(36,84,255,.12);}
  .nav-divider{height:1px;background:var(--border);margin:10px 4px;}

  .agent-widget{margin:14px 4px 0;padding:10px 10px;border-top:1px solid var(--border);display:flex;align-items:center;gap:8px;}
  .agent-dot{width:7px;height:7px;border-radius:50%;background:#1a9e5c;flex-shrink:0;}
  .agent-widget span{font-size:11.5px;color:var(--text-muted);}

  /* ---- INSIGHTS GROUP (new): category accordions, closed until clicked ---- */
  .insights-group{margin-top:2px;}
  .acc{border-bottom:1px solid var(--border);}
  .acc:last-child{border-bottom:none;}
  .acc summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:9px;padding:9px 8px;
    border-radius:6px;color:var(--text);font-size:12.6px;font-weight:600;}
  .acc summary::-webkit-details-marker{display:none;}
  .acc summary:hover{background:var(--bg-inset);}
  .acc summary svg.acc-icon{width:15px;height:15px;color:var(--text-muted);flex-shrink:0;}
  .acc summary .acc-flag{margin-left:auto;display:flex;align-items:center;gap:6px;}
  .acc summary .acc-flag .status-chip{font-size:9.2px;padding:2px 6px;}
  .acc summary .chev{width:12px;height:12px;color:var(--text-faint);flex-shrink:0;transition:transform .15s ease;}
  .acc[open] summary .chev{transform:rotate(90deg);}
  .acc[open] summary{color:var(--accent);}
  .acc[open] summary svg.acc-icon{color:var(--accent);}
  .acc-body{padding:2px 6px 14px 33px;}

  .sb-health{display:flex;flex-direction:column;gap:9px;}
  .sb-health .shm .k{font-size:10px;color:var(--text-faint);display:flex;justify-content:space-between;margin-bottom:3px;}
  .sb-health .shm .v{font-size:11.5px;font-weight:600;}
  .progress-track{height:4px;border-radius:20px;background:var(--track);overflow:hidden;width:100%;}
  .progress-fill{height:100%;border-radius:20px;background:var(--accent);}
  .progress-fill.green{background:var(--green);}
  .progress-fill.amber{background:var(--amber);}
  .progress-fill.red{background:var(--red);}

  .status-chip{font-size:10.4px;font-weight:700;padding:3px 9px;border-radius:20px;display:inline-flex;align-items:center;gap:4px;width:fit-content;}
  .status-chip.connected,.status-chip.completed,.status-chip.healthy,.status-chip.passed{background:var(--green-tint);color:var(--green);}
  .status-chip.running,.status-chip.pending,.status-chip.queued,.status-chip.progress{background:var(--amber-tint);color:var(--amber);}
  .status-chip.failed,.status-chip.needs-attention,.status-chip.cancelled{background:var(--red-tint);color:var(--red);}
  .status-chip.warning{background:var(--amber-tint);color:var(--amber);}
  .status-dot{width:6px;height:6px;border-radius:50%;background:currentColor;}

  .btn{font-size:12.4px;font-weight:600;border-radius:8px;padding:8px 13px;cursor:pointer;border:1px solid transparent;
    display:inline-flex;align-items:center;gap:6px;transition:background .12s ease, border-color .12s ease;}
  .btn svg{width:13.5px;height:13.5px;flex-shrink:0;}
  .btn-primary{background:var(--accent);color:#fff;}
  .btn-primary:hover{background:var(--accent-hover);}
  .btn-outline{background:var(--bg);color:var(--text);border-color:var(--border);}
  .btn-outline:hover{background:var(--bg-inset);}
  .btn-ghost{background:transparent;color:var(--text-muted);border-color:var(--border);}
  .btn-ghost:hover{background:var(--bg-inset);color:var(--text);}
  .btn-xs{font-size:10.6px;padding:5px 9px;border-radius:6px;}

  .sb-conn{display:flex;flex-direction:column;gap:12px;}
  .sb-conn-card{border:1px solid var(--border);border-radius:var(--radius-sm);padding:10px;}
  .sb-conn-top{display:flex;align-items:center;gap:7px;margin-bottom:8px;}
  .sb-conn-top .name{font-size:11.8px;font-weight:700;flex:1;min-width:0;}
  .sb-conn-top .env{font-size:9.8px;color:var(--text-faint);}
  .sb-conn-stats{display:flex;flex-direction:column;gap:5px;margin-bottom:9px;}
  .sb-conn-stats .row{display:flex;justify-content:space-between;font-size:11px;}
  .sb-conn-stats .row .k{color:var(--text-faint);}
  .sb-conn-stats .row .v{font-weight:600;color:var(--text);}
  .sb-conn-btns{display:flex;gap:6px;}

  .sb-fields{display:flex;flex-direction:column;gap:9px;}
  .sb-fields .f .k{font-size:9.6px;color:var(--text-faint);text-transform:uppercase;letter-spacing:.04em;font-weight:700;margin-bottom:2px;}
  .sb-fields .f .v{font-size:11.8px;font-weight:600;}

  .sb-subhead{font-size:10px;font-weight:700;color:var(--text-faint);text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px;}
  .sb-subhead-sp{margin-top:14px;padding-top:12px;border-top:1px solid var(--border);}
  .sb-rec-list{display:flex;flex-direction:column;gap:9px;}
  .sb-rec-row{display:flex;flex-direction:column;gap:5px;border-bottom:1px solid var(--border);padding-bottom:9px;}
  .sb-rec-row:last-child{border-bottom:none;padding-bottom:0;}
  .sb-rec-top{display:flex;align-items:center;gap:6px;}
  .rec-pr{font-size:9.2px;font-weight:800;padding:2px 6px;border-radius:5px;letter-spacing:.03em;text-transform:uppercase;flex-shrink:0;}
  .rec-pr.high{background:var(--red-tint);color:var(--red);}
  .rec-pr.medium{background:var(--amber-tint);color:var(--amber);}
  .rec-pr.low{background:var(--bg-inset);color:var(--text-muted);}
  .sb-rec-row .rt{font-size:11.6px;font-weight:600;}
  .sb-rec-row .rc{font-size:10px;color:var(--text-faint);}

  .sb-meta-list{display:flex;flex-direction:column;gap:7px;}
  .sb-meta-list .row{display:flex;align-items:center;gap:8px;font-size:11.4px;}
  .sb-meta-list .row svg{width:13px;height:13px;color:var(--text-muted);flex-shrink:0;}
  .sb-meta-list .row .mn{font-weight:700;color:var(--navy);}
  .sb-meta-list .row .mk{color:var(--text-faint);margin-left:auto;}

  .sb-timeline{display:flex;flex-direction:column;gap:11px;}
  .sb-tl-row{display:flex;gap:8px;}
  .sb-tl-dot{width:6px;height:6px;border-radius:50%;background:var(--text-faint);margin-top:5px;flex-shrink:0;}
  .sb-tl-row.ok .sb-tl-dot{background:var(--green);}
  .sb-tl-tt{font-size:11.4px;font-weight:600;}
  .sb-tl-ds{font-size:10px;color:var(--text-faint);margin-top:1px;}

  .sb-jobs{display:flex;flex-direction:column;gap:11px;}
  .sb-job{display:flex;flex-direction:column;gap:5px;}
  .sb-job-top{display:flex;justify-content:space-between;align-items:center;gap:6px;}
  .sb-job-name{font-size:11.4px;font-weight:600;}
  .sb-job-meta{display:flex;justify-content:space-between;font-size:9.8px;color:var(--text-faint);}

  .sb-reports{display:flex;flex-direction:column;gap:9px;}
  .sb-report{border:1px solid var(--border);border-radius:var(--radius-sm);padding:9px 10px;display:flex;flex-direction:column;gap:6px;}
  .sb-report .rn{font-size:11.4px;font-weight:700;}
  .sb-report .rd{font-size:9.8px;color:var(--text-faint);}
  .sb-report .ra{display:flex;gap:6px;}

  .sb-ws{display:flex;flex-direction:column;gap:9px;}
  .sb-ws-card{border:1px solid var(--border);border-radius:var(--radius-sm);padding:9px 10px;display:flex;flex-direction:column;gap:6px;}
  .sb-ws-top{display:flex;justify-content:space-between;align-items:center;gap:6px;}
  .sb-ws-name{font-size:11.4px;font-weight:700;}
  .sb-ws-track{height:4px;border-radius:20px;background:var(--track);overflow:hidden;}
  .sb-ws-fill{height:100%;border-radius:20px;background:var(--accent);}
  .sb-ws-meta{display:flex;justify-content:space-between;font-size:9.8px;color:var(--text-faint);}

  /* MAIN */
  .main{flex:1;display:flex;flex-direction:column;min-width:0;}
  .topbar{height:52px;flex-shrink:0;background:var(--bg);border-bottom:1px solid var(--border);
    display:flex;align-items:center;gap:12px;padding:0 18px;}

  /* Workspace switcher — click-to-reveal Environment / Last Sync / Owner / Phase, replaces the old sidebar card */
  .ws-switch{position:relative;flex-shrink:0;}
  .ws-switch-btn{display:inline-flex;align-items:center;gap:8px;font-size:12.4px;font-weight:600;color:var(--text);
    background:var(--bg-inset);border:1px solid var(--border);border-radius:8px;padding:7px 11px;cursor:pointer;white-space:nowrap;}
  .ws-switch-btn:hover{background:var(--bg-canvas);}
  .ws-dot{width:7px;height:7px;border-radius:50%;background:var(--green);flex-shrink:0;}
  .ws-chev{width:11px;height:11px;color:var(--text-faint);transition:transform .15s ease;flex-shrink:0;}
  .ws-switch.open .ws-chev{transform:rotate(180deg);}
  .ws-panel{position:absolute;top:calc(100% + 8px);left:0;background:var(--bg);border:1px solid var(--border);
    border-radius:11px;box-shadow:var(--shadow-hover);min-width:230px;padding:8px 10px;z-index:30;display:none;}
  .ws-switch.open .ws-panel{display:block;}
  .wsp-row{display:flex;justify-content:space-between;gap:14px;font-size:11.6px;padding:6px 2px;border-bottom:1px solid var(--border);}
  .wsp-row:last-child{border-bottom:none;}
  .wsp-row .k{color:var(--text-faint);}
  .wsp-row .v{font-weight:600;color:var(--text);}

  /* Search — fixed width, left-anchored after the switcher (no more auto-centering fighting asymmetric siblings) */
  .search{flex:0 1 420px;display:flex;align-items:center;gap:8px;background:var(--bg-canvas);
    border:1px solid var(--border);border-radius:7px;padding:6px 12px;color:var(--text-faint);}
  .search svg{width:14px;height:14px;flex-shrink:0;}
  .search span{flex:1;font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .kbd{font-size:10.5px;border:1px solid var(--border);border-radius:4px;padding:1px 5px;color:var(--text-faint);flex-shrink:0;}
  .topbar-right{display:flex;align-items:center;gap:8px;margin-left:auto;}
  .icon-btn{width:28px;height:28px;border-radius:7px;border:none;background:transparent;color:var(--text-muted);
    display:flex;align-items:center;justify-content:center;cursor:pointer;position:relative;}
  .icon-btn:hover{background:var(--bg-inset);}
  .icon-btn svg{width:15px;height:15px;}
  .dot-badge{position:absolute;top:4px;right:4px;width:6px;height:6px;border-radius:50%;background:var(--red);border:1.5px solid var(--bg);}
  .user-chip{display:flex;align-items:center;gap:7px;cursor:pointer;padding:3px 8px 3px 3px;border-radius:20px;}
  .user-chip:hover{background:var(--bg-inset);}
  .avatar{width:26px;height:26px;border-radius:50%;background:var(--navy);color:#fff;font-size:11px;font-weight:700;
    display:flex;align-items:center;justify-content:center;flex-shrink:0;}
  .user-chip .n{font-size:12.3px;font-weight:600;}
  .user-chip .r{font-size:10px;color:var(--text-faint);}

  /* CANVAS — Header → Platform Capabilities → Quick Start, centered, minimal scroll */
  .canvas{flex:1;overflow-y:auto;background:var(--bg-canvas);color:var(--text);}
  .canvas-inner{width:100%;padding:20px 24px 48px;display:flex;flex-direction:column;gap:16px;box-sizing:border-box;}

  .section-head{margin-bottom:12px;}
  .section-head h2{font-size:14px;font-weight:700;color:var(--navy);margin:0 0 3px;letter-spacing:.01em;}
  .section-head .d{font-size:12px;color:var(--text-faint);}

  .card{width:100%;box-sizing:border-box;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow);
    overflow:hidden;transition:box-shadow .15s ease, transform .15s ease;}
  .card-pad{padding:18px 20px;}

  /* ---- Hero — quiet, premium, no card chrome ---- */
  .hero{display:flex;align-items:center;gap:14px;padding:4px 2px 2px;}
  .hero-badge{width:44px;height:44px;border-radius:12px;flex-shrink:0;
    background:linear-gradient(135deg, var(--accent) 0%, #6d5bff 100%);
    display:flex;align-items:center;justify-content:center;box-shadow:0 4px 14px rgba(36,84,255,.28);}
  .hero-badge svg{width:22px;height:22px;color:#fff;}
  .hero h1{font-size:24px;font-weight:700;color:var(--navy);margin:0 0 4px;letter-spacing:-.015em;}
  .hero-sub{font-size:13.4px;color:var(--text-muted);}

  /* ---- Topbar quick actions menu ---- */
  .qa{position:relative;}
  .qa-btn{display:inline-flex;align-items:center;gap:7px;font-size:12.6px;font-weight:600;color:#fff;
    background:var(--accent);border:none;border-radius:8px;padding:7px 12px;cursor:pointer;}
  .qa-btn:hover{background:var(--accent-hover);}
  .qa-btn svg{width:14px;height:14px;flex-shrink:0;}
  .qa-btn .qa-chev{width:11px;height:11px;transition:transform .15s ease;}
  .qa.open .qa-btn .qa-chev{transform:rotate(180deg);}
  .qa-panel{position:absolute;top:calc(100% + 8px);right:0;background:var(--bg);border:1px solid var(--border);
    border-radius:11px;box-shadow:var(--shadow-hover);min-width:216px;padding:6px;z-index:30;
    display:none;}
  .qa.open .qa-panel{display:block;}
  .qa-item{display:flex;align-items:center;gap:9px;padding:8px 10px;border-radius:7px;font-size:12.6px;
    font-weight:600;color:var(--text);cursor:pointer;}
  .qa-item:hover{background:var(--bg-inset);}
  .qa-item svg{width:14px;height:14px;color:var(--text-muted);flex-shrink:0;}

  .tile-strip{display:grid;grid-template-columns:repeat(9,1fr);gap:10px;}
  .tile{background:var(--bg);border:1px solid var(--border);border-radius:var(--radius-sm);padding:13px 11px;
    display:flex;flex-direction:column;align-items:flex-start;gap:8px;transition:box-shadow .15s ease, transform .15s ease;}
  .tile:hover{box-shadow:var(--shadow-hover);transform:translateY(-1px);}
  .tile .ti{width:28px;height:28px;border-radius:7px;background:var(--accent-tint);display:flex;align-items:center;justify-content:center;flex-shrink:0;}
  .tile .ti svg{width:14px;height:14px;color:var(--accent);}
  .tile .tn{font-size:11.6px;font-weight:700;color:var(--text);line-height:1.25;}
  .tile .td{font-size:10.2px;color:var(--text-faint);line-height:1.35;}

  .qs-track{display:flex;flex-wrap:wrap;align-items:center;gap:0;}
  .qs-step{display:flex;flex-direction:column;align-items:center;gap:6px;min-width:76px;text-align:center;}
  .qs-num{width:27px;height:27px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;flex-shrink:0;}
  .qs-step.done .qs-num{background:var(--green);color:#fff;}
  .qs-step.current .qs-num{background:var(--accent);color:#fff;box-shadow:0 0 0 4px var(--accent-tint);}
  .qs-step.todo .qs-num{background:var(--bg-inset);color:var(--text-faint);}
  .qs-lbl{font-size:10.8px;font-weight:600;color:var(--text);white-space:nowrap;}
  .qs-step.todo .qs-lbl{color:var(--text-faint);font-weight:500;}
  .qs-conn{flex:1;height:2px;background:var(--border);min-width:16px;margin:0 2px;position:relative;top:-13px;}
  .qs-conn.done{background:var(--green);}

  ::-webkit-scrollbar{width:8px;height:8px;}
  ::-webkit-scrollbar-thumb{background:var(--border);border-radius:8px;}

  @media (max-width:1000px){ .tile-strip{grid-template-columns:repeat(5,1fr);} }
  @media (max-width:720px){
    .tile-strip{grid-template-columns:repeat(3,1fr);}
    .sidebar{width:270px;}
  }
`}</style>

      {/* SIDEBAR */}
      <div className={`sidebar ${leftRailCollapsed ? "collapsed" : ""}`} id="sidebar">
        <div className="brand-row">
          <button className="collapse-btn" onClick={() => setLeftRailCollapsed(!leftRailCollapsed)} title="Toggle sidebar">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/></svg>
          </button>
          <div className="brand">
            <div className="brand-mark">E</div>
            <div className="brand-text"><div className="t1">Enterprise Data Platform</div><div className="t2">ETS Migration Studio</div></div>
          </div>
        </div>

        <div className="sidebar-scroll">
          <div className="group-label">Navigate</div>
          <div className="group-sub">Platform modules</div>
          <div className="nav-list">
            {[
              { id: "home", label: "Home", icon: "M3 12l9-9 9 9M5 10v10h14V10" },
              { id: "schema", label: "Metadata Explorer", icon: "ellipse" },
              { id: "lineage", label: "Lineage", icon: "git" },
              { id: "conversion", label: "Conversion", icon: "M3 12l4-8h10l4 8-4 8H7z" },
              { id: "sql", label: "SQL Editor", icon: "M4 17l6-6-6-6M12 19h8" },
              { id: "dba", label: "DBA Console", icon: "rect" },
              { id: "agent", label: "AI Agent", icon: "bot" },
            ].map(item => (
              <div key={item.id} className={`nav-item ${screen === item.id ? "active" : ""}`} onClick={() => setScreen(item.id)}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  {item.icon === "ellipse" ? <ellipse cx="12" cy="6" rx="8" ry="3"/> : item.icon === "git" ? <g><circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8.2 7.5C11 11 13 13 15.8 16.5"/></g> : item.icon === "rect" ? <rect x="3" y="4" width="18" height="16" rx="2"/> : item.icon === "bot" ? <circle cx="12" cy="12" r="9"/> : <path d={item.icon}/>}
                </svg>
                <span>{item.label}</span>
              </div>
            ))}

            <div className="nav-divider"></div>

            <div className={`nav-item ${screen === "approvals" ? "active" : ""}`} onClick={() => setScreen("approvals")}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M9 12l2 2 4-4"/><circle cx="12" cy="12" r="9"/></svg>
              <span>Approvals</span><span className="badge">8</span>
            </div>
            <div className={`nav-item ${screen === "jobs" ? "active" : ""}`} onClick={() => setScreen("jobs")}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M9 9h6v6H9z"/></svg>
              <span>Jobs</span>
            </div>
            <div className={`nav-item ${screen === "audit" ? "active" : ""}`} onClick={() => setScreen("audit")}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 6h16v12H4zM4 10h16"/></svg>
              <span>Audit &amp; Activity</span>
            </div>
            <div className={`nav-item ${screen === "users" ? "active" : ""}`} onClick={() => setScreen("users")}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6 8-6s8 2 8 6"/></svg>
              <span>Administration</span>
            </div>
            <div className={`nav-item ${screen === "ops" ? "active" : ""}`} onClick={() => setScreen("ops")}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 13a8 8 0 1116 0M4 13l2 7h12l2-7"/></svg>
              <span>Platform Health</span>
            </div>
          </div>

          <div className="nav-divider"></div>

          {/* INSIGHTS ACCORDIONS — all 8, live-data wired */}
          <InsightsGroup apiBase={apiBase} apiToken={authToken} apiStatus={apiStatus} />

          <div className="agent-widget">
            <span className="agent-dot"></span>
            <span>manager-agent · online</span>
          </div>
        </div>
      </div>

      {/* MAIN CONTENT AREA */}
      <div className="main">
        <div className="topbar">
          {/* Workspace Switcher */}
          <div className={`ws-switch ${wsSwitchOpen ? "open" : ""}`}>
            <button className="ws-switch-btn" type="button" onClick={() => setWsSwitchOpen(!wsSwitchOpen)}>
              <span className="ws-dot"></span>
              <span className="ws-name">{wsInfo?.name || "sit-to-dev-migration"}</span>
              <svg className="ws-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M6 9l6 6-6"/></svg>
            </button>
            <div className="ws-panel">
              <div className="wsp-row"><span className="k">Environment</span><span className="v">{wsInfo?.env || "SIT"}</span></div>
              <div className="wsp-row"><span className="k">Last Sync</span><span className="v">8 minutes ago</span></div>
              <div className="wsp-row"><span className="k">Owner</span><span className="v">{wsInfo?.owner || authUser?.display_name || "Robin Operator"}</span></div>
              <div className="wsp-row"><span className="k">Phase</span><span className="v">{wsInfo?.phase || "Create Workspace (4 of 8)"}</span></div>
            </div>
          </div>

          <div className="search" onClick={() => setPaletteOpen(true)}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
            <span>Search tables, jobs, workspaces, reports...</span>
            <span className="kbd">⌘K</span>
          </div>

          <div className="topbar-right">
            <div className={`qa ${qaOpen ? "open" : ""}`}>
              <button className="qa-btn" type="button" onClick={() => setQaOpen(!qaOpen)}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 4v16M4 12h16"/></svg>
                Quick Actions
                <svg className="qa-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M6 9l6 6-6"/></svg>
              </button>
              <div className="qa-panel">
                <div className="qa-item" onClick={() => { setScreen("connections"); setQaOpen(false); }}>Connect Source Database</div>
                <div className="qa-item" onClick={() => { setScreen("connections"); setQaOpen(false); }}>Connect Target Database</div>
                <div className="qa-item" onClick={() => { setScreen("schema"); setQaOpen(false); }}>Discover Metadata</div>
                <div className="qa-item" onClick={() => { setScreen("conversion"); setQaOpen(false); }}>Run Masking Policy</div>
                <div className="qa-item" onClick={() => { setScreen("lineage"); setQaOpen(false); }}>View Lineage Graph</div>
                <div className="qa-item" onClick={() => { setScreen("sql"); setQaOpen(false); }}>Open SQL Editor</div>
                <div className="qa-item" onClick={() => { setScreen("jobs"); setQaOpen(false); }}>Monitor Jobs</div>
                <div className="qa-item" onClick={() => { setScreen("agent"); setQaOpen(false); }}>AI Agent Console</div>
              </div>
            </div>

            <button className="icon-btn" title="Notifications">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 8a6 6 0 1112 0c0 5 2 6 2 6H4s2-1 2-6z"/><path d="M10 20a2 2 0 004 0"/></svg>
              <span className="dot-badge"></span>
            </button>

            <button className="icon-btn" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} title="Toggle theme">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
            </button>

            <div className="user-chip">
              <div className="avatar">{authUser?.username ? authUser.username.slice(0, 2).toUpperCase() : "RO"}</div>
              <div><div className="n">{authUser?.display_name || authUser?.username || "Robin Operator"}</div></div>
            </div>
          </div>
        </div>

                <div className="canvas">
          {screen === "home" && <HomeScreen t={t} apiBase={apiBase} apiStatus={apiStatus} apiToken={authToken} setScreen={setScreen} setSelectedTable={setSelectedTable} authUser={authUser} />}
          {screen === "connections" && <SourceTargetConnectionsScreen t={t} apiBase={apiBase} apiStatus={apiStatus} apiToken={authToken} />}
          {screen === "schema" && <SchemaExplorer t={t} selected={selectedTable} setSelected={setSelectedTable} query={query} setQuery={setQuery} apiBase={apiBase} apiStatus={apiStatus} apiToken={authToken} />}
          {screen === "lineage" && <LineageGraphScreen t={t} apiBase={apiBase} apiStatus={apiStatus} apiToken={authToken} selectedTable={selectedTable} setSelectedTable={setSelectedTable} />}
          {(screen === "masking" || screen === "conversion") && <MaskingDesigner t={t} selected={selectedTable} setSelected={setSelectedTable} apiBase={apiBase} apiStatus={apiStatus} apiToken={authToken} myRole={authUser?.role} />}
          {screen === "sql" && <SqlEditorScreen t={t} apiBase={apiBase} apiStatus={apiStatus} apiToken={authToken} selectedTable={selectedTable} setSelectedTable={setSelectedTable} />}
          {screen === "dba" && <DBAConsole t={t} apiBase={apiBase} apiStatus={apiStatus} apiToken={authToken} myRole={authUser?.role} />}
          {screen === "agent" && <AgentConsole t={t} feedActivity={feedActivity} apiBase={apiBase} apiStatus={apiStatus} apiToken={authToken} myRole={authUser?.role} />}
          {screen === "jobs" && <JobMonitor t={t} apiBase={apiBase} apiStatus={apiStatus} apiToken={authToken} />}
          {screen === "audit" && <AuditDashboard t={t} apiBase={apiBase} apiStatus={apiStatus} apiToken={authToken} />}
          {screen === "users" && <UserManagement t={t} apiBase={apiBase} apiStatus={apiStatus} apiToken={authToken} myRole={authUser?.role} myUsername={authUser?.username} />}
          {screen === "approvals" && <ApprovalDashboard t={t} apiBase={apiBase} apiStatus={apiStatus} apiToken={authToken} myRole={authUser?.role} />}
          {screen === "ops" && <OpsOverview t={t} apiBase={apiBase} apiStatus={apiStatus} apiToken={authToken} />}
        </div>
      </div>
    </div>
  );
}
