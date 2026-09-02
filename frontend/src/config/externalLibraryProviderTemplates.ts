export interface ProviderFieldOption {
  value: string;
  labelI18nKey: string;
}

export type ProviderFieldType =
  "string" | "number" | "boolean" | "enum" | "string-array";

export interface ProviderFieldTemplate {
  /** JSON configuration key for this field. */
  name: string;
  type: ProviderFieldType;
  /** i18n key used to look up the display label. */
  labelI18nKey: string;
  /** i18n key used to look up the help/description text. */
  descriptionI18nKey?: string;
  /** Default value shown in the form (not necessarily sent to the backend). */
  default?: unknown;
  /** Options for ``enum`` fields. */
  options?: ProviderFieldOption[];
  required?: boolean;
}

export interface ProviderTemplate {
  providerType: string;
  fields: ProviderFieldTemplate[];
}

const DEFAULT_LOCAL_EXTENSIONS = ".mp3, .flac, .ogg, .opus, .m4a, .aac, .wav";

export const providerTemplates: Record<string, ProviderTemplate> = {
  local: {
    providerType: "local",
    fields: [
      {
        name: "root",
        type: "string",
        labelI18nKey:
          "pages.externalLibraries.providers.local.fields.root.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.local.fields.root.description",
        required: true,
      },
      {
        name: "follow_symlinks",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.local.fields.follow_symlinks.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.local.fields.follow_symlinks.description",
        default: false,
      },
      {
        name: "recursive",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.local.fields.recursive.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.local.fields.recursive.description",
        default: true,
      },
      {
        name: "extensions",
        type: "string-array",
        labelI18nKey:
          "pages.externalLibraries.providers.local.fields.extensions.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.local.fields.extensions.description",
        default: DEFAULT_LOCAL_EXTENSIONS,
      },
      {
        name: "exclude",
        type: "string-array",
        labelI18nKey:
          "pages.externalLibraries.providers.local.fields.exclude.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.local.fields.exclude.description",
        default: "",
      },
      {
        name: "allow_write_tags",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.local.fields.allow_write_tags.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.local.fields.allow_write_tags.description",
        default: false,
      },
      {
        name: "allow_delete_source",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.local.fields.allow_delete_source.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.local.fields.allow_delete_source.description",
        default: false,
      },
      {
        name: "allow_hashing",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.local.fields.allow_hashing.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.local.fields.allow_hashing.description",
        default: true,
      },
      {
        name: "fast_hash",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.local.fields.fast_hash.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.local.fields.fast_hash.description",
        default: false,
      },
    ],
  },
};

export function getProviderTemplate(providerType: string): ProviderTemplate {
  return providerTemplates[providerType] ?? { providerType, fields: [] };
}

function isEmpty(value: unknown): boolean {
  return value === undefined || value === null || value === "";
}

export function getFieldInitialValue(
  field: ProviderFieldTemplate,
  source?: Record<string, unknown>,
): unknown {
  const existing = source?.[field.name];

  if (existing !== undefined && existing !== null) {
    if (field.type === "string-array" && Array.isArray(existing)) {
      return existing.join(", ");
    }
    if (field.type === "boolean") {
      return Boolean(existing);
    }
    if (field.type === "number") {
      return typeof existing === "number" ? existing : Number(existing);
    }
    return String(existing);
  }

  if (field.default !== undefined) {
    if (field.type === "string-array" && Array.isArray(field.default)) {
      return field.default.join(", ");
    }
    return field.default;
  }

  if (field.type === "boolean") return false;
  if (field.type === "number") return "";
  return "";
}

export function buildProviderConfigFromTemplate(
  template: ProviderTemplate,
  values: Record<string, unknown>,
): Record<string, unknown> {
  const config: Record<string, unknown> = {};

  for (const field of template.fields) {
    const raw = values[field.name];

    if (field.type === "boolean") {
      config[field.name] = Boolean(raw);
      continue;
    }

    if (field.type === "string" || field.type === "enum") {
      const str = isEmpty(raw) ? "" : String(raw);
      if (str === "" && !field.required) continue;
      config[field.name] = str;
      continue;
    }

    if (field.type === "number") {
      const num = isEmpty(raw) ? NaN : Number(raw);
      if (Number.isNaN(num)) {
        if (!field.required) continue;
        throw new Error(`Invalid number for ${field.name}`);
      }
      config[field.name] = num;
      continue;
    }

    if (field.type === "string-array") {
      const str = isEmpty(raw) ? "" : String(raw);
      if (str.trim() === "") {
        if (field.required) config[field.name] = [];
        continue;
      }
      config[field.name] = str
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
      continue;
    }
  }

  return config;
}
