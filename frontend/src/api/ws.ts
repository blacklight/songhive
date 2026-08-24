import { ref, type Ref } from "vue";
import { buildUrl, WS_URL } from "./config";

export type WsStatus =
  "idle" | "connecting" | "open" | "reconnecting" | "closed";

export interface WsEvent {
  type: string;
  data: unknown;
  topic?: string;
}

export interface WsMessage {
  action: "subscribe" | "unsubscribe";
  topics: string[];
}

type WsHandler = (event: WsEvent) => void;

let tokenProvider: (() => string | null) | null = null;

export function setWsTokenProvider(provider: () => string | null) {
  tokenProvider = provider;
}

export class EventBus {
  private socket: WebSocket | null = null;
  private handlers: Map<string, Set<WsHandler>> = new Map();
  private topics: Set<string> = new Set();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private currentDelay = 1000;
  private maxDelay = 30000;
  private closing = false;

  status: Ref<WsStatus> = ref("idle");

  connect() {
    if (this.socket) return;

    this.closing = false;
    this.status.value = "connecting";
    const token = tokenProvider ? tokenProvider() : "";
    const url = buildUrl(WS_URL, token ? { token } : undefined);

    const socket = new WebSocket(url);
    this.socket = socket;

    socket.addEventListener("open", () => {
      this.status.value = "open";
      this.currentDelay = 1000;
      if (this.topics.size > 0) {
        this.send({ action: "subscribe", topics: [...this.topics] });
      }
    });

    socket.addEventListener("message", (event) => {
      try {
        const payload = JSON.parse(event.data) as WsEvent;
        const handlers = this.handlers.get(payload.type);
        if (handlers) {
          handlers.forEach((h) => h(payload));
        }
      } catch {
        // Ignore malformed messages.
      }
    });

    socket.addEventListener("close", () => {
      this.socket = null;
      if (this.closing) {
        this.status.value = "closed";
        return;
      }
      this.status.value = "reconnecting";
      this.scheduleReconnect();
    });

    socket.addEventListener("error", () => {
      this.status.value = "reconnecting";
    });
  }

  disconnect() {
    this.closing = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
    this.status.value = "closed";
  }

  subscribe(topics: string[]) {
    topics.forEach((t) => this.topics.add(t));
    if (this.status.value === "open" && this.socket) {
      this.send({ action: "subscribe", topics });
    } else {
      this.connect();
    }
  }

  unsubscribe(topics: string[]) {
    topics.forEach((t) => this.topics.delete(t));
    if (this.status.value === "open" && this.socket) {
      this.send({ action: "unsubscribe", topics });
    }
  }

  on(type: string, handler: WsHandler) {
    if (!this.handlers.has(type)) {
      this.handlers.set(type, new Set());
    }
    this.handlers.get(type)!.add(handler);
  }

  off(type: string, handler: WsHandler) {
    this.handlers.get(type)?.delete(handler);
  }

  private send(message: WsMessage) {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(message));
    }
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.closing = false;
      this.connect();
      this.currentDelay = Math.min(this.currentDelay * 2, this.maxDelay);
    }, this.currentDelay);
  }
}
