<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useOutputsStore } from "@/stores/outputs";
import type { ProviderField, OutputResponse } from "@/api/outputs";
import { useConfirmStore } from "@/stores/confirm";
import { useToastStore } from "@/stores/toast";
import AppButton from "@/components/ui/AppButton.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppTable from "@/components/ui/AppTable.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

const { t } = useI18n();
const store = useOutputsStore();
const confirm = useConfirmStore();
const toast = useToastStore();

const editingId = ref<string | null>(null);
const showForm = ref(false);
const isSaving = ref(false);
const isTesting = ref(false);
const formError = ref<string | null>(null);
const testResult = ref<string | null>(null);

const name = ref("");
const providerType = ref("");
const enabled = ref(true);
const config = reactive<Record<string, unknown>>({});
const redactedKeys = ref<Set<string>>(new Set());

const providerOptions = computed(() =>
  store.providers
    .filter((p) => p.can_create)
    .map((p) => ({
      value: p.provider_type,
      label: p.label || p.provider_type,
    })),
);

const provider = computed(() =>
  store.providers.find((p) => p.provider_type === providerType.value),
);

const fields = computed<ProviderField[]>(() => provider.value?.fields || []);

const canCreate = computed(() => providerOptions.value.length > 0);

const columns = computed(() => [
  { key: "name", label: t("outputs.name") },
  { key: "provider_type", label: t("outputs.provider") },
  { key: "stream_url", label: t("outputs.streamUrl") },
  { key: "enabled", label: t("outputs.enabled"), align: "center" as const },
  { key: "status", label: t("outputs.status") },
  {
    key: "actions",
    label: t("browse.detail.actions"),
    align: "center" as const,
  },
]);

const rows = computed<Record<string, unknown>[]>(() =>
  store.outputs.map((output) => ({
    id: output.id,
    name: output.name,
    provider_type:
      store.getProvider(output.provider_type)?.label || output.provider_type,
    stream_url: output.stream_url || "",
    enabled: output.enabled,
    status: output.last_error || t("outputs.ok"),
  })),
);

function isSecret(field: ProviderField): boolean {
  if (field.secret) return true;
  return field.type === "password";
}

function fieldLabel(field: ProviderField): string {
  return field.label || field.name;
}

function getFieldValue(field: ProviderField): string | number | boolean {
  const value = config[field.name];
  if (value === undefined || value === null) {
    const defaultValue = field.default;
    if (defaultValue !== undefined)
      return defaultValue as string | number | boolean;
    if (field.type === "number") return "";
    if (field.type === "boolean") return false;
    return "";
  }
  if (field.type === "number") {
    if (typeof value === "number") return value;
    const parsed = Number(value);
    return Number.isNaN(parsed) ? "" : parsed;
  }
  return value as string | number | boolean;
}

function getFieldString(field: ProviderField): string {
  const value = getFieldValue(field);
  if (field.type === "boolean") return value ? "true" : "false";
  return String(value);
}

function getFieldOptions(field: ProviderField) {
  if (!field.options) return [];
  return field.options.map((option) => ({
    value: String(option.value),
    label: option.label,
  }));
}

function setFieldValue(field: ProviderField, value: string | number | boolean) {
  if (field.type === "number") {
    if (value === "" || value === null || value === undefined) {
      config[field.name] = "";
      return;
    }
    const parsed = Number(value);
    config[field.name] = Number.isNaN(parsed) ? value : parsed;
  } else {
    config[field.name] = value;
  }
  if (redactedKeys.value.has(field.name) && value !== "<redacted>") {
    redactedKeys.value.delete(field.name);
  }
}

function resetForm(output?: OutputResponse | null) {
  formError.value = null;
  testResult.value = null;
  redactedKeys.value.clear();

  if (output) {
    editingId.value = output.id;
    name.value = output.name;
    providerType.value = output.provider_type;
    enabled.value = output.enabled;
    const cfg = output.config;
    for (const key of Object.keys(config)) {
      delete config[key];
    }
    const p = store.getProvider(output.provider_type);
    for (const field of p?.fields || []) {
      const value = cfg[field.name];
      if (isSecret(field) && value === "<redacted>") {
        redactedKeys.value.add(field.name);
        config[field.name] = "<redacted>";
      } else if (value !== undefined) {
        config[field.name] = value;
      } else if (field.default !== undefined) {
        config[field.name] = field.default;
      } else if (field.type === "boolean") {
        config[field.name] = false;
      } else {
        config[field.name] = "";
      }
    }
    return;
  }

  editingId.value = null;
  name.value = "";
  providerType.value = providerOptions.value[0]?.value || "";
  enabled.value = true;
  for (const key of Object.keys(config)) {
    delete config[key];
  }
  for (const field of fields.value) {
    if (field.default !== undefined) {
      config[field.name] = field.default;
    } else if (field.type === "boolean") {
      config[field.name] = false;
    } else {
      config[field.name] = "";
    }
  }
}

function startNew() {
  resetForm();
  showForm.value = true;
}

function startEdit(output: OutputResponse) {
  resetForm(output);
  showForm.value = true;
}

function cancelEdit() {
  editingId.value = null;
  showForm.value = false;
}

function validateForm(): Record<string, unknown> | null {
  const cfg: Record<string, unknown> = {};
  for (const field of fields.value) {
    const value = config[field.name];
    if (
      field.required &&
      (value === "" || value === undefined || value === null)
    ) {
      formError.value = t("outputs.fieldRequired", {
        field: fieldLabel(field),
      });
      return null;
    }
    cfg[field.name] = value;
  }
  return cfg;
}

async function onSave() {
  formError.value = null;
  const cfg = validateForm();
  if (!cfg) return;

  isSaving.value = true;
  try {
    if (editingId.value) {
      await store.updateOutput(editingId.value, {
        name: name.value,
        config: cfg,
        enabled: enabled.value,
      });
      toast.push({ type: "success", message: t("outputs.saveSuccess") });
      editingId.value = null;
      showForm.value = false;
    } else {
      await store.createOutput({
        provider_type: providerType.value,
        name: name.value,
        config: cfg,
        enabled: enabled.value,
      });
      toast.push({ type: "success", message: t("outputs.createSuccess") });
      resetForm();
      showForm.value = false;
    }
  } catch (err) {
    formError.value =
      err instanceof Error ? err.message : t("outputs.saveError");
  } finally {
    isSaving.value = false;
  }
}

async function onTest() {
  if (!editingId.value) return;
  formError.value = null;
  testResult.value = null;
  isTesting.value = true;
  try {
    const result = await store.validateOutput(editingId.value);
    if (result.ok) {
      testResult.value = t("outputs.testSuccess");
      toast.push({ type: "success", message: testResult.value });
    } else {
      testResult.value = result.error || t("outputs.testFailed");
    }
  } catch (err) {
    testResult.value =
      err instanceof Error ? err.message : t("outputs.testFailed");
  } finally {
    isTesting.value = false;
  }
}

async function toggleEnabled(output: OutputResponse) {
  try {
    await store.updateOutput(output.id, { enabled: !output.enabled });
    toast.push({ type: "success", message: t("outputs.saveSuccess") });
  } catch (err) {
    toast.push({
      type: "error",
      message: t("outputs.saveError", {
        message: err instanceof Error ? err.message : t("errors.unknown"),
      }),
    });
  }
}

async function onDelete(output: OutputResponse) {
  const confirmed = await confirm.open({
    title: t("common.delete"),
    message: t("outputs.deleteConfirm", { name: output.name }),
    danger: true,
    confirmLabel: t("common.delete"),
  });
  if (!confirmed) return;

  try {
    await store.deleteOutput(output.id);
    if (editingId.value === output.id) {
      editingId.value = null;
    }
    toast.push({ type: "success", message: t("outputs.deleteSuccess") });
  } catch (err) {
    toast.push({
      type: "error",
      message: t("outputs.deleteError", {
        message: err instanceof Error ? err.message : t("errors.unknown"),
      }),
    });
  }
}

function onProviderTypeChanged(value: string) {
  providerType.value = value;
  for (const key of Object.keys(config)) {
    delete config[key];
  }
  for (const field of fields.value) {
    if (field.default !== undefined) {
      config[field.name] = field.default;
    } else if (field.type === "boolean") {
      config[field.name] = false;
    } else {
      config[field.name] = "";
    }
  }
}

watch(
  () => store.providers,
  () => {
    if (
      !editingId.value &&
      !providerType.value &&
      providerOptions.value.length
    ) {
      providerType.value = providerOptions.value[0].value;
      onProviderTypeChanged(providerType.value);
    }
  },
  { once: true },
);

onMounted(async () => {
  await store.loadProviders();
  await store.loadOutputs();
  if (!editingId.value && providerOptions.value.length) {
    providerType.value = providerOptions.value[0].value;
    onProviderTypeChanged(providerType.value);
  }
});
</script>

<template>
  <div class="outputs-view">
    <div class="outputs-view__header">
      <AppPageTitle icon="radio">
        {{ t("outputs.title") }}
      </AppPageTitle>
      <AppButton
        v-if="canCreate"
        icon="plus"
        :disabled="showForm"
        @click="startNew"
      >
        {{ t("outputs.new") }}
      </AppButton>
    </div>

    <div
      v-if="store.loading && !store.outputs.length"
      class="outputs-view__skeleton"
    >
      <SkeletonLoader variant="page" />
    </div>

    <div v-else-if="store.error" class="outputs-view__error" role="alert">
      <span>{{ store.error }}</span>
      <AppButton size="sm" icon="rotate-right" @click="store.loadOutputs">
        {{ t("common.retry") }}
      </AppButton>
    </div>

    <template v-else>
      <p v-if="!canCreate" class="outputs-view__empty">
        {{ t("outputs.noProviders") }}
      </p>

      <template v-if="showForm">
        <form class="outputs-view__form" @submit.prevent="onSave">
          <h2 class="outputs-view__form-title">
            {{ editingId ? t("outputs.editTitle") : t("outputs.newTitle") }}
          </h2>

          <AppInput
            v-model="name"
            :label="t('outputs.name')"
            required
            :disabled="isSaving"
          />

          <AppSelect
            v-if="!editingId"
            v-model="providerType"
            :options="providerOptions"
            :label="t('outputs.provider')"
            :disabled="isSaving"
            @update:model-value="onProviderTypeChanged"
          />

          <AppCheckbox
            v-model="enabled"
            :label="t('outputs.enabled')"
            :disabled="isSaving"
          />

          <div
            v-for="field in fields"
            :key="field.name"
            class="outputs-view__field"
          >
            <AppInput
              v-if="
                field.type === 'text' ||
                field.type === 'password' ||
                field.type === 'number'
              "
              :model-value="getFieldString(field)"
              :label="fieldLabel(field)"
              :type="
                field.type === 'password'
                  ? 'password'
                  : field.type === 'number'
                    ? 'number'
                    : 'text'
              "
              :required="field.required"
              :hint="field.help"
              :disabled="isSaving"
              @update:model-value="(value) => setFieldValue(field, value)"
            />

            <AppSelect
              v-else-if="field.type === 'select'"
              :model-value="getFieldString(field)"
              :options="getFieldOptions(field)"
              :label="fieldLabel(field)"
              :required="field.required"
              :hint="field.help"
              :disabled="isSaving"
              @update:model-value="(value) => setFieldValue(field, value)"
            />

            <AppCheckbox
              v-else-if="field.type === 'boolean'"
              :model-value="Boolean(getFieldValue(field))"
              :label="fieldLabel(field)"
              :hint="field.help"
              :disabled="isSaving"
              @update:model-value="(value) => setFieldValue(field, value)"
            />

            <AppInput
              v-else
              :model-value="getFieldString(field)"
              :label="fieldLabel(field)"
              :required="field.required"
              :hint="field.help"
              :disabled="isSaving"
              @update:model-value="(value) => setFieldValue(field, value)"
            />
          </div>

          <div v-if="formError" class="outputs-view__form-error" role="alert">
            {{ formError }}
          </div>
          <div v-else-if="testResult" class="outputs-view__test-result">
            {{ testResult }}
          </div>

          <div class="outputs-view__form-actions">
            <AppButton
              type="submit"
              variant="primary"
              :loading="isSaving"
              :disabled="isSaving"
            >
              {{ t("common.save") }}
            </AppButton>
            <AppButton
              v-if="editingId"
              type="button"
              icon="check"
              :loading="isTesting"
              :disabled="isTesting || isSaving"
              @click="onTest"
            >
              {{ t("outputs.test") }}
            </AppButton>
            <AppButton
              type="button"
              variant="ghost"
              :disabled="isSaving"
              @click="cancelEdit"
            >
              {{ t("common.cancel") }}
            </AppButton>
          </div>
        </form>
      </template>

      <AppTable
        v-else
        :columns="columns"
        :rows="rows"
        :row-key="(row) => String(row.id)"
        :loading="store.loading"
        :empty-label="t('outputs.noOutputs')"
      >
        <template #row-stream_url="{ value }">
          <a
            v-if="value"
            :href="String(value)"
            class="outputs-view__stream-url"
            target="_blank"
            rel="noopener"
            >{{ String(value) }}</a
          >
          <span v-else class="outputs-view__no-url">—</span>
        </template>

        <template #row-enabled="{ row, value }">
          <AppCheckbox
            :model-value="Boolean(value)"
            :label="t('outputs.enabled')"
            @update:model-value="
              toggleEnabled(store.getOutput(String(row.id)) as OutputResponse)
            "
          />
        </template>

        <template #row-status="{ row }">
          <span
            v-if="store.getOutput(String(row.id))?.last_error"
            class="outputs-view__error-text"
          >
            {{ store.getOutput(String(row.id))?.last_error }}
          </span>
          <span v-else>{{ t("outputs.ok") }}</span>
        </template>

        <template #row-actions="{ row }">
          <div class="outputs-view__actions">
            <AppButton
              variant="ghost"
              size="sm"
              icon="pen-to-square"
              :aria-label="t('common.edit')"
              @click="
                startEdit(store.getOutput(String(row.id)) as OutputResponse)
              "
            />
            <AppButton
              variant="ghost"
              size="sm"
              icon="trash"
              :aria-label="t('common.delete')"
              @click="
                onDelete(store.getOutput(String(row.id)) as OutputResponse)
              "
            />
          </div>
        </template>
      </AppTable>
    </template>
  </div>
</template>

<style scoped>
.outputs-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  max-width: 64rem;
}

.outputs-view__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.outputs-view__skeleton {
  min-height: 16rem;
}

.outputs-view__error,
.outputs-view__empty {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-danger);
}

.outputs-view__empty {
  color: var(--color-text-muted);
}

.outputs-view__form {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
}

.outputs-view__form-title {
  margin: 0;
  font-size: 1.125rem;
  color: var(--color-text);
}

.outputs-view__field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.outputs-view__form-error {
  color: var(--color-danger);
  font-size: 0.875rem;
}

.outputs-view__test-result {
  color: var(--color-text);
  font-size: 0.875rem;
}

.outputs-view__form-actions {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.outputs-view__actions {
  display: flex;
  justify-content: center;
  gap: var(--space-1);
}

.outputs-view__error-text {
  color: var(--color-danger);
  font-size: 0.875rem;
}

.outputs-view__stream-url {
  font-size: 0.875rem;
  word-break: break-all;
}

.outputs-view__no-url {
  color: var(--color-text-muted);
}
</style>
