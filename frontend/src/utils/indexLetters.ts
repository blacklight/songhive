/**
 * A–Z index-bar helpers for the browse grids. Items whose sort key does
 * not start with a Latin letter are bucketed under ``#``, matching the
 * alphabet pickers in comparable music libraries (Jellyfin, Finamp,
 * Substreamer).
 */
export const OTHER_INDEX_LETTER = "#";

export const INDEX_LETTERS: readonly string[] = [
  OTHER_INDEX_LETTER,
  ..."ABCDEFGHIJKLMNOPQRSTUVWXYZ",
];

/**
 * Bucket a sort key under its index letter. Accented letters fold onto
 * their base (``É`` → ``E``); digits, symbols, non-Latin scripts and
 * empty keys all land on ``#``.
 */
export function indexLetterOf(key: string | null | undefined): string {
  const letter = (key?.trim().charAt(0) ?? "")
    .normalize("NFD")
    .replace(/\p{M}/gu, "")
    .toUpperCase();
  return letter >= "A" && letter <= "Z" ? letter : OTHER_INDEX_LETTER;
}
