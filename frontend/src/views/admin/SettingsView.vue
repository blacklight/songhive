<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { getApiErrorMessage } from "@/api/client";
import { listSettings, updateSetting, type SettingResponse } from "@/api/admin";
import { useToastStore } from "@/stores/toast";
import AppButton from "@/components/ui/AppButton.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppPageTitle from "@/components/ui/AppPageTitle.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import SkeletonLoader from "@/components/feedback/SkeletonLoader.vue";

const { t } = useI18n();
const toastStore = useToastStore();

const settings = ref<SettingResponse[]>([]);
const models = ref<Record<string, unknown>>({});
const dirtyKeys = ref<Set<string>>(new Set());
const loading = ref(false);
const saving = ref(false);

const registrationOptions = [
  { value: "open", label: t("pages.admin.settings.registrationOptions.open") },
  {
    value: "invite-only",
    label: t("pages.admin.settings.registrationOptions.invite-only"),
  },
  {
    value: "approval-required",
    label: t("pages.admin.settings.registrationOptions.approval-required"),
  },
  {
    value: "closed",
    label: t("pages.admin.settings.registrationOptions.closed"),
  },
];

function getType(setting: SettingResponse): string {
  if (setting.type) return setting.type;
  if (setting.key === "federation_enabled") return "boolean";
  if (setting.key === "registration_mode") return "string";
  if (
    setting.key === "instance_name" ||
    setting.key === "instance_description"
  ) {
    return "string";
  }
  return "string";
}

function getControl(
  setting: SettingResponse,
): "text" | "textarea" | "select" | "checkbox" | "number" | "json" {
  if (setting.key === "instance_name") return "text";
  if (setting.key === "instance_description") return "textarea";
  if (setting.key === "registration_mode") return "select";
  if (setting.key === "federation_enabled") return "checkbox";

  const type = getType(setting);
  if (type === "boolean") return "checkbox";
  if (type === "number") return "number";
  if (type === "json") return "json";
  return "text";
}

function initialModelValue(setting: SettingResponse): unknown {
  if (setting.value === null || setting.value === undefined) {
    const control = getControl(setting);
    if (control === "checkbox") return false;
    if (control === "number") return "";
    return "";
  }
  const type = getType(setting);
  if (type === "json" && typeof setting.value === "object") {
    return JSON.stringify(setting.value, null, 2);
  }
  return setting.value;
}

function getLabel(setting: SettingResponse): string {
  if (setting.key === "instance_name")
    return t("pages.admin.settings.instanceName");
  if (setting.key === "instance_description")
    return t("pages.admin.settings.instanceDescription");
  if (setting.key === "registration_mode")
    return t("pages.admin.settings.registrationMode");
  if (setting.key === "federation_enabled")
    return t("pages.admin.settings.federationEnabled");
  return setting.key;
}

function getTypeLabel(setting: SettingResponse): string {
  const control = getControl(setting);
  switch (control) {
    case "checkbox":
      return t("pages.admin.settings.typeBoolean");
    case "number":
      return t("pages.admin.settings.typeNumber");
    case "json":
      return t("pages.admin.settings.typeJson");
    default:
      return t("pages.admin.settings.typeString");
  }
}

function onChange(key: string, value: unknown) {
  models.value[key] = value;
  dirtyKeys.value.add(key);
  dirtyKeys.value = new Set(dirtyKeys.value);
}

function coerceValue(value: unknown, setting: SettingResponse): unknown {
  const control = getControl(setting);
  switch (control) {
    case "checkbox":
      return Boolean(value);
    case "number": {
      if (typeof value === "number") return value;
      const num = Number(value);
      return Number.isNaN(num) ? null : num;
    }
    case "json": {
      if (typeof value === "string") {
        try {
          return JSON.parse(value);
        } catch {
          return value;
        }
      }
      return value;
    }
    default:
      return String(value ?? "");
  }
}

async function loadSettings() {
  loading.value = true;
  try {
    const result = await listSettings();
    settings.value = result;
    models.value = Object.fromEntries(
      result.map((s) => [s.key, initialModelValue(s)]),
    );
    dirtyKeys.value.clear();
    dirtyKeys.value = new Set();
  } catch (err) {
    toastStore.push({
      type: "error",
      message: t("pages.admin.settings.saveError", {
        message: getApiErrorMessage(err) || t("errors.unknown"),
      }),
    });
  } finally {
    loading.value = false;
  }
}

async function onSave() {
  if (dirtyKeys.value.size === 0 || saving.value) return;

  saving.value = true;
  const keys = [...dirtyKeys.value];
  const results = await Promise.all(
    keys.map(async (key) => {
      const setting = settings.value.find((s) => s.key === key);
      if (!setting) return { key, ok: false, err: null };
      const coerced = coerceValue(models.value[key], setting);
      try {
        await updateSetting(key, coerced);
        return { key, ok: true, err: null };
      } catch (err) {
        return { key, ok: false, err };
      }
    }),
  );

  const failed = results.filter((r) => !r.ok);
  if (failed.length > 0) {
    const first = failed[0].err;
    toastStore.push({
      type: "error",
      message: t("pages.admin.settings.saveError", {
        message: getApiErrorMessage(first) || t("errors.unknown"),
      }),
    });
  } else {
    toastStore.push({
      type: "success",
      message: t("pages.admin.settings.saved"),
    });
  }

  dirtyKeys.value.clear();
  dirtyKeys.value = new Set();
  await loadSettings();
  saving.value = false;
}

onMounted(() => loadSettings());
</script>

<template>
  <div class="settings-view">
    <header class="settings-view__header">
      <AppPageTitle icon="gear">{{
        t("pages.admin.settings.title")
      }}</AppPageTitle>
      <AppButton
        :loading="saving"
        :disabled="saving || dirtyKeys.size === 0"
        icon="floppy-disk"
        @click="onSave"
      >
        {{ t("pages.admin.settings.save") }}
      </AppButton>
    </header>

    <div v-if="loading" class="settings-view__skeleton">
      <SkeletonLoader variant="page" />
    </div>

    <form v-else class="settings-view__form" @submit.prevent="onSave">
      <div
        v-for="setting in settings"
        :key="setting.key"
        class="settings-view__field"
      >
        <div class="settings-view__label-row">
          <label :for="`setting-${setting.key}`" class="settings-view__key">
            {{ getLabel(setting) }}
          </label>
          <span class="settings-view__type">{{ getTypeLabel(setting) }}</span>
        </div>

        <AppInput
          v-if="getControl(setting) === 'text'"
          :id="`setting-${setting.key}`"
          :model-value="models[setting.key] as string"
          @update:model-value="onChange(setting.key, $event)"
        />

        <AppInput
          v-else-if="getControl(setting) === 'textarea'"
          :id="`setting-${setting.key}`"
          :model-value="models[setting.key] as string"
          as="textarea"
          @update:model-value="onChange(setting.key, $event)"
        />

        <AppInput
          v-else-if="getControl(setting) === 'json'"
          :id="`setting-${setting.key}`"
          :model-value="models[setting.key] as string"
          as="textarea"
          :rows="6"
          @update:model-value="onChange(setting.key, $event)"
        />

        <AppInput
          v-else-if="getControl(setting) === 'number'"
          :id="`setting-${setting.key}`"
          :model-value="models[setting.key] as number"
          type="number"
          @update:model-value="onChange(setting.key, $event)"
        />

        <AppSelect
          v-else-if="getControl(setting) === 'select'"
          :id="`setting-${setting.key}`"
          :model-value="models[setting.key] as string"
          :options="registrationOptions"
          @update:model-value="onChange(setting.key, $event)"
        />

        <AppCheckbox
          v-else-if="getControl(setting) === 'checkbox'"
          :id="`setting-${setting.key}`"
          :model-value="models[setting.key] as boolean"
          :label="getLabel(setting)"
          @update:model-value="onChange(setting.key, $event)"
        />
      </div>
    </form>
  </div>
</template>

<style scoped>
.settings-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.settings-view__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
}

.settings-view__skeleton {
  padding: var(--space-4);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
}

.settings-view__form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  max-width: 40rem;
}

.settings-view__field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
}

.settings-view__label-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.settings-view__key {
  font-weight: 600;
  color: var(--color-text);
}

.settings-view__type {
  font-size: 0.875rem;
  color: var(--color-text-muted);
}
</style>
