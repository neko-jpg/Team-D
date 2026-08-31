# Product context and source of truth

## Product promise

Team-D helps people who want to sell a garment but do not know how to photograph or measure it. The experience guides a flat-laid top through front, back, tag, and measurement capture, reviews length and width, prepares a background without regenerating the garment, and asks the user to approve the final image.

The interface is a coach, not a grader. It should reduce uncertainty by making the current step, the next action, retained progress, and recovery path obvious.

## Read current project artifacts

Do not rely on this summary when the repository has changed. Resolve the repository root and read the relevant current files:

- `requirements.md`: product scope and acceptance criteria.
- `architecture.md`: implementation boundaries, privacy, OSS, and state ownership.
- `openspec/changes/build-listing-photo-assistant-mvp/specs/guided-garment-capture/spec.md`: user-visible capture and measurement requirements.
- `openspec/changes/build-listing-photo-assistant-mvp/specs/background-preserving-edit/spec.md`: editing, comparison, approval, and output requirements.
- `openspec/changes/build-listing-photo-assistant-mvp/design.md`: technical decisions and non-goals.
- `openspec/changes/build-listing-photo-assistant-mvp/tasks.md`: current implementation plan, not the authority for user-visible behavior.

If documents conflict, preserve the user's latest explicit decision and surface the conflict. User-visible behavior follows the OpenSpec specs; OSS and technical boundaries follow `architecture.md`.

## Current experience contract

- Target: one flat-laid top on an iPhone-first React mobile web experience.
- Required order: front, back, tag, measurement, measurement review, background edit, final comparison, approval, save.
- Capture guides are fixed 2D overlays, not spatial AR.
- The user can manually capture even when live guidance is not ready.
- Live semantic and local quality feedback are provisional; app-owned validation decides progress.
- Accepted slots survive retries and reconnects.
- Measurement uses a known-size marker in the same plane as the garment, proposes body-length and body-width endpoints, supports correction, and requires explicit approval.
- Measurement imagery is analysis input, not a listing image.
- Images and intermediate results are session-only unless the current spec explicitly changes that rule.
- Background generation receives text only. Garment RGB in the final composite comes from the original front image.

## Decision priority

When constraints compete, use this order:

1. The user's explicit request and current spec.
2. Accurate, recoverable capture and preservation of user work.
3. Accessibility and familiar iPhone/web interaction.
4. Mercari's Trusted, Simple, and Empowering principles.
5. Visual novelty and decorative polish.

Do not import actual eKYC language, legal claims, surveillance cues, or identity-verification semantics. eKYC products are interaction references for guided capture only.
