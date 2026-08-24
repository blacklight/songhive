export function useDebounce<TArgs extends unknown[]>(
  fn: (...args: TArgs) => void,
  delay: number,
): { (...args: TArgs): void; cancel: () => void } {
  let timer: ReturnType<typeof setTimeout> | null = null;

  function debounced(...args: TArgs) {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      fn(...args);
    }, delay);
  }

  debounced.cancel = () => {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
  };

  return debounced;
}
