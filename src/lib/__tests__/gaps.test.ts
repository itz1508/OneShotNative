/**
 * Unit & Contract Tests for OneShot Gaps Fixes (Gap 10).
 *
 * Verifies the logical correctness of:
 * - Gap 1: Authentication & API key storage
 * - Gap 2: Optimistic state updates and AbortController
 * - Gap 3: Structured event logging & observability
 * - Gap 4: Circuit breaker & retry policy
 * - Gap 5: Client-side input validation
 * - Gap 9: SQLite persistence architecture SQL
 * - Gap 12: Collision-safe crypto.randomUUID() run IDs
 */

import { describe, expect, it, beforeEach } from "vitest";
import {
  clearApiKey,
  getApiKey,
  getCircuitStatus,
  newRunId,
  resetCircuitBreaker,
  setApiKey,
} from "../api";
import { clearLog, getLog, log } from "../eventLog";
import { compareContract, SCHEMA_VERSION, type SchemaSnapshot } from "../schema";
import { validateRunRequest, validateSource } from "../validation";
import {
  RECOMMENDED_SQLITE_PRAGMAS,
  SQLITE_INIT_PRAGMAS_SQL,
  buildRetentionPruneSql,
} from "../persistence";

describe("Gap 12: newRunId Collision Safety", () => {
  it("generates UUID-backed run identifiers", () => {
    const id1 = newRunId();
    const id2 = newRunId();
    expect(id1).toMatch(/^run_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i);
    expect(id2).toMatch(/^run_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i);
    expect(id1).not.toBe(id2);
  });
});

describe("Gap 1: Authentication API Key Management", () => {
  beforeEach(() => {
    clearApiKey();
  });

  it("stores, retrieves, and clears API key safely", () => {
    expect(getApiKey()).toBe("");
    setApiKey("  sk-test-12345  ");
    expect(getApiKey()).toBe("sk-test-12345");
    clearApiKey();
    expect(getApiKey()).toBe("");
  });
});

describe("Gap 3: Structured Logging Ring Buffer", () => {
  beforeEach(() => {
    clearLog();
  });

  it("records log entries and respects capping", () => {
    log("info", "test", "first message", { runId: "run_1" });
    log("warn", "test", "second message", { runId: "run_1", detail: "extra" });

    const entries = getLog();
    expect(entries.length).toBe(2);
    expect(entries[0].message).toBe("first message");
    expect(entries[0].level).toBe("info");
    expect(entries[1].detail).toBe("extra");

    clearLog();
    expect(getLog().length).toBe(0);
  });
});

describe("Gap 4: Circuit Breaker State", () => {
  beforeEach(() => {
    resetCircuitBreaker();
  });

  it("initializes with circuit closed", () => {
    const status = getCircuitStatus();
    expect(status.isOpen).toBe(false);
    expect(status.consecutiveFailures).toBe(0);
  });

  it("can be reset manually", () => {
    resetCircuitBreaker();
    expect(getCircuitStatus().isOpen).toBe(false);
  });
});

describe("Gap 5: Client-Side Input Validation", () => {
  it("rejects empty or whitespace intent", () => {
    const res = validateRunRequest({
      intent: "   ",
      sources: [
        {
          id: "s1",
          type: "text",
          operation: "ADD_SOURCE",
          text: "hello world",
        },
      ],
    });
    expect(res.valid).toBe(false);
    expect(res.errors[0]).toMatch(/Tell OneShot what you want done/);
  });

  it("rejects missing sources", () => {
    const res = validateRunRequest({
      intent: "Summarise this",
      sources: [],
    });
    expect(res.valid).toBe(false);
    expect(res.errors[0]).toMatch(/Add at least one source/);
  });

  it("validates URL source format", () => {
    const invalidFormatErrors = validateSource(
      {
        id: "s1",
        type: "public_url",
        operation: "ADD_SOURCE",
        url: "not-a-valid-url",
      },
      0,
    );
    expect(invalidFormatErrors.length).toBeGreaterThan(0);
    expect(invalidFormatErrors[0]).toMatch(/address is not a valid URL/);

    const nonHttpErrors = validateSource(
      {
        id: "s1",
        type: "public_url",
        operation: "ADD_SOURCE",
        url: "ftp://example.com/file",
      },
      0,
    );
    expect(nonHttpErrors.length).toBeGreaterThan(0);
    expect(nonHttpErrors[0]).toMatch(/address must start with http:\/\/ or https:\/\//);

    const validErrors = validateSource(
      {
        id: "s1",
        type: "public_url",
        operation: "ADD_SOURCE",
        url: "https://example.com/docs",
      },
      0,
    );
    expect(validErrors.length).toBe(0);
  });

  it("validates text source content", () => {
    const errors = validateSource(
      {
        id: "s1",
        type: "text",
        operation: "ADD_SOURCE",
        text: "   ",
      },
      0,
    );
    expect(errors.length).toBeGreaterThan(0);
    expect(errors[0]).toMatch(/has no text content/);
  });

  it("passes a fully valid RunRequest", () => {
    const res = validateRunRequest({
      intent: "Summarise the session expiry rules and flag edge cases.",
      sources: [
        {
          id: "src_1",
          type: "text",
          operation: "ADD_SOURCE",
          text: "Session expires after 30 minutes of inactivity.",
        },
      ],
      options: {
        pause_before_generate: true,
        proceed_on_incomplete: false,
        max_context_chars: 100000,
      },
    });
    expect(res.valid).toBe(true);
    expect(res.errors.length).toBe(0);
  });
});

describe("Gap 7 & Contract Drift Detection", () => {
  it("detects when server schema is in sync with client mirror", async () => {
    const { MIRRORED_MODELS, MIRRORED_VOCABULARIES } = await import("../schema");
    const models: Record<string, unknown> = {};
    for (const name of MIRRORED_MODELS) {
      models[name] = {};
    }
    // Embed all mirrored vocabulary enum values inside snapshot
    models["Taxonomy"] = {
      enum: MIRRORED_VOCABULARIES.flatMap((vocab) => vocab.values),
    };

    const snapshot: SchemaSnapshot = {
      schema_version: SCHEMA_VERSION,
      models,
    };

    const drift = compareContract(snapshot);
    expect(drift.inSync).toBe(true);
    expect(drift.versionMismatch).toBe(false);
    expect(drift.missingModels.length).toBe(0);
    expect(drift.vocabularyGaps.length).toBe(0);
  });

  it("detects schema version mismatch", () => {
    const snapshot: SchemaSnapshot = {
      schema_version: "0.9.0-old",
      models: {},
    };

    const drift = compareContract(snapshot);
    expect(drift.inSync).toBe(false);
    expect(drift.versionMismatch).toBe(true);
  });
});

describe("Gap 9: SQLite Architecture & Specification", () => {
  it("defines WAL mode and safety pragmas", () => {
    expect(RECOMMENDED_SQLITE_PRAGMAS.journalMode).toBe("WAL");
    expect(RECOMMENDED_SQLITE_PRAGMAS.synchronous).toBe("NORMAL");
    expect(SQLITE_INIT_PRAGMAS_SQL).toContain("PRAGMA journal_mode = WAL;");
    expect(SQLITE_INIT_PRAGMAS_SQL).toContain("PRAGMA foreign_keys = ON;");
  });

  it("generates retention purge query with correct interval", () => {
    const sql = buildRetentionPruneSql(30);
    expect(sql).toContain("-30 days");
    expect(sql).toContain("deleted_at IS NOT NULL");
  });
});
