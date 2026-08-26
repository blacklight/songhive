import { describe, it, expect, vi, beforeEach } from "vitest";
import { useDebounce } from "./useDebounce";

describe("useDebounce", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it("fires once after delay", () => {
    const fn = vi.fn();
    const debounced = useDebounce(fn, 100);
    debounced("a");
    debounced("b");
    debounced("c");
    expect(fn).not.toHaveBeenCalled();
    vi.advanceTimersByTime(100);
    expect(fn).toHaveBeenCalledTimes(1);
    expect(fn).toHaveBeenCalledWith("c");
  });

  it("cancel suppresses pending call", () => {
    const fn = vi.fn();
    const debounced = useDebounce(fn, 100);
    debounced("x");
    debounced.cancel();
    vi.advanceTimersByTime(100);
    expect(fn).not.toHaveBeenCalled();
  });
});
