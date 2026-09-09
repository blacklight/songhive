<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import AppInput from "@/components/ui/AppInput.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import GenreInput from "@/components/genres/GenreInput.vue";
import TagInput from "@/components/tags/TagInput.vue";

export interface Props {
  bulk?: boolean;
  canRenameFile?: boolean;
  disabled?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  bulk: false,
  canRenameFile: true,
  disabled: false,
});

const title = defineModel<string>("title", { required: true });
const artistName = defineModel<string>("artistName", { required: true });
const albumTitle = defineModel<string>("albumTitle", { required: true });
const genres = defineModel<string[]>("genres", { required: true });
const trackNumber = defineModel<string>("trackNumber", { required: true });
const discNumber = defineModel<string>("discNumber", { required: true });
const releaseYear = defineModel<string>("releaseYear", { required: true });
const filename = defineModel<string>("filename", { required: true });
const visibility = defineModel<string>("visibility", { required: true });
const tags = defineModel<string[]>("tags", { required: true });
const description = defineModel<string>("description", { default: "" });

const emit = defineEmits<{ submit: [] }>();

const { t } = useI18n();

const visibilityOptions = computed(() => {
  const options = [
    { value: "private", label: t("browse.visibility.private") },
    { value: "local", label: t("browse.visibility.local") },
    { value: "public", label: t("browse.visibility.public") },
  ];
  if (props.bulk) {
    return [
      { value: "", label: t("browse.bulkEdit.keepUnchanged") },
      ...options,
    ];
  }
  return options;
});
</script>

<template>
  <form class="track-metadata-form" @submit.prevent="emit('submit')">
    <AppInput
      v-model="title"
      :label="t('browse.edit.title')"
      :required="!props.bulk"
      :disabled="props.disabled"
    />
    <AppInput
      v-model="artistName"
      :label="t('browse.edit.artist')"
      :required="!props.bulk"
      :disabled="props.disabled"
    />
    <AppInput
      v-model="albumTitle"
      :label="t('browse.edit.album')"
      :disabled="props.disabled"
    />
    <GenreInput
      v-model="genres"
      :placeholder="t('genres.placeholder')"
      :aria-label="t('genres.ariaLabel')"
    />
    <div class="track-metadata-form__row">
      <AppInput
        v-model="trackNumber"
        type="number"
        :label="t('browse.detail.trackNumber')"
        :disabled="props.disabled"
      />
      <AppInput
        v-model="discNumber"
        type="number"
        :label="t('browse.detail.discNumber')"
        :disabled="props.disabled"
      />
    </div>
    <AppInput
      v-model="releaseYear"
      type="number"
      :label="t('browse.edit.releaseYear')"
      :disabled="props.disabled"
    />
    <AppInput
      v-model="filename"
      :label="t('browse.edit.filename')"
      :disabled="props.disabled || !props.canRenameFile"
    />
    <AppInput
      v-if="!props.bulk"
      v-model="description"
      as="textarea"
      :label="t('browse.edit.description')"
      :disabled="props.disabled"
    />
    <AppSelect
      v-model="visibility"
      :label="t('browse.detail.visibility')"
      :options="visibilityOptions"
      :disabled="props.disabled"
    />

    <TagInput
      v-model="tags"
      :placeholder="t('tags.placeholder')"
      :aria-label="t('tags.label')"
    />

    <div v-if="$slots.default" class="track-metadata-form__actions">
      <slot />
    </div>
  </form>
</template>

<style scoped>
.track-metadata-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.track-metadata-form__row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
}

.track-metadata-form__actions {
  display: flex;
  gap: var(--space-3);
  align-items: center;
  flex-wrap: wrap;
}
</style>
