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

  it("returns a template for the webdav provider", () => {
    const template = getProviderTemplate("webdav");
    expect(template.providerType).toBe("webdav");
    const fields = template.fields.map((f) => f.name);
    expect(fields).toContain("url");
    expect(fields).toContain("username");
    expect(fields).toContain("password");
    expect(fields).toContain("token");
    expect(fields).toContain("verify_ssl");
    expect(fields).toContain("ca_bundle");
    expect(fields).toContain("timeout");
    expect(template.fields.find((f) => f.name === "url")!.required).toBe(true);
    expect(template.fields.find((f) => f.name === "token")!.type).toBe(
      "password",
    );
    expect(template.fields.find((f) => f.name === "ca_bundle")!.configKey).toBe(
      "verify_ssl",
    );
  });

  it("returns a template for the dropbox provider", () => {
    const template = getProviderTemplate("dropbox");
    expect(template.providerType).toBe("dropbox");
    expect(template.helpI18nKey).toBe(
      "pages.externalLibraries.providers.dropbox.help",
    );
    const fields = template.fields.map((f) => f.name);
    expect(fields).toContain("access_token");
    expect(fields).toContain("refresh_token");
    expect(fields).toContain("app_key");
    expect(fields).toContain("app_secret");
    expect(fields).toContain("root");
    expect(fields).toContain("temporary_links");
    expect(fields).toContain("timeout");
    expect(template.fields.find((f) => f.name === "access_token")!.type).toBe(
      "password",
    );
    expect(template.fields.find((f) => f.name === "refresh_token")!.type).toBe(
      "password",
    );
    // Only the app key is truly required — the tokens are granted by the
    // OAuth connect flow.
    expect(template.fields.find((f) => f.name === "app_key")!.required).toBe(
      true,
    );
    expect(
      template.fields.find((f) => f.name === "access_token")!.required,
    ).toBeFalsy();
    expect(
      template.fields.find((f) => f.name === "refresh_token")!.required,
    ).toBeFalsy();
    expect(
      template.fields.find((f) => f.name === "app_secret")!.required,
    ).toBeFalsy();
  });

  it("returns a template for the gdrive provider", () => {
    const template = getProviderTemplate("gdrive");
    expect(template.providerType).toBe("gdrive");
    expect(template.helpI18nKey).toBe(
      "pages.externalLibraries.providers.gdrive.help",
    );
    expect(template.helpLinkUrl).toBe(
      "https://console.cloud.google.com/apis/credentials",
    );
    const fields = template.fields.map((f) => f.name);
    expect(fields).toContain("access_token");
    expect(fields).toContain("refresh_token");
    expect(fields).toContain("client_id");
    expect(fields).toContain("client_secret");
    expect(fields).toContain("service_account_key");
    expect(fields).toContain("root_folder_id");
    expect(fields).toContain("drive_id");
    // The OAuth client credentials are required for the Connect flow; the
    // tokens are granted by it and the service account key is an alternative.
    expect(template.fields.find((f) => f.name === "client_id")!.required).toBe(
      true,
    );
    expect(
      template.fields.find((f) => f.name === "client_secret")!.required,
    ).toBe(true);
    expect(
      template.fields.find((f) => f.name === "access_token")!.required,
    ).toBeFalsy();
    expect(
      template.fields.find((f) => f.name === "refresh_token")!.required,
    ).toBeFalsy();
    expect(
      template.fields.find((f) => f.name === "service_account_key")!.type,
    ).toBe("textarea");
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

  it("writes a field with configKey to the aliased JSON key", () => {
    const template = getProviderTemplate("webdav");
    const values: Record<string, unknown> = {
      url: "https://dav.example.com/files/alice",
      verify_ssl: false,
      ca_bundle: "/etc/ssl/private-ca.pem",
      timeout: 30,
      recursive: true,
      allow_hashing: true,
      fast_hash: false,
      allow_write_tags: false,
      allow_rename_source: false,
      allow_delete_source: false,
    };

    const config = buildProviderConfigFromTemplate(template, values);
    // The CA bundle path overrides the boolean on the shared verify_ssl key.
    expect(config.verify_ssl).toBe("/etc/ssl/private-ca.pem");
    expect(config).not.toHaveProperty("ca_bundle");
    expect(config.url).toBe("https://dav.example.com/files/alice");
  });

  it("keeps the boolean verify_ssl value when the CA bundle field is empty", () => {
    const template = getProviderTemplate("webdav");
    const values: Record<string, unknown> = {
      url: "https://dav.example.com",
      verify_ssl: false,
      ca_bundle: "",
      timeout: 30,
      recursive: true,
      allow_hashing: true,
      fast_hash: false,
      allow_write_tags: false,
      allow_rename_source: false,
      allow_delete_source: false,
    };

    const config = buildProviderConfigFromTemplate(template, values);
    expect(config.verify_ssl).toBe(false);
    expect(config).not.toHaveProperty("ca_bundle");
  });

  it("prefills a configKey field only from string values on the shared key", () => {
    const template = getProviderTemplate("webdav");
    const caField = template.fields.find((f) => f.name === "ca_bundle")!;
    const verifyField = template.fields.find((f) => f.name === "verify_ssl")!;

    expect(
      getFieldInitialValue(caField, { verify_ssl: "/etc/ssl/ca.pem" }),
    ).toBe("/etc/ssl/ca.pem");
    expect(getFieldInitialValue(caField, { verify_ssl: true })).toBe("");
    expect(getFieldInitialValue(caField, { verify_ssl: false })).toBe("");
    // A CA bundle path on verify_ssl still counts as verification enabled.
    expect(
      getFieldInitialValue(verifyField, { verify_ssl: "/etc/ssl/ca.pem" }),
    ).toBe(true);
    expect(getFieldInitialValue(verifyField, { verify_ssl: false })).toBe(
      false,
    );
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
