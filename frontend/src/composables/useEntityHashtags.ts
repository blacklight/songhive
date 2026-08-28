import { ref } from "vue";
import { addHashtags, removeHashtag, type EntityType } from "@/api/hashtags";

export function useEntityHashtags() {
  const hashtags = ref<string[]>([]);
  const originalHashtags = ref<string[]>([]);

  function resetHashtags(initial: string[] | null | undefined) {
    hashtags.value = initial ?? [];
    originalHashtags.value = [...hashtags.value];
  }

  async function syncHashtags(type: EntityType, id: string) {
    const original = new Set(originalHashtags.value);
    const current = new Set(hashtags.value);

    const toAdd = hashtags.value.filter((h) => !original.has(h));
    const toRemove = originalHashtags.value.filter((h) => !current.has(h));

    if (toAdd.length > 0) {
      await addHashtags(type, id, { hashtags: toAdd });
    }

    for (const hashtag of toRemove) {
      await removeHashtag(type, id, hashtag);
    }

    originalHashtags.value = [...hashtags.value];
  }

  return {
    hashtags,
    originalHashtags,
    resetHashtags,
    syncHashtags,
  };
}
