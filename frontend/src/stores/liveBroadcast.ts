import { computed, ref, watch, type Ref } from "vue";
import { defineStore } from "pinia";
import { buildUrl } from "@/api/config";
import { LIVE_MIME_CANDIDATES, LIVE_WS_CLOSE } from "@/api/streams";
import type { StreamResponse } from "@/api/streams";
import { useAuthStore } from "@/stores/auth";

/**
 * Live device broadcast state.
 *
 * One broadcast per tab, owned by this store rather than a component so it
 * survives modal closes and SPA navigation. The flow: ``ensurePermission``
 * (browser prompt) → ``openPreview`` (mic capture + level meter) →
 * ``start`` (WebSocket ``start`` handshake + MediaRecorder slices) →
 * ``on-air``. The server side is ``/ws/live/{output_id}`` — see
 * ``songhive/streaming/live.py`` for the message protocol and close codes.
 */
export type LiveBroadcastStatus =
  | "idle"
  | "requesting-permission"
  | "ready"
  | "connecting"
  | "on-air"
  | "stopping"
  | "error";

export type LivePermission = "prompt" | "granted" | "denied" | "unsupported";

/** Error shown in the UI — an i18n key under ``pages.streams.broadcast``. */
export interface LiveBroadcastError {
  key: string;
  message?: string;
}

const DEVICE_STORAGE_KEY = "songhive.live.deviceId";
const MAX_RECONNECTS = 5;
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 8000;
const RECORDER_TIMESLICE_MS = 250;
const RECORDER_BITRATE = 128_000;
// Bytes buffered on the socket beyond which we warn about the uplink.
const BUFFERED_WARN_BYTES = 2 * 1024 * 1024;

function makeIngestId(): string {
  // Matches the server-side ``^[A-Za-z0-9._-]{8,128}$`` constraint.
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  const rand = Array.from({ length: 16 }, () =>
    Math.floor(Math.random() * 16).toString(16),
  ).join("");
  return `ingest-${Date.now()}-${rand}`;
}

/** Map server error codes to i18n keys under ``pages.streams.broadcast.error``. */
const ERROR_CODE_KEYS: Record<string, string> = {
  disabled: "disabled",
  forbidden: "forbidden",
  not_found: "notFound",
  bad_ingest_id: "generic",
  unsupported_format: "unsupportedFormat",
  already_started: "generic",
  expected_start: "generic",
  conflict: "conflict",
  bitrate_exceeded: "tooSlow",
  backpressure: "tooSlow",
  start_timeout: "startTimeout",
  worker_unavailable: "workerUnavailable",
  output_gone: "notFound",
  internal_error: "server",
  bad_message: "generic",
};

/** Close codes that end the broadcast immediately (no reconnect). */
const TERMINAL_CLOSE_CODES = new Set<number>([
  LIVE_WS_CLOSE.unauthenticated,
  LIVE_WS_CLOSE.forbidden,
  LIVE_WS_CLOSE.notFound,
  LIVE_WS_CLOSE.conflict,
  LIVE_WS_CLOSE.startTimeout,
  LIVE_WS_CLOSE.tooSlow,
  LIVE_WS_CLOSE.workerUnavailable,
  LIVE_WS_CLOSE.serverError,
]);

function errorKeyForClose(code: number): string {
  switch (code) {
    case LIVE_WS_CLOSE.unauthenticated:
      return "authentication";
    case LIVE_WS_CLOSE.forbidden:
      return "forbidden";
    case LIVE_WS_CLOSE.notFound:
      return "notFound";
    case LIVE_WS_CLOSE.conflict:
      return "conflict";
    case LIVE_WS_CLOSE.startTimeout:
      return "startTimeout";
    case LIVE_WS_CLOSE.tooSlow:
      return "tooSlow";
    case LIVE_WS_CLOSE.workerUnavailable:
      return "workerUnavailable";
    case LIVE_WS_CLOSE.serverError:
      return "server";
    default:
      return "connectionLost";
  }
}

function pickMimeType(): string | null {
  if (typeof MediaRecorder === "undefined") return null;
  for (const mime of LIVE_MIME_CANDIDATES) {
    try {
      if (MediaRecorder.isTypeSupported(mime)) return mime;
    } catch {
      // Ignore browsers that throw on probing.
    }
  }
  return null;
}

export const useLiveBroadcastStore = defineStore("liveBroadcast", () => {
  const status: Ref<LiveBroadcastStatus> = ref("idle");
  const permission: Ref<LivePermission> = ref("prompt");
  const devices: Ref<MediaDeviceInfo[]> = ref([]);
  const deviceId: Ref<string> = ref(
    localStorage.getItem(DEVICE_STORAGE_KEY) ?? "",
  );
  const voiceMode = ref(false);
  const outputId: Ref<string | null> = ref(null);
  const mount: Ref<string | null> = ref(null);
  const streamName: Ref<string | null> = ref(null);
  const level = ref(0);
  const startedAt: Ref<number | null> = ref(null);
  const listeners = ref(0);
  const error: Ref<LiveBroadcastError | null> = ref(null);
  const slowConnection = ref(false);

  const isActive = computed(
    () =>
      status.value === "connecting" ||
      status.value === "on-air" ||
      status.value === "stopping",
  );

  // Internal non-reactive handles — the store controls their lifecycle.
  let mediaStream: MediaStream | null = null;
  let recorder: MediaRecorder | null = null;
  let socket: WebSocket | null = null;
  let audioContext: AudioContext | null = null;
  let analyser: AnalyserNode | null = null;
  let levelFrame: number | null = null;
  let reconnectAttempts = 0;
  let intentionalClose = false;
  let lastErrorCode: string | null = null;
  let devicesListenerBound = false;
  let beforeUnloadBound = false;

  function captureSupported(): boolean {
    return (
      typeof window !== "undefined" &&
      window.isSecureContext !== false &&
      !!navigator.mediaDevices?.getUserMedia &&
      typeof MediaRecorder !== "undefined"
    );
  }

  /**
   * Resolve microphone permission, prompting once when needed. Device
   * labels stay empty until permission is granted, so a bare
   * ``getUserMedia`` runs first when the state cannot be queried or is
   * ``prompt``.
   */
  async function ensurePermission(): Promise<LivePermission> {
    if (!captureSupported()) {
      permission.value = "unsupported";
      status.value = "error";
      error.value = { key: "unsupported" };
      return permission.value;
    }

    let state: PermissionState;
    try {
      const result = await navigator.permissions.query({
        // ``microphone`` is not in every lib.dom PermissionName union.
        name: "microphone" as PermissionName,
      });
      state = result.state;
      result.addEventListener("change", () => {
        if (result.state === "granted" && permission.value !== "granted") {
          void ensurePermission();
        }
      });
    } catch {
      // Firefox/Safari may not support the query — fall back to prompting.
      state = "prompt";
    }
    permission.value =
      state === "granted"
        ? "granted"
        : state === "denied"
          ? "denied"
          : "prompt";

    if (state === "denied") {
      status.value = "error";
      error.value = { key: "permissionDenied" };
      return permission.value;
    }
    if (state !== "granted") {
      status.value = "requesting-permission";
      try {
        const probe = await navigator.mediaDevices.getUserMedia({
          audio: true,
        });
        probe.getTracks().forEach((track) => track.stop());
        permission.value = "granted";
      } catch (err) {
        const name = err instanceof DOMException ? err.name : "";
        if (name === "NotAllowedError" || name === "SecurityError") {
          permission.value = "denied";
          error.value = { key: "permissionDenied" };
        } else if (name === "NotFoundError") {
          permission.value = "granted";
          error.value = { key: "noDevices" };
        } else {
          error.value = { key: "captureFailed" };
        }
        status.value = "error";
        return permission.value;
      }
    }

    await refreshDevices();
    bindDeviceListener();
    if (!isActive.value) status.value = "ready";
    return permission.value;
  }

  async function refreshDevices(): Promise<void> {
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      devices.value = all.filter((d) => d.kind === "audioinput");
      // The persisted choice may refer to a device that is gone.
      if (
        deviceId.value &&
        !devices.value.some((d) => d.deviceId === deviceId.value)
      ) {
        deviceId.value = "";
        localStorage.removeItem(DEVICE_STORAGE_KEY);
      }
    } catch {
      devices.value = [];
    }
  }

  function bindDeviceListener() {
    if (devicesListenerBound || !navigator.mediaDevices?.addEventListener) {
      return;
    }
    devicesListenerBound = true;
    navigator.mediaDevices.addEventListener("devicechange", () => {
      void refreshDevices();
    });
  }

  function audioConstraints() {
    const constraints: MediaTrackConstraints = {
      echoCancellation: voiceMode.value,
      noiseSuppression: voiceMode.value,
      autoGainControl: voiceMode.value,
      channelCount: { ideal: 2 },
    };
    if (deviceId.value) constraints.deviceId = { exact: deviceId.value };
    return { audio: constraints };
  }

  /**
   * Open (or reopen) the capture device so the level meter runs before
   * going live. Accepts an optional stream factory so alternate sources
   * (e.g. tab audio) can be added without restructuring.
   */
  async function openPreview(
    getStream?: () => Promise<MediaStream>,
  ): Promise<boolean> {
    releaseStream();
    try {
      mediaStream = await (
        getStream ??
        (() => navigator.mediaDevices.getUserMedia(audioConstraints()))
      )();
    } catch (err) {
      const name = err instanceof DOMException ? err.name : "";
      if (name === "NotAllowedError") {
        permission.value = "denied";
        error.value = { key: "permissionDenied" };
      } else if (name === "NotFoundError") {
        error.value = { key: "noDevices" };
      } else {
        error.value = { key: "captureFailed" };
      }
      status.value = "error";
      return false;
    }
    watchStreamTracks(mediaStream);
    startLevelMeter(mediaStream);
    return true;
  }

  /** Persisted device choice; reopens the preview unless broadcasting. */
  async function selectDevice(id: string): Promise<void> {
    deviceId.value = id;
    if (id) localStorage.setItem(DEVICE_STORAGE_KEY, id);
    else localStorage.removeItem(DEVICE_STORAGE_KEY);
    if (!isActive.value && permission.value === "granted") {
      await openPreview();
    }
  }

  /** Voice-mode toggle re-captures the preview with processing enabled. */
  async function setVoiceMode(enabled: boolean): Promise<void> {
    voiceMode.value = enabled;
    if (!isActive.value && mediaStream) {
      await openPreview();
    }
  }

  function watchStreamTracks(stream: MediaStream) {
    for (const track of stream.getTracks()) {
      track.addEventListener("ended", () => {
        if (isActive.value) fail("deviceLost");
      });
    }
  }

  function startLevelMeter(stream: MediaStream) {
    stopLevelMeter();
    try {
      audioContext = new AudioContext();
      const source = audioContext.createMediaStreamSource(stream);
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
    } catch {
      analyser = null;
      return;
    }
    const data = new Uint8Array(analyser.fftSize);
    const tick = () => {
      if (!analyser) return;
      analyser.getByteTimeDomainData(data);
      let peak = 0;
      for (let i = 0; i < data.length; i += 1) {
        const v = Math.abs(data[i] - 128) / 128;
        if (v > peak) peak = v;
      }
      level.value = peak;
      levelFrame = requestAnimationFrame(tick);
    };
    levelFrame = requestAnimationFrame(tick);
  }

  function stopLevelMeter() {
    if (levelFrame !== null) {
      cancelAnimationFrame(levelFrame);
      levelFrame = null;
    }
    level.value = 0;
    if (audioContext) {
      void audioContext.close().catch(() => {});
      audioContext = null;
      analyser = null;
    }
  }

  function releaseStream() {
    if (mediaStream) {
      mediaStream.getTracks().forEach((track) => track.stop());
      mediaStream = null;
    }
  }

  /**
   * Go live on ``stream``: connect the ingest socket, send the ``start``
   * handshake, and record once ``accepted`` arrives.
   */
  async function start(
    stream: StreamResponse,
    options: { title?: string } = {},
  ): Promise<void> {
    if (isActive.value) return;
    const mime = pickMimeType();
    if (!mime) {
      status.value = "error";
      error.value = { key: "noRecorder" };
      return;
    }
    if (!mediaStream && !(await openPreview())) return;

    outputId.value = stream.id;
    mount.value = stream.mount;
    streamName.value = stream.name;
    listeners.value = 0;
    error.value = null;
    reconnectAttempts = 0;
    status.value = "connecting";
    connect(mime, options.title ?? "");
  }

  function connect(mime: string, title: string) {
    intentionalClose = false;
    lastErrorCode = null;
    const ws = new WebSocket(buildUrl(`/ws/live/${outputId.value}`));
    socket = ws;
    ws.binaryType = "blob";

    ws.addEventListener("open", () => {
      ws.send(
        JSON.stringify({
          action: "start",
          ingest_id: makeIngestId(),
          mime,
          title,
        }),
      );
    });

    ws.addEventListener("message", (event) => {
      let payload: { type?: string; code?: string; message?: string };
      try {
        payload = JSON.parse(String(event.data));
      } catch {
        return;
      }
      switch (payload.type) {
        case "accepted":
          startRecorder(ws, mime);
          break;
        case "on_air":
          reconnectAttempts = 0;
          startedAt.value = Date.now();
          status.value = "on-air";
          bindBeforeUnload();
          break;
        case "listeners": {
          const count = (payload as { count?: number }).count;
          if (typeof count === "number") listeners.value = count;
          break;
        }
        case "stopped":
          // The server ended the broadcast (e.g. duration cap) — not an
          // error; the close frame lands right after the teardown.
          intentionalClose = true;
          teardown();
          break;
        case "error":
          lastErrorCode = payload.code
            ? (ERROR_CODE_KEYS[payload.code] ?? "generic")
            : null;
          break;
      }
    });

    ws.addEventListener("close", (event) => {
      socket = null;
      stopRecorder();
      if (intentionalClose || !isActive.value) return;
      if (event.code === LIVE_WS_CLOSE.clean) {
        // Clean close without a ``stopped`` message — just end.
        teardown();
        return;
      }
      if (
        reconnectAttempts < MAX_RECONNECTS &&
        !TERMINAL_CLOSE_CODES.has(event.code)
      ) {
        scheduleReconnect(mime, title);
        return;
      }
      fail(lastErrorCode ?? errorKeyForClose(event.code));
    });

    ws.addEventListener("error", () => {
      // The close event reports the actual code.
    });
  }

  function scheduleReconnect(mime: string, title: string) {
    reconnectAttempts += 1;
    status.value = "connecting";
    const delay = Math.min(
      RECONNECT_BASE_MS * 2 ** (reconnectAttempts - 1),
      RECONNECT_MAX_MS,
    );
    setTimeout(() => {
      if (status.value === "connecting" && outputId.value) {
        connect(mime, title);
      }
    }, delay);
  }

  function startRecorder(ws: WebSocket, mime: string) {
    stopRecorder();
    if (!mediaStream) return;
    try {
      recorder = new MediaRecorder(mediaStream, {
        mimeType: mime,
        audioBitsPerSecond: RECORDER_BITRATE,
      });
    } catch {
      fail("noRecorder");
      return;
    }
    recorder.addEventListener("dataavailable", (event) => {
      const blob = event.data as Blob;
      if (!blob.size || ws.readyState !== WebSocket.OPEN) return;
      ws.send(blob);
      // Dropping bytes corrupts the container, so a congested uplink is
      // surfaced as a warning instead; the server caps the bitrate anyway.
      slowConnection.value = ws.bufferedAmount > BUFFERED_WARN_BYTES;
    });
    recorder.start(RECORDER_TIMESLICE_MS);
  }

  function stopRecorder() {
    if (recorder) {
      try {
        if (recorder.state !== "inactive") recorder.stop();
      } catch {
        // Already stopped.
      }
      recorder = null;
    }
  }

  /** End the broadcast deliberately and release every resource. */
  function stop() {
    if (status.value === "idle") return;
    status.value = "stopping";
    intentionalClose = true;
    try {
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ action: "stop" }));
      }
      socket?.close();
    } catch {
      // Ignore: the socket may already be closing.
    }
    socket = null;
    teardown();
  }

  /** Abort with an error state, releasing every resource. */
  function fail(key: string) {
    intentionalClose = true;
    try {
      socket?.close();
    } catch {
      // Ignore.
    }
    socket = null;
    teardown();
    status.value = "error";
    error.value = { key };
  }

  function teardown() {
    stopRecorder();
    releaseStream();
    stopLevelMeter();
    unbindBeforeUnload();
    slowConnection.value = false;
    // A deliberate stop returns to a preview-ready ``idle`` — the modal
    // reopens the capture device when it is shown again.
    if (status.value !== "error") status.value = "idle";
    outputId.value = null;
    mount.value = null;
    streamName.value = null;
    startedAt.value = null;
  }

  function bindBeforeUnload() {
    if (beforeUnloadBound) return;
    beforeUnloadBound = true;
    window.addEventListener("beforeunload", onBeforeUnload);
  }

  function unbindBeforeUnload() {
    if (!beforeUnloadBound) return;
    beforeUnloadBound = false;
    window.removeEventListener("beforeunload", onBeforeUnload);
  }

  function onBeforeUnload(event: BeforeUnloadEvent) {
    // Browsers ignore custom text; preventDefault triggers the dialog.
    event.preventDefault();
  }

  /** Clear a terminal error back to idle so the flow can be retried. */
  function reset() {
    if (status.value !== "error") return;
    error.value = null;
    status.value = "idle";
  }

  // Logging out while on air must not leak a live broadcast.
  const auth = useAuthStore();
  watch(
    () => auth.isAuthenticated,
    (authenticated) => {
      if (!authenticated && isActive.value) stop();
    },
  );

  return {
    status,
    permission,
    devices,
    deviceId,
    voiceMode,
    outputId,
    mount,
    streamName,
    level,
    startedAt,
    listeners,
    error,
    slowConnection,
    isActive,
    ensurePermission,
    refreshDevices,
    selectDevice,
    setVoiceMode,
    openPreview,
    start,
    stop,
    reset,
  };
});
