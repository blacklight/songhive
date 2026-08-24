export function formatTime(seconds: number): string {
  const s = Math.floor(seconds % 60);
  const m = Math.floor((seconds / 60) % 60);
  const h = Math.floor(seconds / 3600);
  const mm = h > 0 ? `${h}:${m.toString().padStart(2, "0")}` : String(m);
  return `${mm}:${s.toString().padStart(2, "0")}`;
}
