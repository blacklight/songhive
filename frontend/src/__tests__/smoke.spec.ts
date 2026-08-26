import { describe, it, expect } from "vitest";

describe("smoke", () => {
  it("can import core modules and components", () => {
    expect(async () => {
      await import("@/api/client");
      await import("@/stores/auth");
      await import("@/stores/theme");
      await import("@/components/feedback/AppToast.vue");
      await import("@/components/ui/AppButton.vue");
      await import("@/components/player/PlayerBar.vue");
      await import("@/components/player/PlayerBarSlot.vue");
    }).not.toThrow();
  });
});
