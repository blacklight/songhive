import { ref } from "vue";
import { addTags, removeTag, type EntityType } from "@/api/tags";

export function useEntityTags() {
  const tags = ref<string[]>([]);
  const originalTags = ref<string[]>([]);

  function resetTags(initial: string[] | null | undefined) {
    tags.value = initial ?? [];
    originalTags.value = [...tags.value];
  }

  async function syncTags(type: EntityType, id: string) {
    const original = new Set(originalTags.value);
    const current = new Set(tags.value);

    const toAdd = tags.value.filter((h) => !original.has(h));
    const toRemove = originalTags.value.filter((h) => !current.has(h));

    if (toAdd.length > 0) {
      await addTags(type, id, { tags: toAdd });
    }

    for (const tag of toRemove) {
      await removeTag(type, id, tag);
    }

    originalTags.value = [...tags.value];
  }

  return {
    tags,
    originalTags,
    resetTags,
    syncTags,
  };
}
