/**
 * Client-side input validation & sanitisation (Gap 5).
 *
 * Validates a `RunRequest` before it is dispatched to the network.
 * Catches missing objectives, malformed URLs, empty sources, and out-of-range
 * limits in the browser with plain human sentences, preventing unnecessary 422
 * round trips to the OneShot System.
 */

import {
  SOURCE_TYPES,
  type RunRequest,
  type SourceInput,
  type SourceOperation,
  type SourceType,
} from "./schema";

export interface ValidationResult {
  valid: boolean;
  errors: string[];
}

const VALID_OPERATIONS: ReadonlySet<SourceOperation> = new Set([
  "ADD_SOURCE",
  "INPUT_SOURCE",
  "IMPORT_SOURCE",
  "REFERENCE_SOURCE",
  "REMOVE_SOURCE",
]);

const VALID_TYPES: ReadonlySet<SourceType> = new Set(
  SOURCE_TYPES.map((entry) => entry.value),
);

/** Maximum allowed base64 length corresponding to ~25MB raw payload. */
const MAX_BASE64_LENGTH = 35 * 1024 * 1024;

/**
 * Validates an individual source payload.
 */
export function validateSource(
  source: SourceInput,
  index: number,
): string[] {
  const errors: string[] = [];
  const label = source.label ? `"${source.label}"` : `Source #${index + 1}`;

  if (!source.id || !source.id.trim()) {
    errors.push(`${label} is missing an identifier.`);
  }

  if (!VALID_TYPES.has(source.type)) {
    errors.push(`${label} has unsupported type "${source.type}".`);
  }

  if (source.operation && !VALID_OPERATIONS.has(source.operation)) {
    errors.push(`${label} has unknown operation "${source.operation}".`);
  }

  if (source.type === "public_url") {
    const rawUrl = source.url?.trim() ?? "";
    if (!rawUrl) {
      errors.push(`${label} needs a web address (URL).`);
    } else {
      try {
        const parsed = new URL(rawUrl);
        if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
          errors.push(`${label} address must start with http:// or https://.`);
        }
      } catch {
        errors.push(`${label} address is not a valid URL.`);
      }
    }
  } else if (source.type === "text" || source.type === "raw_html") {
    if (!source.text || !source.text.trim()) {
      errors.push(`${label} has no text content.`);
    }
  } else {
    // Binary or file upload type
    if (!source.content_base64 || !source.content_base64.trim()) {
      errors.push(`${label} requires uploaded file content.`);
    } else if (source.content_base64.length > MAX_BASE64_LENGTH) {
      errors.push(`${label} file exceeds the maximum allowed 25 MB limit.`);
    }
  }

  return errors;
}

/**
 * Validates a full RunRequest before sending to the OneShot System.
 */
export function validateRunRequest(request: RunRequest): ValidationResult {
  const errors: string[] = [];

  // 1. Objective / Intent
  if (!request.intent || !request.intent.trim()) {
    errors.push("Tell OneShot what you want done with these sources.");
  } else if (request.intent.trim().length < 3) {
    errors.push("The objective is too short — describe what you want done.");
  }

  // 2. Sources
  if (!Array.isArray(request.sources) || request.sources.length === 0) {
    errors.push("Add at least one source — OneShot reads only what you give it.");
  } else {
    request.sources.forEach((source, index) => {
      errors.push(...validateSource(source, index));
    });
  }

  // 3. Options
  if (request.options) {
    const { max_context_chars, max_chars_per_source } = request.options;
    if (
      max_context_chars !== undefined &&
      (!Number.isFinite(max_context_chars) || max_context_chars <= 0)
    ) {
      errors.push("Max context characters must be a positive number.");
    }
    if (
      max_chars_per_source !== undefined &&
      max_chars_per_source !== null &&
      (!Number.isFinite(max_chars_per_source) || max_chars_per_source < 200)
    ) {
      errors.push("Max characters per source must be at least 200.");
    }
  }

  return {
    valid: errors.length === 0,
    errors,
  };
}
