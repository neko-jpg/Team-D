# Guided capture quality checklist

Use this checklist for design reviews, Storybook reviews, and implementation handoff. Report only actionable findings and distinguish a spec violation from an optional improvement.

## Flow and state

- The current step and total progress are always understandable.
- Front, back, tag, measurement, review, editing, and approval have explicit entry and completion states.
- One primary instruction wins when several problems coexist.
- Short-lived observations cannot overwrite a newer step or stable state.
- Correcting a problem produces a brief affirmative response.
- Manual capture remains available outside `READY`.
- Accepted shots and measurement edits survive retry, reconnect, and provider failure.
- Busy, timeout, error, retry, disconnected/fallback, and success states are represented by fixtures.

## Camera composition

- Video is full-bleed; guides and panels do not obscure the part the user must align.
- Guide geometry matches the shot: garment silhouette/safe frame, tag rectangle, or garment plus marker placement.
- Progress and persistent controls never move when guidance changes.
- Guidance is readable on light, dark, and visually busy camera frames.
- Captured source images exclude every guide and control.

## Copy

- The primary message contains one concrete action.
- Copy avoids scores, confidence, model names, and diagnostic jargon.
- Error copy says what happened, what was retained, and what the user can do.
- Success language is positive but not celebratory enough to slow the flow.
- Japanese text survives 200% zoom and likely localization expansion.

## iPhone and accessibility

- Verify at 390px width and at least one narrower supported width.
- Dynamic Island/notch, browser chrome, home indicator, and safe-area insets do not cover critical UI.
- Interactive targets are at least 44px in both dimensions.
- Controls have accessible names and visible focus.
- Status updates are announced deliberately; frame-by-frame guidance does not flood `aria-live`.
- Meaning is not encoded by color alone.
- Reduced-motion users receive equivalent state feedback.
- Pinch zoom is not disabled globally.

## Measurement

- Preparation explains marker size, print scale, same-plane placement, garment orientation, and full-frame requirements.
- Missing, cropped, overlapping, duplicate, or distorted marker states explain a recovery action.
- Measurement endpoints have large touch targets without falsifying their precise coordinate.
- Dragging an endpoint recalculates the value and clears prior approval.
- Automatic values start unapproved; manual entry is visibly marked and also requires approval.
- Processing cannot freeze the main interface; cancellation/retry or fallback remains understandable.

## Editing and approval

- Original and composited images use equivalent framing for comparison.
- The initial preview is unapproved.
- Mask errors cannot produce an approvable composite.
- The selected result is explicit before save.
- Garment pixels, accepted source shots, and approved measurement data remain unchanged by background generation.

## Implementation evidence

- Storybook covers visual states with deterministic fixtures.
- Reducer tests cover valid and invalid transitions.
- Coordinate-transform tests cover object-fit cropping, orientation, Canvas, and measurement endpoint mapping.
- Browser testing covers camera permission, upload fallback, reconnect, text zoom, touch, and safe areas.
- Visual review includes at least one real garment image, not only clean mock imagery.
