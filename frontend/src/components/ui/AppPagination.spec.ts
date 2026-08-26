import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import AppPagination from "./AppPagination.vue";

describe("AppPagination", () => {
  it("renders the current page and total", () => {
    const wrapper = mount(AppPagination, {
      props: { page: 2, total: 25, perPage: 10 },
    });
    expect(wrapper.text()).toContain("2 / 3");
  });

  it("disables the previous button on the first page", () => {
    const wrapper = mount(AppPagination, {
      props: { page: 1, total: 25, perPage: 10 },
    });
    const buttons = wrapper.findAll("button");
    expect(buttons[0].attributes("disabled")).toBeDefined();
  });

  it("disables the next button on the last page", () => {
    const wrapper = mount(AppPagination, {
      props: { page: 3, total: 25, perPage: 10 },
    });
    const buttons = wrapper.findAll("button");
    expect(buttons[1].attributes("disabled")).toBeDefined();
  });

  it("updates disabled state when props change", async () => {
    const wrapper = mount(AppPagination, {
      props: { page: 3, total: 25, perPage: 10 },
    });
    const buttons = wrapper.findAll("button");
    expect(buttons[1].attributes("disabled")).toBeDefined();

    await wrapper.setProps({ page: 2 });
    expect(buttons[1].attributes("disabled")).toBeUndefined();
  });

  it("emits update:page when previous is clicked", async () => {
    const wrapper = mount(AppPagination, {
      props: { page: 2, total: 25, perPage: 10 },
    });
    await wrapper.findAll("button")[0].trigger("click");
    expect(wrapper.emitted("update:page")?.[0]).toEqual([1]);
  });

  it("emits update:page when next is clicked", async () => {
    const wrapper = mount(AppPagination, {
      props: { page: 2, total: 25, perPage: 10 },
    });
    await wrapper.findAll("button")[1].trigger("click");
    expect(wrapper.emitted("update:page")?.[0]).toEqual([3]);
  });
});
