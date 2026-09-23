import { defineStore } from "pinia";
import { computed, ref, type Ref } from "vue";
import { getApiErrorMessage } from "@/api/client";
import * as outputsApi from "@/api/outputs";
import type {
  OutputCreate,
  OutputResponse,
  OutputUpdate,
  ProviderResponse,
} from "@/api/outputs";
import { i18n } from "@/i18n";

export interface OutputFormState {
  providerType: string;
  name: string;
  enabled: boolean;
  config: Record<string, unknown>;
}

export const useOutputsStore = defineStore("outputs", () => {
  const outputs: Ref<OutputResponse[]> = ref([]);
  const providers: Ref<ProviderResponse[]> = ref([]);
  const loading = ref(false);
  const error: Ref<string | null> = ref(null);

  const webOutput = computed<OutputResponse>(() => ({
    id: "web",
    user_id: "",
    provider_type: "web",
    name: i18n.global.t("outputs.thisDevice"),
    config: {},
    capabilities: null,
    enabled: true,
    last_error: null,
    created_at: "",
    updated_at: "",
  }));

  const enabledOutputs = computed(() =>
    outputs.value.filter((output) => output.enabled),
  );

  function getProvider(providerType: string): ProviderResponse | undefined {
    return providers.value.find((p) => p.provider_type === providerType);
  }

  function getOutput(id: string): OutputResponse | undefined {
    return outputs.value.find((o) => o.id === id);
  }

  function errorMessage(err: unknown): string {
    return (
      getApiErrorMessage(err) ||
      (err instanceof Error ? err.message : i18n.global.t("errors.unknown"))
    );
  }

  async function loadProviders(): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      providers.value = await outputsApi.listOutputProviders();
    } catch (err) {
      error.value = errorMessage(err);
      providers.value = [];
    } finally {
      loading.value = false;
    }
  }

  async function loadOutputs(): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      outputs.value = await outputsApi.listOutputs();
    } catch (err) {
      error.value = errorMessage(err);
      outputs.value = [];
    } finally {
      loading.value = false;
    }
  }

  async function createOutput(body: OutputCreate): Promise<OutputResponse> {
    const created = await outputsApi.createOutput(body);
    outputs.value.unshift(created);
    return created;
  }

  async function updateOutput(
    id: string,
    body: OutputUpdate,
  ): Promise<OutputResponse> {
    const updated = await outputsApi.updateOutput(id, body);
    const index = outputs.value.findIndex((o) => o.id === id);
    if (index !== -1) {
      outputs.value[index] = updated;
    }
    return updated;
  }

  async function deleteOutput(id: string): Promise<void> {
    await outputsApi.deleteOutput(id);
    outputs.value = outputs.value.filter((o) => o.id !== id);
  }

  async function validateOutput(
    id: string,
  ): Promise<outputsApi.ValidationResponse> {
    return outputsApi.validateOutput(id);
  }

  function $reset(): void {
    outputs.value = [];
    providers.value = [];
    loading.value = false;
    error.value = null;
  }

  return {
    outputs,
    providers,
    loading,
    error,
    webOutput,
    enabledOutputs,
    getProvider,
    getOutput,
    loadProviders,
    loadOutputs,
    createOutput,
    updateOutput,
    deleteOutput,
    validateOutput,
    $reset,
  };
});
