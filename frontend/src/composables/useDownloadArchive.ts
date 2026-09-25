import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { useToastStore } from "@/stores/toast";
import { getApiErrorMessage } from "@/api/client";
import { createArchive, type ArchiveCreateRequest } from "@/api/downloads";

/**
 * Queue a bulk-download archive and report the result through a toast.
 *
 * The archive builds asynchronously on the server; the user is pointed at
 * the Downloads page (and notified when the ZIP is ready).
 */
export function useDownloadArchive() {
  const { t } = useI18n();
  const toastStore = useToastStore();
  const requesting = ref(false);

  async function requestArchive(
    request: ArchiveCreateRequest,
  ): Promise<boolean> {
    if (requesting.value) return false;
    requesting.value = true;
    try {
      const archive = await createArchive(request);
      toastStore.push({
        type: "success",
        message: t("downloads.queued", { label: archive.label }),
      });
      return true;
    } catch (err) {
      toastStore.push({
        type: "error",
        message: t("downloads.requestFailed", {
          message: getApiErrorMessage(err),
        }),
      });
      return false;
    } finally {
      requesting.value = false;
    }
  }

  return { requesting, requestArchive };
}
