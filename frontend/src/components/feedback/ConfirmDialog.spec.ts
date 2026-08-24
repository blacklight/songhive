import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { defineComponent, ref, nextTick } from "vue";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import ConfirmDialog from "./ConfirmDialog.vue";
import { useConfirm } from "@/composables/useConfirm";

const Harness = defineComponent({
  components: { ConfirmDialog },
  setup() {
    const { confirm } = useConfirm();
    const result = ref<string>("none");

    async function ask() {
      result.value = String(await confirm({ message: "Are you sure?" }));
    }

    return { result, ask };
  },
  template: `
    <button class="ask" @click="ask">Ask</button>
    <span class="result">{{ result }}</span>
    <ConfirmDialog />
  `,
});

describe("ConfirmDialog", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    document.body.innerHTML = "";
  });

  afterEach(() => {
    document.body.innerHTML = "";
    document.body.style.overflow = "";
  });

  async function openDialog(wrapper: ReturnType<typeof mount>) {
    await wrapper.find(".ask").trigger("click");
    await flushPromises();
    await nextTick();
  }

  it("resolves true when the confirm button is clicked", async () => {
    const wrapper = mount(Harness, { attachTo: document.body });
    await openDialog(wrapper);

    const confirmButton = document.body.querySelector(
      ".app-modal__actions button:last-child",
    ) as HTMLElement | null;
    expect(confirmButton?.textContent?.trim()).toBe("Confirm");
    confirmButton?.click();

    await flushPromises();
    await nextTick();
    expect(wrapper.find(".result").text()).toBe("true");
    wrapper.unmount();
  });

  it("resolves false when the cancel button is clicked", async () => {
    const wrapper = mount(Harness, { attachTo: document.body });
    await openDialog(wrapper);

    const cancelButton = document.body.querySelector(
      ".app-modal__actions button:first-child",
    ) as HTMLElement | null;
    expect(cancelButton?.textContent?.trim()).toBe("Cancel");
    cancelButton?.click();

    await flushPromises();
    await nextTick();
    expect(wrapper.find(".result").text()).toBe("false");
    wrapper.unmount();
  });

  it("resolves false when the modal close button is clicked", async () => {
    const wrapper = mount(Harness, { attachTo: document.body });
    await openDialog(wrapper);

    const closeButton = document.body.querySelector(
      ".app-modal__close",
    ) as HTMLElement | null;
    closeButton?.click();

    await flushPromises();
    await nextTick();
    expect(wrapper.find(".result").text()).toBe("false");
    wrapper.unmount();
  });
});
