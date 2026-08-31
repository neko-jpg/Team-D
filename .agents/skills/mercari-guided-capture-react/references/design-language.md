# Design language: Calm Guided Capture

Use this reference when designing or reviewing the capture experience. The goal is not an eKYC look; it is a camera that understands the scene and quietly returns the single most useful next action.

> AIが評価するカメラではなく、ユーザーの動きに合わせて、次の一手だけをやさしく返すカメラ。

## Evidence and naming

This project does **not** use a publicly verified Mercari UI Kit. Do not call generated components “Mercari UI Kit” or imply official endorsement. Label design decisions by evidence level:

- `official-brand`: values explicitly published in Mercari brand guidelines, such as the core brand red and approved logo treatment. Use only for the documented role; a brand color does not establish component behavior.
- `official-principle`: Mercari's published design principles. Apply **Trusted**, **Simple**, and **Empowering** most strongly here; Open and Connecting remain supporting principles.
- `observed-product`: patterns inferred from the current Mercari product or public screenshots, such as approximate UI colors, density, radii, and control sizing. Treat these as revisable project defaults, not official tokens.

When sources conflict, prioritize: capture clarity and safe recovery, then iPhone/Web usability, then Mercari resemblance. Never weaken legibility, control access, or user agency to look more branded.

## Experience character

- **Trusted:** explain what is happening, preserve captured work, and distinguish guidance from blocking errors. Do not expose opaque AI scores or confidence percentages.
- **Simple:** keep one task per screen and one primary instruction at a time. Progressive disclosure beats simultaneous hints.
- **Empowering:** let the user take the photo before the system says “ready,” retake it, use a documented fallback when automatic measurement fails, and explicitly approve generated or measured results.
- **Calm:** respond when state meaningfully changes; do not narrate every frame. Confirm improvements briefly so the user knows their action worked.
- **Camera-first:** the live image is the working surface. UI should frame the subject, not cover it.

The emotional model is a knowledgeable person coaching from beside the user—not an examiner, scanner, security checkpoint, or futuristic AI console.

## Five-layer camera composition

Keep these layers stable across front, back, tag, and measurement capture:

1. **Live camera:** full-bleed and visually dominant. Avoid decorative blur, tint, or dimming over the subject.
2. **Capture guide:** a thin, low-obstruction outline that changes shape by task. Use it to communicate placement or crop, not system status.
3. **Primary coaching:** one short action near the relevant area, while remaining readable against changing imagery. Replace the message only after a stable state change.
4. **Progress:** always show the current step and total, such as `正面 1/4` and `採寸 4/4`. Do not silently omit a required step.
5. **Controls:** keep back, light, gallery/retake, and shutter positions predictable. Guidance may change; controls must not move with it.

If space is tight, remove secondary confirmations before shrinking or moving the primary instruction and controls.

## Feedback rhythm

Design the UI around this state rhythm:

`searching → issue detected → one corrective action → improvement confirmed → hold steady → ready → capture → checking → success / retake`

- Choose the highest-priority actionable issue when several are present. Typical priority: subject missing or cropped, unsafe/unsupported angle, distance, lighting, stability, minor composition.
- Use hysteresis or a short stable interval before changing instructions so borderline signals do not flicker.
- Keep the manual shutter available unless capture is technically impossible. `ready` is reassurance, not permission.
- Show a short positive acknowledgement such as `明るさOK` after an issue clears; then let it recede.
- During checking, say what the system is doing without pretending certainty: `撮影した写真を確認しています`.
- For recoverable failure, retain the photo and offer the next action. Agent or network loss must not erase local progress.

## Visual foundation

### Color roles

Use semantic roles in implementation; do not scatter raw color values through components.

| Role | Starting point | Evidence / use |
|---|---:|---|
| Brand core | `#FF0211` | `official-brand`; brand expression and approved logo contexts |
| Primary action | `#FF333F` | `observed-product`; primary CTA and active action, pending verification |
| Text strong | `#333333` | `observed-product`; light surfaces |
| Text muted | `#666666` | `observed-product`; secondary information |
| Surface | `#FFFFFF` | base light surface |
| Surface subtle | `#F5F5F5` | `observed-product`; grouped content and review screens |
| On-camera text | `#FFFFFF` | camera overlays; use scrims/shadows to maintain contrast |
| Success / ready | `#0AA466` | project default; only for confirmed improvement, ready, and success |

Use translucent charcoal surfaces behind camera text instead of heavy opaque cards. Reserve red for brand/primary action and true failure; routine coaching is neutral or informational. Never communicate state by color alone.

### Type

- Use the system stack (`-apple-system`, `BlinkMacSystemFont`, then suitable fallbacks) to feel native on iPhone without imitating native chrome.
- Default body and actionable guidance to 15–17 CSS px with comfortable line height. Do not reduce critical overlay text to fit more words; shorten the copy.
- Use medium or semibold weight for the one primary instruction and progress label. Avoid large promotional headlines inside the camera.
- Keep Japanese line breaks intentional and messages to one sentence where possible.

### Space, shape, and touch

- Design from a 390 CSS px portrait viewport, then verify narrower and wider iPhones.
- Use a 4 px spacing rhythm; prefer 16 px page edges, 12–16 px overlay padding, and at least 8 px between related controls.
- Use 12–16 px radii for floating information panels. Do not round every camera element into a pill.
- Maintain at least a 44 × 44 CSS px touch target; primary controls generally sit at 48–52 px or larger.
- Respect `env(safe-area-inset-*)` on every edge. Full-bleed video may extend behind the safe area; text and controls may not.
- Use dynamic viewport units with a fallback so Safari browser chrome does not hide bottom controls.

### Motion

- Use restrained 200–300 ms fades or small transforms for meaningful state transitions.
- Let the guide or shutter ring settle into ready state; avoid continuous pulsing, scanning lines, particle effects, or neon glows.
- Do not animate on every inference frame. Motion should explain a state change, not advertise that AI is running.
- Honor `prefers-reduced-motion`. Never rely on motion or haptics as the only confirmation; web haptics may be unavailable.

## Content rules

Write the next physical action, not the model's diagnosis:

| Avoid | Prefer |
|---|---|
| `距離不足です` | `少し離してください` |
| `被写体が欠損しています` | `袖と裾まで画面に入れてください` |
| `角度が不正です` | `カメラを真上に向けてください` |
| `安定性が不足しています` | `そのまま止めてください` |
| `品質基準を満たしました` | `きれいに撮れそうです` |
| `マーカー検出失敗` | `正方形マーカー全体を画面に入れてください` |

Prefer one sentence, one action, and polite plain Japanese. Avoid blame, grading language, unexplained technical terms, and promises the model cannot guarantee. If the user can continue, say so explicitly: `接続が切れました。撮影は続けられます`.

## Measurement-specific expression

Measurement is a required fourth capture step in the current product contract. Preserve the same calm coaching model:

- Explain the 50mm square reference marker and 100% print-size check before opening the camera.
- Guide one correction at a time: include the whole garment, include all four marker corners, move overhead, then hold steady.
- After calculation, show measurement points and values as editable proposals, not facts. Require explicit confirmation before saving.
- If automatic analysis fails, retain the measurement photo and offer retake or manual input; do not fabricate a result or silently skip the step.

## Do / don't

Do:

- Keep the subject visible and controls spatially stable.
- Confirm resolved issues briefly.
- Preserve local photos and progress through recoverable failures.
- Give retake, documented fallback, and explicit approval paths.
- Test every meaningful state at camera-like contrast extremes and with long Japanese text.

Don't:

- Show AI scores, bounding-box debug data, or confidence percentages.
- Present several corrective instructions at once.
- Flicker copy as raw detections cross thresholds.
- Disable the shutter only because the readiness heuristic is false.
- Interrupt ordinary corrections with modal dialogs.
- Use surveillance, identity-check, scanning, or sci-fi visual language.
- Auto-accept a generated background or measured value.

## Review gate

A design is ready to implement only when all answers are yes:

- Is the current step understandable within a glance?
- Is exactly one next action visually dominant?
- Can the user still see the garment area needed to perform that action?
- Are shutter and recovery actions available and stationary?
- Does every non-success state explain how to continue?
- Are success, warning, and failure distinguishable without color alone?
- Does the layout respect iPhone safe areas and 44 px touch targets?
- Are measurement proposals and AI-produced results clearly user-controlled and explicitly approved?
- Can each UI claim be identified as `official-brand`, `official-principle`, or `observed-product` rather than presented as an official UI kit?
