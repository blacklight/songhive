import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { i18n } from "@/i18n";
import * as externalLibrariesApi from "@/api/externalLibraries";
import ExternalDuplicateModal from "./ExternalDuplicateModal.vue";

vi.mock("@/api/externalLibraries", () => ({
  resolveUploadDuplicate: vi.fn(),
}));

describe("ExternalDuplicateModal", () => {
  const warning = {
    token: "token-1",
    sha256: "abc123",
    provider_type: "s3",
    display_info: [{ title: "Existing Track", artist: "Artist" }],
  };

  let wrapper: ReturnType<typeof mount>;

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    wrapper?.unmount();
    document.body.innerHTML = "";
  });

  it("does not render when closed", () => {
    wrapper = mount(ExternalDuplicateModal, {
      props: { open: false, warning: null },
      global: { plugins: [i18n] },
    });
    expect(document.body.querySelector(".app-modal")).toBeNull();
  });

  it("renders the duplicate warning and resolves on keep", async () => {
    vi.mocked(externalLibrariesApi.resolveUploadDuplicate).mockResolvedValue({
      id: "track-1",
      title: "Existing Track",
    });

    wrapper = mount(ExternalDuplicateModal, {
      attachTo: document.body,
      props: { open: true, warning },
      global: { plugins: [i18n] },
    });
    await flushPromises();

    expect(document.body.textContent).toContain("Existing Track");

    const keepButton = document.body.querySelector(
      "[data-testid=keep-local-button]",
    ) as HTMLButtonElement;
    expect(keepButton).not.toBeNull();
    await keepButton.click();
    await flushPromises();

    expect(externalLibrariesApi.resolveUploadDuplicate).toHaveBeenCalledWith(
      "token-1",
      "keep_local",
    );
    expect(wrapper.emitted("resolved")).toHaveLength(1);
  });

  it("resolves with discard when the discard button is clicked", async () => {
    vi.mocked(externalLibrariesApi.resolveUploadDuplicate).mockResolvedValue({
      id: "file-1",
    });

    wrapper = mount(ExternalDuplicateModal, {
      attachTo: document.body,
      props: { open: true, warning },
      global: { plugins: [i18n] },
    });
    await flushPromises();

    const discardButton = document.body.querySelector(
      "[data-testid=discard-upload-button]",
    ) as HTMLButtonElement;
    expect(discardButton).not.toBeNull();
    await discardButton.click();
    await flushPromises();

    expect(externalLibrariesApi.resolveUploadDuplicate).toHaveBeenCalledWith(
      "token-1",
      "discard_upload",
    );
    expect(wrapper.emitted("resolved")).toHaveLength(1);
  });
});
