import { describe, it, expect } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { createPinia, setActivePinia } from "pinia";
import AdminLayout from "./AdminLayout.vue";

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: "/admin",
        component: { template: "<div/>" },
        children: [
          {
            path: "",
            name: "adminDashboard",
            component: { template: "<div/>" },
          },
          {
            path: "users",
            name: "adminUsers",
            component: { template: "<div/>" },
          },
          {
            path: "settings",
            name: "adminSettings",
            component: { template: "<div/>" },
          },
        ],
      },
      { path: "/:pathMatch(.*)*", component: { template: "<div/>" } },
    ],
  });
}

async function mountLayout() {
  const pinia = createPinia();
  setActivePinia(pinia);
  const router = createTestRouter();
  const wrapper = mount(AdminLayout, {
    global: {
      plugins: [pinia, router],
      stubs: { RouterView: true },
    },
  });
  return { wrapper, router };
}

describe("AdminLayout", () => {
  it("highlights the Dashboard nav item only on /admin", async () => {
    const { wrapper, router } = await mountLayout();
    await router.push("/admin");
    await flushPromises();

    const dashboard = wrapper
      .findAll(".admin-layout__nav li a")
      .find((a) => a.text().trim() === "Dashboard");
    expect(dashboard).toBeTruthy();
    expect(dashboard!.classes()).toContain("router-link-active");
  });

  it("does not highlight the Dashboard nav item in other admin sections", async () => {
    const { wrapper, router } = await mountLayout();
    await router.push("/admin/users");
    await flushPromises();

    const dashboard = wrapper
      .findAll(".admin-layout__nav li a")
      .find((a) => a.text().trim() === "Dashboard");
    const users = wrapper
      .findAll(".admin-layout__nav li a")
      .find((a) => a.text().trim() === "Users");
    expect(dashboard).toBeTruthy();
    expect(users).toBeTruthy();
    expect(dashboard!.classes()).not.toContain("router-link-active");
    expect(users!.classes()).toContain("router-link-active");
  });
});
