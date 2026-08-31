import type { SessionSlot } from "./captureTypes";

/** Finite advice codes an Agent may push during live capture. */
export const GUIDANCE_CODES = [
  "MOVE_CLOSER",
  "MOVE_FARTHER",
  "CENTER_GARMENT",
  "SHOW_FULL_GARMENT",
  "WRONG_SIDE",
  "MOVE_TO_TAG",
  "PLACE_MARKER",
  "MARKER_NOT_VISIBLE",
  "FLATTEN_GARMENT",
  "CAMERA_OVERHEAD",
  "HOLD_STEADY",
  "READY",
  "AGENT_UNAVAILABLE",
] as const;
export type GuidanceCode = (typeof GUIDANCE_CODES)[number];

/** One piece of live advice pushed by the guidance Agent for a session. */
export interface GuidanceEvent {
  sessionId: string;
  sequence: number;
  shot: SessionSlot;
  code: GuidanceCode;
  message: string;
  confidence: number;
  observedAt: number;
  expiresAt: number;
}

/** Live transport state, tracked independently of the capture step. */
export const CONNECTION_STATES = [
  "connecting",
  "connected",
  "reconnecting",
  "disconnected",
] as const;
export type ConnectionState = (typeof CONNECTION_STATES)[number];
