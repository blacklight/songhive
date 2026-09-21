import { describe, it, expect } from "vitest";
import {
  buildProviderConfigFromTemplate,
  getFieldInitialValue,
  getProviderTemplate,
} from "./externalLibraryProviderTemplates";

describe("externalLibraryProviderTemplates", () => {
  it("returns a template for the local provider", () => {
    const template = getProviderTemplate("local");
    expect(template.providerType).toBe("local");
    expect(template.fields.length).toBeGreaterThan(0);
    expect(template.fields.map((f) => f.name)).toContain("root");
    expect(template.fields.map((f) => f.name)).toContain("follow_symlinks");
  });

  it("returns a template for the sftp provider", () => {
    const template = getProviderTemplate("sftp");
    expect(template.providerType).toBe("sftp");
    const fields = template.fields.map((f) => f.name);
    expect(fields).toContain("host");
    expect(fields).toContain("username");
    expect(fields).toContain("password");
    expect(fields).toContain("private_key");
    expect(fields).toContain("root");
    expect(template.fields.find((f) => f.name === "host")!.required).toBe(true);
    expect(template.fields.find((f) => f.name === "private_key")!.type).toBe(
      "textarea",
    );
  });

  it("returns an empty template for unknown providers", () => {
    const template = getProviderTemplate("unknown");
    expect(template.providerType).toBe("unknown");
    expect(template.fields).toEqual([]);
  });

  it("initializes field values from an existing config", () => {
    const template = getProviderTemplate("local");
    const rootField = template.fields.find((f) => f.name === "root")!;
    const followField = template.fields.find(
      (f) => f.name === "follow_symlinks",
    )!;
    const extField = template.fields.find((f) => f.name === "extensions")!;

    const source = {
      root: "/music",
      follow_symlinks: true,
      extensions: [".mp3", ".flac"],
    };

    expect(getFieldInitialValue(rootField, source)).toBe("/music");
    expect(getFieldInitialValue(followField, source)).toBe(true);
    expect(getFieldInitialValue(extField, source)).toBe(".mp3, .flac");
  });

  it("falls back to defaults when no existing config is provided", () => {
    const template = getProviderTemplate("local");
    const followField = template.fields.find(
      (f) => f.name === "follow_symlinks",
    )!;
    const rootField = template.fields.find((f) => f.name === "root")!;

    expect(getFieldInitialValue(followField)).toBe(false);
    expect(getFieldInitialValue(rootField)).toBe("");
  });

  it("builds a JSON configuration object from template values", () => {
    const template = getProviderTemplate("local");
    const values: Record<string, unknown> = {
      root: "/music",
      follow_symlinks: true,
      recursive: false,
      extensions: ".mp3, .flac",
      exclude: "",
      allow_write_tags: true,
      allow_delete_source: false,
      allow_hashing: true,
      fast_hash: false,
    };

    const config = buildProviderConfigFromTemplate(template, values);
    expect(config).toEqual({
      root: "/music",
      follow_symlinks: true,
      recursive: false,
      extensions: [".mp3", ".flac"],
      allow_write_tags: true,
      allow_rename_source: false,
      allow_delete_source: false,
      allow_hashing: true,
      fast_hash: false,
    });
  });

  it("keeps multiline textarea values intact", () => {
    const template = getProviderTemplate("sftp");
    const pem =
      "-----BEGIN OPENSSH PRIVATE KEY-----\nabc123\n-----END OPENSSH PRIVATE KEY-----";
    const values: Record<string, unknown> = {
      host: "nas.local",
      port: 22,
      username: "music",
      private_key: pem,
      verify_host_key: false,
      follow_symlinks: false,
      recursive: true,
      allow_hashing: true,
      fast_hash: false,
      allow_write_tags: false,
      allow_rename_source: false,
      allow_delete_source: false,
    };

    const config = buildProviderConfigFromTemplate(template, values);
    expect(config.private_key).toBe(pem);
    expect(config.host).toBe("nas.local");
    expect(config.port).toBe(22);
    expect(config).not.toHaveProperty("password");
    expect(config).not.toHaveProperty("known_hosts");
    expect(config).not.toHaveProperty("root");
  });

  it("parses comma-separated string arrays and skips empty optional arrays", () => {
    const template = getProviderTemplate("local");
    const values: Record<string, unknown> = {
      root: "/music",
      follow_symlinks: false,
      recursive: true,
      extensions: "",
      exclude: "",
      allow_write_tags: false,
      allow_delete_source: false,
      allow_hashing: true,
      fast_hash: false,
    };

    const config = buildProviderConfigFromTemplate(template, values);
    expect(config).not.toHaveProperty("extensions");
    expect(config).not.toHaveProperty("exclude");
    expect(config).toEqual(
      expect.objectContaining({
        root: "/music",
        follow_symlinks: false,
      }),
    );
  });
});
