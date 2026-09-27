import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { useToastStore } from "@/stores/toast";
import { getApiErrorMessage } from "@/api/client";
import { downloadM3u } from "@/api/downloads";

/**
 * Download an album or playlist as an M3U playlist file.
 *
 * Requesters who can manage the entity export with ``access="token"`` so
 * non-public member tracks carry an embedded share token that standalone
 * players can use; everyone else gets the anonymous (``exclude``) export.
 */
export function useM3uExport() {
  const { t } = useI18n();
  const toastStore = useToastStore();
  const exporting = ref(false);

  async function exportM3u(
    kind: "albums" | "playlists",
    id: string,
    canManage: boolean,
  ): Promise<boolean> {
    if (exporting.value) return false;
    exporting.value = true;
    try {
      await downloadM3u(kind, id, {
        access: canManage ? "token" : "exclude",
      });
      return true;
    } catch (err) {
      toastStore.push({
        type: "error",
        message: t("downloads.m3uFailed", {
          message: getApiErrorMessage(err),
        }),
      });
      return false;
    } finally {
      exporting.value = false;
    }
  }

  return { exporting, exportM3u };
}
