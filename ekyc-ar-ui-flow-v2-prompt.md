# eKYC / AR UI Flow V2 Edit Prompt

生成方法: Codex built-in `image_gen`（`ekyc-ar-ui-flow.png`を編集）

```text
Use case: precise-object-edit
Asset type: revised high-fidelity UX flow board for the Mercari AI Agent Hackathon
Primary request: Edit the supplied six-phone UI storyboard to make the entire capture journey feel continuously AR-assisted, visibly show real camera-angle changes between steps, keep progress inside the live camera UI, and let the user choose the replacement background.

Preserve exactly: the 16:9 warm off-white presentation board, six equally sized black smartphones in one left-to-right row, refined Japanese iOS visual quality, overall spacing, subtle arrows, coral + mint + charcoal palette, and the same sage-green cardigan as the identical physical product throughout. Keep all phones front-facing and undistorted.

Revise the step titles above the phones to:
“01 AR準備”
“02 正面撮影”
“03 撮影ガイド”
“04 背面撮影”
“05 採寸（任意）”
“06 背景設定”

Screen 01 — make it AR, not a static onboarding page:
Show a live wide-angle camera preview of a real room floor from a standing handheld viewpoint. Overlay a subtle perspective AR plane grid and a translucent garment-shaped placement silhouette on the floor. Add edge brackets and a lighting scan. Show “撮影場所を確認” at the top, a mint status chip “この場所で撮影できます”, and bottom instruction “服を平らに置いてください”. It should look like the agent is scanning and approving the shooting surface before the garment is placed.

Screen 02 — top-down front shot:
Keep a live camera UI, but make the viewpoint clearly near-perfect overhead and closer than screen 01. Show the cardigan front-side up inside a translucent contour guide, corner brackets, level indicator, chips “明るさ OK” and “全体が見えています”, and a shutter button. Main instruction “枠に合わせてください”.

Screen 03 — visibly different angle and live correction:
Do not reuse the exact same flat camera image as screen 02. Show the same cardigan from a noticeably oblique handheld angle, with perspective distortion because the phone is too tilted and slightly too close. Use an AR horizon/tilt guide, curved directional arrow, and ghost target outline indicating the desired top-down angle. Show supportive glass-panel feedback “カメラを真上に” and “少し離れてください”. This screen must clearly communicate that the user physically changes phone angle and distance.

Screen 04 — remain in AR live camera and show progress:
Replace the current white confirmation/result card completely. Show a live camera view with the same cardigan now physically flipped to its back side and seen from a slightly wider overhead angle than screen 02. Keep camera controls and AR contour guidance visible. At the top, overlay an elegant three-step progress strip: “正面 ✓  背面 2/3  タグ”. Highlight “背面 2/3” as the current step. Show a mint chip “正面を確認しました” and main instruction “次は背面を撮影”. Keep a shutter button. The user should understand progress without leaving the AR camera.

Screen 05 — optional measurement from another framing:
Show a tighter, slightly diagonal top-down camera framing of the cardigan. Use AR landmark dots, thin adjustable measurement lines, and drag handles for “肩幅” and “身幅”. Show “測定位置を確認してください”. Keep the “任意” label clear. The change in framing should feel like the camera has moved closer for measurement.

Screen 06 — user-selectable background:
Keep a large realistic preview of the unchanged cardigan cutout composited on a selected clean background. Preserve a small original/edited comparison affordance, but add a clearly interactive bottom editor. Title “背景を選ぶ”. Include a horizontal carousel of four large visual background thumbnails with short labels: “白”, “木目”, “グレー”, “＋ 追加”. Show one thumbnail with a coral selected border. Include primary button “この背景を使う” and secondary “元画像を見る”. Make it obvious the user can set or add their own background rather than receiving one automatic result.

Camera-angle continuity: screen 01 wide standing view of the empty floor; screen 02 close true overhead front view; screen 03 tilted oblique correction view; screen 04 wider overhead back view; screen 05 tighter diagonal measurement view; screen 06 editor preview. These viewpoints must be visibly distinct while the physical cardigan stays consistent.

Text must be accurate Japanese and legible. Keep text minimal and only use the specified phrases.
Constraints: this is a realistic React mobile-web camera flow, not futuristic magic. Strong eKYC language: AR framing, live guidance, progress, verification, user control. Product itself must never be regenerated or changed; background only can change at step 06.
Avoid: static onboarding illustration in screen 01, white result page in screen 04, six repeated identical camera angles, identical duplicated front images, virtual try-on, generated people, price suggestions, X branding, Mercari logo, neon sci-fi effects, clutter, warped phones, random English, watermarks.
```

## 最終テキスト修正

```text
In the step 05 heading above the fifth smartphone, replace the existing heading with the exact Japanese text “05 サイズ計測（任意）”. Change only this one heading and preserve every other visual element.
```
