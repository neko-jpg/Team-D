# Research sources and provenance

Last reviewed: 2026-08-31.

No publicly distributed Mercari Japan Figma UI kit, React package, or design-token package was confirmed during this review. Mercari has described internal shared libraries and design-system work. Treat the implementation in this skill as research-derived, not official.

## Mercari

- [Mercari Group Brand Guidelines](https://storage.googleapis.com/prd-about-asset-2020/2023/01/c2422f3d-mercari_guidelines_230113.pdf): `official-brand`; logo and brand color rules.
- [Mercari Design Principles](https://note.com/mercari_design/n/n1002aa6aebf5): `official-principle`; Trusted, Open, Simple, Empowering, and Connecting.
- [Mercari Design System article](https://note.com/mercari_design/n/na159427a730f): `official-principle`; internal shared libraries, platform adaptation, typography, and accessibility work.
- [Mercari Sans](https://design.mercari.com/mercari-sans/): `official-brand`; tone reference only. Do not assume the font is licensed for this web product.
- [Mercari Japan web product](https://jp.mercari.com/): `observed-product`; verify current UI before refreshing observed tokens.

## iPhone interaction

- [Apple HIG: Layout](https://developer.apple.com/design/human-interface-guidelines/layout): safe areas, adaptable layout, and iPhone display features.
- [Apple HIG: Feedback](https://developer.apple.com/design/human-interface-guidelines/feedback): clear, proportional, actionable feedback.
- [Apple HIG: Progress indicators](https://developer.apple.com/design/human-interface-guidelines/progress-indicators): determinate progress and stalled-process recovery.
- [DataScannerViewController](https://developer.apple.com/documentation/visionkit/datascannerviewcontroller): native reference for live recognition, guidance, highlighting, and an overlay container. Use as an interaction reference, not a web dependency.

## Guided capture

- [Microblink BlinkID Web callbacks](https://docs.microblink.com/blinkid/sdk/web/callbacks): per-frame quality and UI-state callbacks.
- [AWS Face Liveness](https://docs.aws.amazon.com/rekognition/latest/dg/face-liveness.html): single-instruction alignment, readiness, capture, and result flow.
- [Regula Face SDK UI customization](https://docs.regulaforensics.com/develop/face-sdk/overview/ui-customization/): guide shapes, message plates, processing, retry, and success states.
- [Veriff end-user flow events](https://devdocs.veriff.com/docs/end-user-flow-events-table): assisted capture events, issue detection, issue resolution, and lack of response.
- [Apple ARCoachingOverlayView](https://developer.apple.com/documentation/arkit/arcoachingoverlayview): conversational movement coaching and temporary guidance.

Useful refresh searches:

```text
"guided capture" camera UX "real-time feedback"
"camera coaching" overlay "hold steady"
"smart capture" SDK glare blur
"assisted image capture" mobile UX
"object framing guidance" live camera UI
```

Avoid using `real-time OS` as a query because it primarily returns RTOS material. Use eKYC products as state-machine references, not as evidence of legal or identity-verification requirements.
