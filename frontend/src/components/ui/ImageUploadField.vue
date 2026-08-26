<script setup lang="ts">
import { ref } from "vue";
import AppButton from "./AppButton.vue";

export interface Props {
  label?: string;
  imageUrl?: string | null;
  uploadLabel?: string;
  removeLabel?: string;
  accept?: string;
  loading?: boolean;
  removing?: boolean;
  error?: string;
  disabled?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  uploadLabel: "Upload image",
  removeLabel: "Remove image",
  accept: "image/*",
  loading: false,
  removing: false,
  error: undefined,
  disabled: false,
});

const emit = defineEmits<{
  upload: [file: File];
  remove: [];
}>();

const fileInput = ref<HTMLInputElement | null>(null);

function onFileChange(event: Event) {
  const target = event.target as HTMLInputElement;
  const file = target.files?.[0];
  if (!file) return;

  emit("upload", file);
  if (target) target.value = "";
}

function onRemove() {
  emit("remove");
  if (fileInput.value) fileInput.value.value = "";
}

function chooseFile() {
  fileInput.value?.click();
}
</script>

<template>
  <div class="image-upload-field">
    <span v-if="props.label" class="image-upload-field__label">
      {{ props.label }}
    </span>

    <div class="image-upload-field__preview">
      <img
        v-if="props.imageUrl"
        :src="props.imageUrl"
        alt=""
        class="image-upload-field__image"
      />
      <span v-else class="image-upload-field__placeholder">
        {{ "No image set" }}
      </span>
    </div>

    <div class="image-upload-field__actions">
      <input
        ref="fileInput"
        type="file"
        :accept="props.accept"
        class="image-upload-field__file-input"
        @change="onFileChange"
      />
      <AppButton
        variant="secondary"
        icon="upload"
        :loading="props.loading"
        :disabled="props.disabled"
        @click="chooseFile"
      >
        {{ props.uploadLabel }}
      </AppButton>
      <AppButton
        v-if="props.imageUrl"
        variant="danger"
        icon="trash-can"
        :loading="props.removing"
        :disabled="props.disabled || props.loading || props.removing"
        @click="onRemove"
      >
        {{ props.removeLabel }}
      </AppButton>
    </div>

    <p v-if="props.error" class="image-upload-field__error" role="alert">
      {{ props.error }}
    </p>
  </div>
</template>

<style scoped>
.image-upload-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.image-upload-field__label {
  color: var(--color-text);
  font-size: 0.875rem;
  font-weight: 500;
}

.image-upload-field__preview {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 8rem;
  height: 8rem;
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  overflow: hidden;
}

.image-upload-field__image {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.image-upload-field__placeholder {
  color: var(--color-text-muted);
  font-size: 0.875rem;
  text-align: center;
  padding: var(--space-2);
}

.image-upload-field__actions {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  flex-wrap: wrap;
}

.image-upload-field__file-input {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.image-upload-field__error {
  margin: 0;
  color: var(--color-danger);
  font-size: 0.875rem;
}
</style>
