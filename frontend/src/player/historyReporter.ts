import { addHistory } from "@/api/history";

const HISTORY_MIN_SECONDS = 30;

export class HistoryReporter {
  private trackId: string | null = null;
  private duration = 0;
  private elapsed = 0;
  private previousTime = 0;
  private reported = false;

  load(trackId: string) {
    this.trackId = trackId;
    this.duration = 0;
    this.elapsed = 0;
    this.previousTime = 0;
    this.reported = false;
  }

  setDuration(duration: number) {
    this.duration = duration;
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

    const reachedTime = this.elapsed >= HISTORY_MIN_SECONDS;
    const reachedHalf = duration > 0 && currentTime / duration >= 0.5;

    if (reachedTime || reachedHalf) {
      this.reported = true;
      Promise.resolve(addHistory(this.trackId)).catch((err) => {
        console.warn("Failed to record listen history", err);
      });
    }
  }
}
