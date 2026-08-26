import { describe, it, expect, beforeEach, vi } from "vitest";
import { HistoryReporter } from "./historyReporter";
import * as historyApi from "@/api/history";

vi.mock("@/api/history", () => ({
  addHistory: vi.fn(() => Promise.resolve()),
}));

const addHistory = vi.mocked(historyApi.addHistory);

describe("HistoryReporter", () => {
  let reporter: HistoryReporter;

  beforeEach(() => {
    reporter = new HistoryReporter();
    addHistory.mockReset();
  });

  it("reports after 30 seconds of continuous playback", () => {
    reporter.load("track-1");
    reporter.setDuration(120);

    for (let i = 0; i <= 120; i++) {
      reporter.onTimeUpdate(i * 0.25, 120);
    }

    expect(addHistory).toHaveBeenCalledTimes(1);
    expect(addHistory).toHaveBeenCalledWith("track-1");
  });

  it("reports at the 50% duration mark", () => {
    reporter.load("track-2");
    reporter.setDuration(10);

    reporter.onTimeUpdate(0, 10);
    reporter.onTimeUpdate(5.1, 10);

    expect(addHistory).toHaveBeenCalledTimes(1);
    expect(addHistory).toHaveBeenCalledWith("track-2");
  });

  it("reports only once per track instance", () => {
    reporter.load("track-3");
    reporter.setDuration(20);

    reporter.onTimeUpdate(0, 20);
    reporter.onTimeUpdate(12, 20);

    expect(addHistory).toHaveBeenCalledTimes(1);

    reporter.onTimeUpdate(18, 20);
    reporter.onTimeUpdate(30, 20);

    expect(addHistory).toHaveBeenCalledTimes(1);
  });

  it("does not count large forward jumps as elapsed time", () => {
    reporter.load("track-4");
    reporter.setDuration(120);

    reporter.onTimeUpdate(0, 120);
    reporter.onTimeUpdate(50, 120);
    reporter.onTimeUpdate(50.25, 120);

    expect(addHistory).not.toHaveBeenCalled();
    reporter.onTimeUpdate(55, 120);
    expect(addHistory).not.toHaveBeenCalled();
  });

  it("resets when a new track is loaded", () => {
    reporter.load("track-5");
    reporter.setDuration(10);

    reporter.onTimeUpdate(0, 10);
    reporter.onTimeUpdate(5.1, 10);

    expect(addHistory).toHaveBeenCalledTimes(1);

    reporter.load("track-6");
    reporter.setDuration(10);
    addHistory.mockClear();

    reporter.onTimeUpdate(0, 10);
    expect(addHistory).not.toHaveBeenCalled();

    reporter.onTimeUpdate(5.1, 10);
    expect(addHistory).toHaveBeenCalledTimes(1);
    expect(addHistory).toHaveBeenCalledWith("track-6");
  });

  it("does not report when duration is unknown", () => {
    reporter.load("track-7");

    for (let i = 0; i < 100; i++) {
      reporter.onTimeUpdate(i * 0.2, 0);
    }

    expect(addHistory).not.toHaveBeenCalled();
  });
});
