import { ref } from "vue";
import { addGenres, type GenreEntityType } from "@/api/genres";

export function useEntityGenres() {
  const genres = ref<string[]>([]);
  const originalGenres = ref<string[]>([]);

  function resetGenres(initial: string[] | null | undefined) {
    genres.value = initial ?? [];
    originalGenres.value = [...genres.value];
  }

  async function syncGenres(type: GenreEntityType, id: string) {
    const original = new Set(originalGenres.value);

    const changed =
      genres.value.length !== originalGenres.value.length ||
      genres.value.some((g) => !original.has(g));

    if (changed) {
      await addGenres(type, id, { genres: genres.value });
    }

    originalGenres.value = [...genres.value];
  }

  return {
    genres,
    originalGenres,
    resetGenres,
    syncGenres,
  };
}
