<script setup lang="ts">
import { ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import type { ActivityResponse, ActivityVisibility } from "@/api/activities";
import { getApiErrorMessage } from "@/api/client";
import { useActivitiesStore } from "@/stores/activities";
import AppModal from "@/components/feedback/AppModal.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppSelect from "@/components/ui/AppSelect.vue";

const props = defineProps<{ open: boolean; activity: ActivityResponse }>();
const emit = defineEmits<{ close: [] }>();

const { t } = useI18n();
const store = useActivitiesStore();

const content = ref("");
const visibility = ref<ActivityVisibility>("public");
const saving = ref(false);
const error = ref("");

const VISIBILITY_VALUES: ActivityVisibility[] = [
  "public",
  "followers",
  "mentioned",
  "local",
  "private",
];

const visibilityOptions = VISIBILITY_VALUES.map((value) => ({
  value,
  label: t(`activities.visibility.${value}`),
}));

watch(
  () => props.open,
  (open) => {
    if (!open) return;
    content.value = props.activity.content_source ?? "";
    visibility.value = props.activity.visibility;
    error.value = "";
  },
);

async function save() {
  saving.value = true;
  error.value = "";
  try {
    await store.update(props.activity.id, {
      content: content.value,
      visibility: visibility.value,
    });
    emit("close");
  } catch (err) {
    error.value = getApiErrorMessage(err) || t("activities.edit.error");
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <AppModal
    :open="open"
    :title="t('activities.edit.title')"
    @close="emit('close')"
  >
    <form class="activity-edit" @submit.prevent="save">
      <label class="activity-edit__label" for="activity-edit-content">
        {{ t("activities.edit.content") }}
      </label>
      <textarea
        id="activity-edit-content"
        v-model="content"
        class="activity-edit__textarea"
        rows="6"
        :disabled="saving"
      />

      <AppSelect
        v-model="visibility"
        :options="visibilityOptions"
        :label="t('activities.edit.visibility')"
        :disabled="saving"
      />

      <p v-if="error" class="activity-edit__error" role="alert">{{ error }}</p>

      <div class="activity-edit__actions">
        <AppButton
          variant="secondary"
          :disabled="saving"
          @click="emit('close')"
        >
          {{ t("common.cancel") }}
        </AppButton>
        <AppButton type="submit" :loading="saving">
          {{ t("common.save") }}
        </AppButton>
      </div>
    </form>
  </AppModal>
</template>

<style scoped>
.activity-edit {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  min-width: min(28rem, 80vw);
}

.activity-edit__label {
  font-weight: 600;
}

.activity-edit__textarea {
  width: calc(100% - var(--space-5));
  padding: var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-background);
  color: var(--color-text);
  font-family: inherit;
  font-size: 1rem;
  resize: vertical;
}

.activity-edit__error {
  margin: 0;
  color: var(--color-danger);
}

.activity-edit__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}
</style>
