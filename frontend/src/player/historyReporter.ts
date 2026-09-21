import { addHistory } from "@/api/history";

export const HISTORY_DEFAULT_MIN_SECONDS = 30;
export const HISTORY_DEFAULT_MIN_PERCENT = 50;

/** What to call once a track passes the listen threshold. */
export type ListenReporter = (id: string) => Promise<unknown>;

export class HistoryReporter {
  private trackId: string | null = null;
  private report: ListenReporter = addHistory;
  private duration = 0;
  private elapsed = 0;
  private previousTime = 0;
  private reported = false;
  private minSeconds = HISTORY_DEFAULT_MIN_SECONDS;
  private minPercent = HISTORY_DEFAULT_MIN_PERCENT;

  load(trackId: string | null, report: ListenReporter = addHistory) {
    this.trackId = trackId;
    this.report = report;
    this.duration = 0;
    this.elapsed = 0;
    this.previousTime = 0;
    this.reported = false;
  }

  setDuration(duration: number) {
    this.duration = duration;
  }

  /** Apply the user's listen thresholds (seconds / percent of duration). */
  setThresholds(minSeconds: number, minPercent: number) {
    this.minSeconds = minSeconds;
    this.minPercent = minPercent;
  }

  onTimeUpdate(currentTime: number, duration = this.duration) {
    if (this.reported || !this.trackId) return;

    const delta = currentTime - this.previousTime;
    // Only count small, positive time advances. Jumps from seeks or
    // fast-forwards are ignored so we only accumulate real playback time.
    if (delta > 0 && delta < 5) {
      this.elapsed += delta;
    }
    this.previousTime = currentTime;

    const reachedTime = this.elapsed >= this.minSeconds;
    const reachedPercent =
      duration > 0 && currentTime / duration >= this.minPercent / 100;

    if (reachedTime || reachedPercent) {
      this.reported = true;
      Promise.resolve(this.report(this.trackId)).catch((err) => {
        console.warn("Failed to record listen history", err);
      });
    }
  }
}
