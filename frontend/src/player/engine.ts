import { streamUrl } from "@/api/stream";
import type { QueueTrack, EngineCallbacks } from "./types";
import { HistoryReporter } from "./historyReporter";

export class PlayerEngine {
  private primary: HTMLAudioElement;
  private preload: HTMLAudioElement;
  private callbacks: EngineCallbacks = {};
  private nextTrack: QueueTrack | null = null;
  private currentUrl = "";
  private history: HistoryReporter;
  private pendingStartAt: number | undefined;

  private boundTimeUpdate: () => void;
  private boundLoadedMetadata: () => void;
  private boundPlay: () => void;
  private boundPause: () => void;
  private boundWaiting: () => void;
  private boundStalled: () => void;
  private boundEnded: () => void;
  private boundError: () => void;
  private boundCanPlay: () => void;

  constructor() {
    this.primary = new Audio();
    this.preload = new Audio();
    this.history = new HistoryReporter();

    this.boundTimeUpdate = () => this.handleTimeUpdate();
    this.boundLoadedMetadata = () => this.handleLoadedMetadata();
    this.boundPlay = () => this.handlePlay();
    this.boundPause = () => this.handlePause();
    this.boundWaiting = () => this.handleWaiting();
    this.boundStalled = () => this.handleStalled();
    this.boundEnded = () => this.handleEnded();
    this.boundError = () => this.handleError();
    this.boundCanPlay = () => this.handleCanPlay();
  }

  init(callbacks: EngineCallbacks) {
    this.callbacks = callbacks;
    this.removeListeners(this.primary);
    this.addListeners(this.primary);
  }

  load(track: QueueTrack, startAt?: number) {
    this.pendingStartAt = startAt;
    this.history.load(track.id);
    this.callbacks.onStateChange?.("loading");

    const url = streamUrl(track);
    if (this.currentUrl === url && this.primary.src === url) {
      // Same track already loaded (e.g. after gapless swap). Just reset
      // position and play state, without re-requesting the resource.
      this.primary.currentTime = startAt ?? 0;
      return;
    }

    this.currentUrl = url;
    this.primary.src = url;
    this.primary.load();
  }

  play() {
    const promise = this.primary.play();
    if (promise && typeof promise.catch === "function") {
      promise.catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (
          err &&
          typeof err === "object" &&
          "name" in err &&
          err.name === "AbortError"
        )
          return;
        console.warn("play() failed", err);
      });
    }
  }

  pause() {
    this.primary.pause();
  }

  seek(seconds: number) {
    this.primary.currentTime = seconds;
  }

  setVolume(volume: number, muted: boolean) {
    this.primary.volume = volume;
    this.primary.muted = muted;
  }

  setNextTrack(track: QueueTrack | null) {
    this.nextTrack = track;
    if (!track) {
      this.preload.src = "";
    }
  }

  destroy() {
    this.removeListeners(this.primary);
    this.pause();
    this.primary.src = "";
    this.currentUrl = "";
    this.preload.src = "";
    this.nextTrack = null;
  }

  private addListeners(audio: HTMLAudioElement) {
    audio.addEventListener("timeupdate", this.boundTimeUpdate);
    audio.addEventListener("loadedmetadata", this.boundLoadedMetadata);
    audio.addEventListener("play", this.boundPlay);
    audio.addEventListener("pause", this.boundPause);
    audio.addEventListener("waiting", this.boundWaiting);
    audio.addEventListener("stalled", this.boundStalled);
    audio.addEventListener("ended", this.boundEnded);
    audio.addEventListener("error", this.boundError);
    audio.addEventListener("canplay", this.boundCanPlay);
  }

  private removeListeners(audio: HTMLAudioElement) {
    audio.removeEventListener("timeupdate", this.boundTimeUpdate);
    audio.removeEventListener("loadedmetadata", this.boundLoadedMetadata);
    audio.removeEventListener("play", this.boundPlay);
    audio.removeEventListener("pause", this.boundPause);
    audio.removeEventListener("waiting", this.boundWaiting);
    audio.removeEventListener("stalled", this.boundStalled);
    audio.removeEventListener("ended", this.boundEnded);
    audio.removeEventListener("error", this.boundError);
    audio.removeEventListener("canplay", this.boundCanPlay);
  }

  private handleCanPlay() {
    if (this.pendingStartAt !== undefined) {
      this.primary.currentTime = this.pendingStartAt;
      this.pendingStartAt = undefined;
    }
  }

  private handleTimeUpdate() {
    const t = this.primary.currentTime;
    const d = this.primary.duration;
    this.callbacks.onTimeUpdate?.(t);
    this.history.onTimeUpdate(t, d);

    if (this.nextTrack && d > 0) {
      const progress = t / d;
      if (progress >= 0.8) {
        const url = streamUrl(this.nextTrack);
        if (this.preload.src !== url) {
          this.preload.src = url;
          this.preload.load();
        }
      }
    }
  }

  private handleLoadedMetadata() {
    const d = this.primary.duration;
    this.callbacks.onDuration?.(d);
    this.history.setDuration(d);
  }

  private handlePlay() {
    this.callbacks.onStateChange?.("playing");
  }

  private handlePause() {
    this.callbacks.onStateChange?.("paused");
  }

  private handleWaiting() {
    this.callbacks.onStateChange?.("loading");
  }

  private handleStalled() {
    this.callbacks.onStateChange?.("loading");
  }

  private handleEnded() {
    if (this.nextTrack && this.preload.src) {
      const url = this.preload.src;
      this.primary.src = url;
      this.primary.currentTime = 0;
      this.currentUrl = url;
      this.preload.src = "";
    }
    this.callbacks.onEnded?.();
  }

  private handleError() {
    this.callbacks.onError?.(this.primary.error);
    this.callbacks.onStateChange?.("error");
  }
}

export const playerEngine = new PlayerEngine();
