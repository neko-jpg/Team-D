export {
  DEFAULT_FRAME_DIFFERENCE_THRESHOLD,
  DEFAULT_STABLE_DURATION_MS,
  GRAYSCALE_MAX_VALUE,
  StabilityTracker,
  createStabilityTracker,
  normalizedFrameDifference,
} from "./stability-tracker";
export type {
  GrayRoiFrame,
  RoiFrame,
  RoiGeometry,
  StabilityResetReason,
  StabilityResult,
  StabilityTrackerOptions,
} from "./stability-tracker";
