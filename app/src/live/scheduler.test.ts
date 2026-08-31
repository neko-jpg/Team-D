import { describe, expect, it } from "vitest";
import {
  FrameScheduler,
  type FrameSchedulerPlatform,
  type VideoFrameCallback,
} from "./scheduler";

class FakeClock {
  private currentMs = 0;
  private nextId = 1;
  private readonly timers = new Map<number, { dueAt: number; callback: () => void }>();

  public readonly platform: FrameSchedulerPlatform = {
    now: () => this.currentMs,
    setTimeout: (callback, delayMs) => {
      const id = this.nextId++;
      this.timers.set(id, {
        dueAt: this.currentMs + Math.max(0, delayMs),
        callback,
      });
      return id;
    },
    clearTimeout: (handle) => {
      this.timers.delete(handle as number);
    },
  };

  public advance(milliseconds: number): void {
    if (milliseconds < 0) {
      throw new RangeError("Fake clock cannot move backwards");
    }

    const target = this.currentMs + milliseconds;
    while (true) {
      const next = [...this.timers.entries()]
        .filter(([, timer]) => timer.dueAt <= target)
        .sort(([, left], [, right]) => left.dueAt - right.dueAt)[0];
      if (!next) {
        break;
      }

      const [id, timer] = next;
      this.timers.delete(id);
      this.currentMs = timer.dueAt;
      timer.callback();
    }

    this.currentMs = target;
  }
}

async function flushMicrotasks(): Promise<void> {
  for (let index = 0; index < 6; index += 1) {
    await Promise.resolve();
  }
}

function createVideoHarness() {
  let callback: VideoFrameCallback | undefined;
  let nextHandle = 1;
  const cancelled: unknown[] = [];

  return {
    video: {
      requestVideoFrameCallback(nextCallback: VideoFrameCallback) {
        callback = nextCallback;
        return nextHandle++;
      },
      cancelVideoFrameCallback(handle: unknown) {
        cancelled.push(handle);
      },
    },
    emit(timestamp: number, metadata?: unknown) {
      const current = callback;
      expect(current).toBeDefined();
      callback = undefined;
      current?.(timestamp, metadata);
    },
    get cancelled() {
      return cancelled;
    },
  };
}

describe("FrameScheduler", () => {
  it("prefers rVFC and keeps only the latest frame while analysis is busy", async () => {
    const clock = new FakeClock();
    const harness = createVideoHarness();
    let latestFrame = 1;
    let releaseFirst!: () => void;
    const firstAnalysis = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    const received: number[] = [];
    let active = 0;
    let maxActive = 0;

    const scheduler = new FrameScheduler<number>({
      video: harness.video,
      platform: clock.platform,
      readFrame: () => latestFrame,
      onFrame: async (frame) => {
        active += 1;
        maxActive = Math.max(maxActive, active);
        received.push(frame);
        if (frame === 1) {
          await firstAnalysis;
        }
        active -= 1;
      },
    });

    scheduler.start();
    expect(scheduler.selectedSource).toBe("rvfc");

    harness.emit(clock.platform.now());
    await flushMicrotasks();
    expect(received).toEqual([1]);
    expect(scheduler.isProcessing).toBe(true);

    for (let frame = 2; frame <= 20; frame += 1) {
      latestFrame = frame;
      clock.advance(10);
      harness.emit(clock.platform.now());
    }

    expect(scheduler.pendingFrameCount).toBe(1);
    expect(received).toEqual([1]);

    releaseFirst();
    await flushMicrotasks();
    expect(received).toEqual([1]);

    clock.advance(60);
    await flushMicrotasks();
    expect(received).toEqual([1, 20]);
    expect(scheduler.pendingFrameCount).toBe(0);
    expect(maxActive).toBe(1);

    scheduler.stop();
    expect(scheduler.isRunning).toBe(false);
    expect(harness.cancelled.length).toBeGreaterThan(0);
  });

  it("falls back to rAF when rVFC is unavailable", async () => {
    const clock = new FakeClock();
    let rafCallback: ((timestamp: number) => void) | undefined;
    let cancelled = 0;
    const platform: FrameSchedulerPlatform = {
      ...clock.platform,
      requestAnimationFrame: (callback) => {
        rafCallback = callback;
        return 1;
      },
      cancelAnimationFrame: () => {
        cancelled += 1;
      },
    };
    const received: number[] = [];
    const scheduler = new FrameScheduler<number>({
      platform,
      readFrame: () => 42,
      onFrame: (frame) => {
        received.push(frame);
      },
    });

    scheduler.start();
    expect(scheduler.selectedSource).toBe("raf");
    expect(rafCallback).toBeDefined();
    rafCallback?.(clock.platform.now());
    await flushMicrotasks();
    expect(received).toEqual([42]);

    scheduler.stop();
    expect(cancelled).toBe(1);
  });

  it("falls back to a 250ms timer when rVFC and rAF are unavailable", async () => {
    const clock = new FakeClock();
    let latestFrame = "first";
    const received: string[] = [];
    const scheduler = new FrameScheduler<string>({
      clock: clock.platform,
      readFrame: () => latestFrame,
      onFrame: (frame) => {
        received.push(frame);
      },
    });

    scheduler.start();
    expect(scheduler.selectedSource).toBe("timer");
    clock.advance(249);
    await flushMicrotasks();
    expect(received).toEqual([]);

    clock.advance(1);
    await flushMicrotasks();
    expect(received).toEqual(["first"]);

    latestFrame = "second";
    clock.advance(250);
    await flushMicrotasks();
    expect(received).toEqual(["first", "second"]);
    scheduler.stop();
  });

  it("can be driven by manual ticks without browser APIs", async () => {
    const clock = new FakeClock();
    const received: number[] = [];
    const scheduler = new FrameScheduler<number>({
      mode: "manual",
      platform: clock.platform,
      onFrame: (frame) => {
        received.push(frame);
      },
    });

    scheduler.tick(1, 0);
    await flushMicrotasks();
    expect(scheduler.selectedSource).toBe("manual");
    expect(received).toEqual([1]);

    scheduler.tick(2, 100);
    await flushMicrotasks();
    expect(received).toEqual([1]);
    expect(scheduler.pendingFrameCount).toBe(1);

    clock.advance(250);
    await flushMicrotasks();
    expect(received).toEqual([1, 2]);
    scheduler.stop();
  });
});
