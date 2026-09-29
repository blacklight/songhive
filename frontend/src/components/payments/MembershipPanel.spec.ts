import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { i18n } from "@/i18n";
import * as paymentsApi from "@/api/payments";
import * as usersApi from "@/api/users";
import { useAuthStore } from "@/stores/auth";
import MembershipPanel from "./MembershipPanel.vue";

vi.mock("@/api/payments", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/payments")>();
  return {
    ...actual,
    membershipQuote: vi.fn(),
    membershipStatus: vi.fn(),
    membershipCheckout: vi.fn(),
    membershipPortal: vi.fn(),
    membershipCancel: vi.fn(),
    mintMembershipSession: vi.fn(),
  };
});

vi.mock("@/api/users", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/users")>()),
  getMe: vi.fn(),
}));

const quote = { price_minor: 500, currency: "usd", interval: "month" };

const activeStatus = {
  status: "active",
  paid_through: "2026-10-29T00:00:00+00:00",
  cancel_at_period_end: false,
  payments_required: true,
  is_active: true,
  email_verified: true,
};

const paidUnverifiedStatus = {
  status: "active",
  paid_through: "2026-10-29T00:00:00+00:00",
  cancel_at_period_end: false,
  payments_required: true,
  is_active: false,
  email_verified: false,
};

const pendingStatus = {
  status: "incomplete",
  paid_through: null,
  cancel_at_period_end: false,
  payments_required: true,
  is_active: false,
  email_verified: true,
};

function mountPanel(props = {}) {
  return mount(MembershipPanel, {
    props,
    global: { plugins: [i18n] },
  });
}

describe("MembershipPanel", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(paymentsApi.membershipQuote).mockResolvedValue(quote);
    vi.mocked(paymentsApi.membershipStatus).mockResolvedValue(activeStatus);
    vi.mocked(paymentsApi.mintMembershipSession).mockResolvedValue({});
    vi.mocked(usersApi.getMe).mockResolvedValue({
      id: "u1",
      username: "buyer",
      role: "user",
    } as never);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("mints a session and signs in on a confirmed checkout return", async () => {
    const wrapper = mountPanel({
      billingToken: "bcap-token",
      orderToken: "order-token",
      checkoutReturn: true,
    });
    await flushPromises();

    expect(paymentsApi.mintMembershipSession).toHaveBeenCalledWith(
      "bcap-token",
      "order-token",
    );
    expect(usersApi.getMe).toHaveBeenCalled();

    const authStore = useAuthStore();
    expect(authStore.isAuthenticated).toBe(true);
    expect(wrapper.emitted("confirmed")).toEqual([["signed_in"]]);
  });

  it("reports verify-email when payment is confirmed but the account is unverified", async () => {
    // Paid registrations stay is_active=false until the email is verified —
    // the mint 403s and the UI must not claim the buyer was signed in.
    vi.mocked(paymentsApi.membershipStatus).mockResolvedValue(
      paidUnverifiedStatus,
    );
    vi.mocked(paymentsApi.mintMembershipSession).mockRejectedValue(
      new Error("forbidden"),
    );

    const wrapper = mountPanel({
      billingToken: "bcap-token",
      orderToken: "order-token",
      checkoutReturn: true,
    });
    await flushPromises();

    expect(paymentsApi.mintMembershipSession).toHaveBeenCalledTimes(1);
    expect(useAuthStore().isAuthenticated).toBe(false);
    expect(wrapper.emitted("confirmed")).toEqual([["verify"]]);
  });

  it("emits confirmed but does not mint when already authenticated", async () => {
    const authStore = useAuthStore();
    authStore.user = { id: "u1", username: "buyer", role: "user" } as never;

    const wrapper = mountPanel({
      orderToken: "order-token",
      checkoutReturn: true,
    });
    await flushPromises();

    expect(paymentsApi.mintMembershipSession).not.toHaveBeenCalled();
    expect(wrapper.emitted("confirmed")).toBeTruthy();
  });

  it("keeps manage actions but reports login when the mint fails", async () => {
    vi.mocked(paymentsApi.mintMembershipSession).mockRejectedValue(
      new Error("forbidden"),
    );

    const wrapper = mountPanel({
      orderToken: "order-token",
      checkoutReturn: true,
    });
    await flushPromises();

    expect(paymentsApi.mintMembershipSession).toHaveBeenCalledTimes(1);
    expect(usersApi.getMe).not.toHaveBeenCalled();
    expect(useAuthStore().isAuthenticated).toBe(false);
    // A fresh order token still authorizes portal/cancel on the order it
    // came from — but the UI must not claim the buyer was signed in.
    expect(wrapper.text()).toContain(
      i18n.global.t("payments.membership.managePortal"),
    );
    expect(wrapper.emitted("confirmed")).toEqual([["login"]]);
  });

  it("does not mint without a scoped credential", async () => {
    const wrapper = mountPanel({ checkoutReturn: true });
    await flushPromises();

    expect(paymentsApi.mintMembershipSession).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain(
      i18n.global.t("payments.membership.continueLogin"),
    );
  });

  it("keeps polling without minting while payment is unconfirmed", async () => {
    vi.useFakeTimers();
    vi.mocked(paymentsApi.membershipStatus).mockResolvedValue(pendingStatus);

    const wrapper = mountPanel({
      orderToken: "order-token",
      checkoutReturn: true,
    });
    await vi.runAllTimersAsync();

    expect(paymentsApi.mintMembershipSession).not.toHaveBeenCalled();
    expect(wrapper.emitted("confirmed")).toBeFalsy();
    // Still not active — no "confirmed" UI, and an order token cannot open
    // a new checkout, so no action buttons render.
    expect(wrapper.text()).not.toContain(
      i18n.global.t("payments.membership.subscribe"),
    );
    expect(wrapper.text()).not.toContain(
      i18n.global.t("payments.membership.continueLogin"),
    );
  });
});
