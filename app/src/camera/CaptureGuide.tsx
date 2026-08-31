import type { ReactElement } from "react";

import type { CaptureSlot } from "../shared";

export interface NormalizedCaptureGuide {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

/**
 * Single display/analysis geometry for the fixed guides. The normalized values
 * can also be passed to the PixelRoi conversion when local analysis is wired.
 */
export const CAPTURE_GUIDES: Record<CaptureSlot, NormalizedCaptureGuide> = {
  front: { x: 0.1, y: 0.09, width: 0.8, height: 0.8 },
  back: { x: 0.1, y: 0.09, width: 0.8, height: 0.8 },
  tag: { x: 0.2, y: 0.28, width: 0.6, height: 0.44 },
};

const GUIDE_LABELS: Record<CaptureSlot, string> = {
  front: "正面をガイドに合わせる",
  back: "背面をガイドに合わせる",
  tag: "タグを枠の中に入れる",
};

export interface CaptureGuideProps {
  readonly shot: CaptureSlot;
}

function normalizedPercent(value: number): number {
  return Number((value * 100).toFixed(4));
}

/** A decorative sibling layer over the video; it is never a capture source. */
export function CaptureGuide({ shot }: CaptureGuideProps): ReactElement {
  const isTag = shot === "tag";
  const guide = CAPTURE_GUIDES[shot];
  const transform = [
    `translate(${normalizedPercent(guide.x)} ${normalizedPercent(guide.y)})`,
    `scale(${guide.width} ${guide.height})`,
  ].join(" ");

  return (
    <div
      aria-hidden="true"
      className="capture-guide"
      data-guide-kind={isTag ? "tag" : "garment"}
      data-shot={shot}
      data-testid="capture-guide"
    >
      <svg focusable="false" preserveAspectRatio="none" viewBox="0 0 100 100">
        <g data-testid="capture-guide-geometry" transform={transform}>
          {isTag ? (
            <>
              <rect
                className="capture-guide-shape capture-guide-tag"
                height="100"
                rx="6"
                width="100"
                x="0"
                y="0"
              />
              <path
                className="capture-guide-detail"
                d="M12 23h76M12 43h58M12 63h72M12 83h45"
              />
            </>
          ) : (
            <path
              className="capture-guide-shape capture-guide-garment"
              d="M31 0 16 8 0 28l15 15 9-9v66h52V34l9 9 15-15L84 8 69 0c-3 10-10 15-19 15S34 10 31 0Z"
            />
          )}
        </g>
      </svg>
      <span className="capture-guide-label">{GUIDE_LABELS[shot]}</span>
    </div>
  );
}
