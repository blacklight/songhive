import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import ImageUploadField from "./ImageUploadField.vue";

function getFileInput(wrapper: ReturnType<typeof mount>) {
  return wrapper.find('input[type="file"]').element as HTMLInputElement;
}

describe("ImageUploadField", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("renders an upload button and no remove button when no image is set", () => {
    const wrapper = mount(ImageUploadField, {
      props: {
        label: "Cover",
        imageUrl: null,
      },
      attachTo: document.body,
    });

    expect(wrapper.text()).toContain("Cover");
    expect(wrapper.text()).toContain("Upload image");
    expect(wrapper.text()).not.toContain("Remove image");
  });

  it("renders a remove button when an image is set", () => {
    const wrapper = mount(ImageUploadField, {
      props: {
        imageUrl: "https://example.com/cover.jpg",
      },
      attachTo: document.body,
    });

    expect(wrapper.text()).toContain("Remove image");
  });

  it("emits the selected file when a file is chosen", async () => {
    const wrapper = mount(ImageUploadField, {
      props: { imageUrl: null },
      attachTo: document.body,
    });

    const file = new File(["image"], "cover.jpg", { type: "image/jpeg" });
    const input = getFileInput(wrapper);
    Object.defineProperty(input, "files", {
      value: [file],
      configurable: true,
    });
    input.dispatchEvent(new Event("change"));
    await flushPromises();

    expect(wrapper.emitted("upload")?.[0]).toEqual([file]);
  });

  it("emits remove when the remove button is clicked", async () => {
    const wrapper = mount(ImageUploadField, {
      props: { imageUrl: "https://example.com/cover.jpg" },
      attachTo: document.body,
    });

    const removeButton = Array.from(
      document.body.querySelectorAll("button"),
    ).find((b) => b.textContent?.includes("Remove image"));
    expect(removeButton).toBeDefined();
    removeButton?.click();
    await flushPromises();

    expect(wrapper.emitted("remove")).toHaveLength(1);
  });

  it("displays an error message when an error is provided", () => {
    const wrapper = mount(ImageUploadField, {
      props: {
        imageUrl: null,
        error: "Upload failed",
      },
      attachTo: document.body,
    });

    expect(wrapper.text()).toContain("Upload failed");
  });
});
