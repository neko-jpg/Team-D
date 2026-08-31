---
name: mercari-guided-capture-react
description: Design, build, and review Team-D's iPhone-first React garment-capture UI using Mercari research-derived principles and calm real-time guided-capture feedback. Use for camera flows, UI states, Japanese copy, components, Storybook fixtures, measurement review, and visual QA. Do not treat it as an official Mercari UI kit or as identity-verification compliance guidance.
---

# Mercari Guided Capture React

Create a camera experience that feels like a calm coach: the live image remains primary, the interface presents one useful action at a time, successful correction is acknowledged, and the user stays in control.

## Establish context

Before changing product behavior, read [product-context.md](references/product-context.md) and the current project requirements/specs it identifies. Current project artifacts override examples in this skill. Preserve unrelated worktree changes.

Use three evidence labels when describing Mercari influence:

- `official-brand`: values published in Mercari brand guidance.
- `official-principle`: experience principles published by Mercari Design.
- `observed-product`: implementation details observed in the public product; these are not a public API or official kit.

Never call the resulting components an official Mercari UI kit. Do not recreate proprietary icons, logos, or trade dress without approved assets.

## Route the task

- For visual direction, layout, color, typography, or motion, read [design-language.md](references/design-language.md).
- For capture flow, event priority, copy, measurement, failure recovery, or Storybook states, read [guided-capture-states.md](references/guided-capture-states.md).
- For React, TypeScript, Vite, Tailwind/CSS tokens, Radix boundaries, camera overlays, Canvas, OpenCV.js, or tests, read [react-implementation.md](references/react-implementation.md).
- For a design or implementation review, also read [quality-checklist.md](references/quality-checklist.md).
- When provenance or refreshing research matters, read [research-sources.md](references/research-sources.md).
- When implementing tokens, adapt [capture-theme.css](assets/capture-theme.css); do not blindly overwrite an existing theme.

## Non-negotiable interaction rules

- Keep the camera image full-bleed and visually dominant.
- Keep progress, shutter, back, help, and light controls stable; transient guidance must not shift them.
- Show one primary corrective action at a time. Express the next action, not an AI diagnosis or confidence score.
- Acknowledge improvement before returning to neutral or ready state.
- Debounce noisy observations and use expiry/sequence rules so the UI does not flicker or rewind.
- Keep the manual shutter usable even when guidance is not `READY`.
- Treat live guidance as advice. Only validated post-capture results and app-owned state may accept a slot or advance the flow.
- Never burn guides or overlays into captured source images.
- Preserve accepted images and measurement work through retries, reconnects, and provider failures.
- Keep failures recoverable and state what remains safe.
- Require explicit user approval for measurement results and for the final original/composited image.
- Preserve original garment pixels; generated imagery may replace the background, not redraw the product.
- Respect iPhone safe areas, text zoom, reduced motion, screen readers, keyboard input, and a minimum 44px target.

## Working method

1. Model the finite UI states and their priority before styling a screen.
2. Create fixture-driven stories for normal, warning, ready, processing, success, retry, reconnecting, and fallback states.
3. Build semantic tokens and owned components; avoid encoding meaning in raw color names or scattered utility values.
4. Keep native HTML controls and accessible headless primitives beneath custom visual styling.
5. Verify the complete path at an iPhone-sized viewport before adding decorative polish.
6. Review observable behavior, not only static screenshots: timing, state stability, focus, recovery, approval, and retained progress.

When the user asks only for design exploration or review, do not infer permission to implement or modify product files.
