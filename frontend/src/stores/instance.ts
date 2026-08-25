import { computed, ref, type Ref } from "vue";
import { defineStore } from "pinia";
import { getInstance, type InstanceInfo } from "@/api/instance";
import { getApiErrorMessage } from "@/api/client";

export type InstanceStoreStatus = "idle" | "loading" | "loaded" | "error";

let bootstrapped: Promise<void> | null = null;

export const useInstanceStore = defineStore("instance", () => {
  const instance: Ref<InstanceInfo | null> = ref(null);
  const error: Ref<string | null> = ref(null);
  const status: Ref<InstanceStoreStatus> = ref("idle");

  const registrations = computed(() => instance.value?.registrations ?? false);
  const approvalRequired = computed(
    () => instance.value?.approval_required ?? false,
  );
  const invitesEnabled = computed(
    () => instance.value?.invites_enabled ?? false,
  );
  const loading = computed(() => status.value === "loading");

  async function load(): Promise<void> {
    if (bootstrapped && status.value !== "idle") return bootstrapped;

    bootstrapped = (async () => {
      status.value = "loading";
      try {
        instance.value = await getInstance();
        error.value = null;
        status.value = "loaded";
      } catch (err) {
        error.value = getApiErrorMessage(err);
        status.value = "error";
      }
    })();

    return bootstrapped;
  }

  return {
    instance,
    error,
    status,
    registrations,
    approvalRequired,
    invitesEnabled,
    loading,
    load,
  };
});
