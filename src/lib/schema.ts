/**
 * The client's mirror of the OneShot System contract.
 *
 * Typing only — this file contains no pipeline, inspection, generation or
 * classification logic. The System owns all of that; the client presents and
 * transports. Shapes mirror `workspace/OneShot/systems/schemas.py`.
 *
 * `SCHEMA_VERSION` mirrors the System's, so drift is detectable rather than
 * silent: the header compares the two at runtime.
 */

export const SCHEMA_VERSION = "1.1";

export type SourceType =
  | "public_url"
  | "text"
  | "raw_html"
  | "pdf"
  | "docx"
  | "audio";

/** `DELETE_SOURCE` is deliberately not a scope operation — it is refused on the wire. */
export type SourceOperation =
  | "INPUT_SOURCE"
  | "ADD_SOURCE"
  | "IMPORT_SOURCE"
  | "REFERENCE_SOURCE"
  | "REMOVE_SOURCE";

export type Stage =
  | "Scan"
  | "Read"
  | "Prepare"
  | "Quality & Coverage Check"
  | "Generate"
  | "Output";

export type TraceStatus = "started" | "completed" | "failed" | "skipped" | "info";

export type WorkflowState =
  | "Starting"
  | "Process Display"
  | "Pausing"
  | "Paused"
  | "Editor Review"
  | "Finalized";

export type WorkflowCommand = "pause" | "resume" | "review" | "finalize";

export type TaskKind =
  | "summarise"
  | "explain_source"
  | "evaluate_requirements"
  | "generate"
  | "unclassified";

export type QualityOutcome = "Sufficient" | "Incomplete" | "Unusable";

/** The full taxonomy; v1 selects `Text` and `Mixed` only. */
export type InspectionMode =
  | "Text"
  | "Visual"
  | "Files and Folders"
  | "Mixed"
  | "Research When Needed";

export type LimitationSeverity = "info" | "warning" | "error";

export type ScanOutcome = "accessible" | "unsupported" | "unreadable" | "excluded";

export interface Limitation {
  code: string;
  message: string;
  severity: LimitationSeverity;
  source_id: string | null;
}

export interface SourceInput {
  id: string;
  type: SourceType;
  label?: string;
  url?: string | null;
  text?: string | null;
  filename?: string | null;
  mime_type?: string | null;
  content_base64?: string | null;
  operation?: SourceOperation;
}

export interface RunOptions {
  pause_before_generate?: boolean;
  max_context_chars?: number;
  max_chars_per_source?: number | null;
  /**
   * Gap 7 — HITL for partial context.
   *
   * When false (default), an `Incomplete` Quality & Coverage Check outcome
   * stops the run and surfaces the known gaps in EditorReview so the user can
   * decide before generation proceeds.
   *
   * When true, the System proceeds to Generate even with partial context. The
   * limitations are still reported in the output — generation just isn't blocked.
   */
  proceed_on_incomplete?: boolean;
}

export interface RunRequest {
  intent: string;
  sources: SourceInput[];
  task_kind?: TaskKind;
  options?: RunOptions;
  /** Supplied by the client so the live channel can be opened before the run starts. */
  run_id?: string;
}

export interface SourceProvenance {
  source_id: string;
  label: string;
  type: SourceType;
  mode: InspectionMode;
  outcome: ScanOutcome;
  /** Self-reported and unverified (ADR-001 D3). */
  reported_content_sha256: string;
  reported_source_version: string;
  provenance_status: "reported_unverified";
}

export interface ScanObservation {
  source_id: string;
  type: SourceType;
  label: string;
  outcome: ScanOutcome;
  reason: string;
  detail: string;
  size_bytes: number | null;
  limitation: Limitation | null;
}

export interface ReadSegment {
  source_id: string;
  mode: InspectionMode;
  text: string;
  char_count: number;
  truncated: boolean;
  dropped_chars: number;
  locator: string;
  reported_content_sha256: string;
  reported_source_version: string;
}

export interface SupportedTask {
  supported: boolean;
  statement: string;
  task_type: TaskKind;
}

export interface ContextBlock {
  source_id: string;
  source_label: string;
  mode: InspectionMode;
  text: string;
  char_count: number;
  locator: string;
  trust: "untrusted_source_content";
}

export interface ContextDecision {
  source_id: string;
  mode: InspectionMode;
  included: boolean;
  reason: string;
  chars_included: number;
  chars_dropped: number;
  locator: string;
  note: string;
}

export interface PreparedInput {
  intent: string;
  task_kind: TaskKind;
  supported_task: SupportedTask;
  instructions: string[];
  context_blocks: ContextBlock[];
  decisions: ContextDecision[];
  scope: string[];
  constraints: string[];
  requirements: string[];
  context_chars: number;
  context_available: boolean;
}

export interface CoverageDimension {
  name: string;
  covered: boolean;
  detail: string;
}

export interface QualityCheck {
  outcome: QualityOutcome;
  reasons: string[];
  limitations: Limitation[];
  coverage: CoverageDimension[];
}

export interface Citation {
  source_id: string;
  source_label: string;
  locator: string;
  reported_content_sha256: string;
  reported_source_version: string;
}

export interface GeneratedSection {
  heading: string;
  body: string;
  citations: Citation[];
}

export interface GeneratedResult {
  title: string;
  summary: string;
  sections: GeneratedSection[];
  citations: Citation[];
  model: string | null;
}

export interface ProcessTraceEvent {
  index: number;
  stage: Stage | null;
  status: TraceStatus;
  message: string;
  detail: string;
  source_id: string | null;
  at_ms: number;
}

export interface OneShotOutput {
  schema_version: string;
  run_id: string;
  state: WorkflowState;
  quality: QualityCheck;
  supported_task: SupportedTask;
  sources: SourceProvenance[];
  prepared: PreparedInput | null;
  context_decisions: ContextDecision[];
  generated: GeneratedResult | null;
  trace: ProcessTraceEvent[];
  limitations: Limitation[];
}

export interface ProviderStatus {
  provider: string | null;
  model: string | null;
  configured: boolean;
  supported: string[];
  key_variable?: string | null;
}

export interface TranscriberStatus {
  provider: string | null;
  configured: boolean;
  supported: string[];
}

export interface Health {
  status: string;
  service: string;
  schema_version: string;
  stateless: boolean;
  generate: ProviderStatus;
  transcribe: TranscriberStatus;
  inspection_modes_v1: string[];
  workflow_states: string[];
}

export type StreamEvent =
  | { type: "hello"; run_id: string; schema_version: string; state: WorkflowState; at_ms: number }
  | { type: "state"; state: WorkflowState; at_ms: number }
  | { type: "trace"; event: ProcessTraceEvent }
  | { type: "output"; output: OneShotOutput; at_ms: number }
  | { type: "failed"; message: string; at_ms: number }
  | { type: "heartbeat"; at_ms: number }
  | { type: "end"; run_id: string; state: WorkflowState; at_ms: number };

export const WORKFLOW_STATES: WorkflowState[] = [
  "Starting",
  "Process Display",
  "Pausing",
  "Paused",
  "Editor Review",
  "Finalized",
];

export const TASK_KINDS: { value: TaskKind; label: string }[] = [
  { value: "unclassified", label: "Let the System decide" },
  { value: "summarise", label: "Summarise" },
  { value: "explain_source", label: "Explain a source" },
  { value: "evaluate_requirements", label: "Evaluate requirements" },
  { value: "generate", label: "Generate an output" },
];

export const SOURCE_TYPES: {
  value: SourceType;
  label: string;
  hint: string;
  accepts?: string;
}[] = [
  { value: "public_url", label: "Public URL", hint: "OneShot fetches it and reads the page." },
  { value: "text", label: "Pasted text", hint: "Sent exactly as you paste it." },
  { value: "raw_html", label: "Raw HTML", hint: "Markup is stripped to readable text." },
  { value: "pdf", label: "PDF upload", hint: "Text layer only — scans need Visual Inspection, which v1 defers.", accepts: ".pdf,application/pdf" },
  { value: "docx", label: "DOCX upload", hint: "Word .docx body text. Legacy .doc is not read.", accepts: ".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document" },
  { value: "audio", label: "Audio upload", hint: "Transcribed by the System, if a transcription provider is configured.", accepts: "audio/*" },
];

/* ---------------------------------------------------------------------------- *
 * The contract, checked against the System (GET /schema)
 *
 * The types above are a mirror. A mirror that is never compared drifts in
 * silence, so the shapes the client actually renders from are listed here and
 * checked against the snapshot the System publishes.
 * ---------------------------------------------------------------------------- */

/** Models the client mirrors; the System publishes all of these from `GET /schema`. */
export const MIRRORED_MODELS: readonly string[] = [
  "SourceInput",
  "RunOptions",
  "RunRequest",
  "SourceProvenance",
  "ScanObservation",
  "ScanResult",
  "ReadSegment",
  "ReadResult",
  "SupportedTask",
  "ContextBlock",
  "ContextDecision",
  "PreparedInput",
  "CoverageDimension",
  "QualityCheck",
  "Citation",
  "GeneratedSection",
  "GeneratedResult",
  "ProcessTraceEvent",
  "OneShotOutput",
];

/**
 * Every vocabulary value the UI can render, grouped so a gap can be reported in
 * words. A value the System no longer sends is a value the UI can no longer
 * truthfully display.
 *
 * `WorkflowCommand` (pause / resume / review / finalize) is deliberately absent:
 * it travels on `POST /runs/{run_id}/command` and is not part of the snapshot the
 * System publishes, so there is nothing to compare it against here.
 */
export const MIRRORED_VOCABULARIES: readonly { name: string; values: readonly string[] }[] = [
  { name: "source types", values: SOURCE_TYPES.map((entry) => entry.value) },
  {
    name: "scope operations",
    values: ["INPUT_SOURCE", "ADD_SOURCE", "IMPORT_SOURCE", "REFERENCE_SOURCE", "REMOVE_SOURCE"],
  },
  { name: "workflow states", values: WORKFLOW_STATES },
  { name: "task kinds", values: TASK_KINDS.map((entry) => entry.value) },
  { name: "scan outcomes", values: ["accessible", "unsupported", "unreadable", "excluded"] },
  { name: "limitation severities", values: ["info", "warning", "error"] },
  {
    name: "stages",
    values: ["Scan", "Read", "Prepare", "Quality & Coverage Check", "Generate", "Output"],
  },
  { name: "trace statuses", values: ["started", "completed", "failed", "skipped", "info"] },
  { name: "quality outcomes", values: ["Sufficient", "Incomplete", "Unusable"] },
  {
    name: "inspection modes",
    values: ["Text", "Visual", "Files and Folders", "Mixed", "Research When Needed"],
  },
];

/** `GET /schema` — the System's Pydantic snapshot. */
export interface SchemaSnapshot {
  schema_version: string;
  models: Record<string, unknown>;
}

export interface VocabularyGap {
  name: string;
  values: string[];
}

export interface ContractDrift {
  inSync: boolean;
  versionMismatch: boolean;
  serverVersion: string;
  clientVersion: string;
  missingModels: string[];
  vocabularyGaps: VocabularyGap[];
  /** How many vocabulary values the System published in total. */
  publishedValues: number;
}

/** Every string that appears in an `enum` anywhere in the snapshot. */
function collectEnumValues(node: unknown, into: Set<string>): void {
  if (Array.isArray(node)) {
    for (const child of node) collectEnumValues(child, into);
    return;
  }
  if (!node || typeof node !== "object") return;
  for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
    if (key === "enum" && Array.isArray(value)) {
      for (const item of value) {
        if (typeof item === "string") into.add(item);
      }
      continue;
    }
    collectEnumValues(value, into);
  }
}

/**
 * Compare the System's published contract against this client's mirror.
 *
 * Three independent signals, because the version can agree while the contents
 * do not: the version string, the model names, and every vocabulary value the
 * UI renders from.
 */
export function compareContract(snapshot: SchemaSnapshot): ContractDrift {
  const models = snapshot.models ?? {};
  const published = new Set<string>();
  collectEnumValues(models, published);

  const missingModels = MIRRORED_MODELS.filter((name) => !(name in models));
  const vocabularyGaps = MIRRORED_VOCABULARIES.map(({ name, values }) => ({
    name,
    values: values.filter((value) => !published.has(value)),
  })).filter((gap) => gap.values.length > 0);

  const serverVersion = snapshot.schema_version || "unknown";
  const versionMismatch = serverVersion !== SCHEMA_VERSION;

  return {
    inSync: !versionMismatch && missingModels.length === 0 && vocabularyGaps.length === 0,
    versionMismatch,
    serverVersion,
    clientVersion: SCHEMA_VERSION,
    missingModels,
    vocabularyGaps,
    publishedValues: published.size,
  };
}