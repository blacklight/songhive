import { reactive } from "vue";
import { useI18n } from "vue-i18n";
import {
  sendStreamCommand,
  updateStream,
  type StreamResponse,
} from "@/api/streams";
import { getApiErrorMessage } from "@/api/client";
import { useToastStore } from "@/stores/toast";

/**
 * Owner/admin actions on a listed stream: enable/disable the mount and
 * toggle play/pause on the playback session driving it. ``busyStreams``
 * holds the ids with an in-flight action so cards can disable their
 * buttons.
 */
export function useStreamActions() {
  const { t } = useI18n();
  const toast = useToastStore();
  const busyStreams = reactive(new Set<string>());

  function errorMessage(err: unknown): string {
    return (
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : t("errors.unknown"))
    );
  }

  function reportError(err: unknown) {
    toast.push({
      type: "error",
      message: t("pages.streams.actionError", { message: errorMessage(err) }),
    });
  }

  async function toggleEnabled(stream: StreamResponse) {
    if (busyStreams.has(stream.id)) return;
    busyStreams.add(stream.id);
    try {
      const result = await updateStream(stream.id, {
        enabled: !stream.enabled,
      });
      stream.enabled = result.enabled;
    } catch (err) {
      reportError(err);
    } finally {
      busyStreams.delete(stream.id);
    }
  }

  async function togglePlayback(stream: StreamResponse) {
    if (busyStreams.has(stream.id)) return;
    busyStreams.add(stream.id);
    const command = stream.playback_state === "playing" ? "pause" : "play";
    try {
      const result = await sendStreamCommand(stream.id, command);
      stream.playback_state = result.state;
    } catch (err) {
      reportError(err);
    } finally {
      busyStreams.delete(stream.id);
    }
  }

  return { busyStreams, toggleEnabled, togglePlayback };
}
