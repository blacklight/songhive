export interface ProviderFieldOption {
  value: string;
  labelI18nKey: string;
}

export type ProviderFieldType =
  | "string"
  | "password"
  | "number"
  | "boolean"
  | "enum"
  | "string-array"
  | "textarea";

export interface ProviderFieldTemplate {
  /** JSON configuration key for this field. */
  name: string;
  /**
   * JSON configuration key the value is written to/read from. Defaults to
   * ``name``. Useful when a form field shares a union-typed key with another
   * field (e.g. a CA bundle path that overrides a boolean flag).
   */
  configKey?: string;
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
  /** i18n key for an intro/help paragraph shown above the fields. */
  helpI18nKey?: string;
  /** Console/documentation URL linkified as the ``{appConsole}`` help placeholder. */
  helpLinkUrl?: string;
  /** Scopes the provider app requires, rendered as ``<code>`` via the ``{permissions}`` placeholder. */
  helpRequiredScopes?: string[];
  /** Optional scopes, rendered as ``<code>`` via the ``{writePermissions}`` placeholder. */
  helpOptionalScopes?: string[];
  fields: ProviderFieldTemplate[];
}

const DEFAULT_LOCAL_EXTENSIONS = ".mp3, .flac, .ogg, .opus, .m4a, .aac, .wav";

export const providerTemplates: Record<string, ProviderTemplate> = {
  gdrive: {
    providerType: "gdrive",
    helpI18nKey: "pages.externalLibraries.providers.gdrive.help",
    helpLinkUrl: "https://console.cloud.google.com/apis/credentials",
    helpRequiredScopes: ["https://www.googleapis.com/auth/drive.readonly"],
    helpOptionalScopes: ["https://www.googleapis.com/auth/drive"],
    fields: [
      {
        name: "access_token",
        type: "password",
        labelI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.access_token.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.access_token.description",
      },
      {
        name: "refresh_token",
        type: "password",
        labelI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.refresh_token.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.refresh_token.description",
      },
      {
        name: "client_id",
        type: "string",
        labelI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.client_id.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.client_id.description",
        required: true,
      },
      {
        name: "client_secret",
        type: "password",
        labelI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.client_secret.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.client_secret.description",
        required: true,
      },
      {
        name: "service_account_key",
        type: "textarea",
        labelI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.service_account_key.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.service_account_key.description",
      },
      {
        name: "root_folder_id",
        type: "string",
        labelI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.root_folder_id.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.root_folder_id.description",
      },
      {
        name: "drive_id",
        type: "string",
        labelI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.drive_id.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.drive_id.description",
      },
      {
        name: "token_uri",
        type: "string",
        labelI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.token_uri.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.token_uri.description",
      },
      {
        name: "verify_ssl",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.verify_ssl.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.verify_ssl.description",
        default: true,
      },
      {
        // The backend's ``verify_ssl`` accepts either a boolean or a CA
        // bundle path; a non-empty path written here overrides the boolean.
        name: "ca_bundle",
        type: "string",
        configKey: "verify_ssl",
        labelI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.ca_bundle.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.ca_bundle.description",
      },
      {
        name: "timeout",
        type: "number",
        labelI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.timeout.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.timeout.description",
        default: 30,
      },
      {
        name: "extensions",
        type: "string-array",
        labelI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.extensions.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.extensions.description",
        default: DEFAULT_LOCAL_EXTENSIONS,
      },
      {
        name: "exclude",
        type: "string-array",
        labelI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.exclude.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.exclude.description",
        default: "",
      },
      {
        name: "recursive",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.recursive.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.recursive.description",
        default: true,
      },
      {
        name: "allow_hashing",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.allow_hashing.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.allow_hashing.description",
        default: true,
      },
      {
        name: "fast_hash",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.fast_hash.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.fast_hash.description",
        default: false,
      },
      {
        name: "trash_on_delete",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.trash_on_delete.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.trash_on_delete.description",
        default: true,
      },
      {
        name: "allow_write_tags",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.allow_write_tags.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.allow_write_tags.description",
        default: false,
      },
      {
        name: "allow_rename_source",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.allow_rename_source.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.allow_rename_source.description",
        default: false,
      },
      {
        name: "allow_delete_source",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.allow_delete_source.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.gdrive.fields.allow_delete_source.description",
        default: false,
      },
    ],
  },
  jellyfin: {
    providerType: "jellyfin",
    helpI18nKey: "pages.externalLibraries.providers.jellyfin.help",
    fields: [
      {
        name: "server_url",
        type: "string",
        labelI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.server_url.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.server_url.description",
        required: true,
      },
      {
        name: "api_key",
        type: "password",
        labelI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.api_key.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.api_key.description",
      },
      {
        name: "username",
        type: "string",
        labelI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.username.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.username.description",
      },
      {
        name: "password",
        type: "password",
        labelI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.password.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.password.description",
      },
      {
        name: "verify_ssl",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.verify_ssl.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.verify_ssl.description",
        default: true,
      },
      {
        name: "timeout",
        type: "number",
        labelI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.timeout.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.timeout.description",
        default: 30,
      },
      {
        name: "collections",
        type: "string-array",
        labelI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.collections.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.collections.description",
        default: "",
      },
      {
        name: "include_tracks",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.include_tracks.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.include_tracks.description",
        default: true,
      },
      {
        name: "include_artists",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.include_artists.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.include_artists.description",
        default: true,
      },
      {
        name: "include_albums",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.include_albums.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.include_albums.description",
        default: true,
      },
      {
        name: "include_playlists",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.include_playlists.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.include_playlists.description",
        default: true,
      },
      {
        name: "sync_metadata",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.sync_metadata.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.sync_metadata.description",
        default: false,
      },
      {
        name: "sync_cover_art",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.sync_cover_art.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.sync_cover_art.description",
        default: true,
      },
      {
        name: "dedup_musicbrainz",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.dedup_musicbrainz.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.jellyfin.fields.dedup_musicbrainz.description",
        default: false,
      },
    ],
  },
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
        name: "allow_rename_source",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.local.fields.allow_rename_source.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.local.fields.allow_rename_source.description",
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
  s3: {
    providerType: "s3",
    fields: [
      {
        name: "bucket",
        type: "string",
        labelI18nKey:
          "pages.externalLibraries.providers.s3.fields.bucket.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.s3.fields.bucket.description",
        required: true,
      },
      {
        name: "prefix",
        type: "string",
        labelI18nKey:
          "pages.externalLibraries.providers.s3.fields.prefix.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.s3.fields.prefix.description",
      },
      {
        name: "endpoint_url",
        type: "string",
        labelI18nKey:
          "pages.externalLibraries.providers.s3.fields.endpoint_url.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.s3.fields.endpoint_url.description",
      },
      {
        name: "region",
        type: "string",
        labelI18nKey:
          "pages.externalLibraries.providers.s3.fields.region.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.s3.fields.region.description",
      },
      {
        name: "access_key",
        type: "string",
        labelI18nKey:
          "pages.externalLibraries.providers.s3.fields.access_key.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.s3.fields.access_key.description",
      },
      {
        name: "secret_key",
        type: "password",
        labelI18nKey:
          "pages.externalLibraries.providers.s3.fields.secret_key.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.s3.fields.secret_key.description",
      },
      {
        name: "path_style",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.s3.fields.path_style.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.s3.fields.path_style.description",
        default: false,
      },
      {
        name: "presigned_urls",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.s3.fields.presigned_urls.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.s3.fields.presigned_urls.description",
        default: true,
      },
      {
        name: "presigned_expiry_seconds",
        type: "number",
        labelI18nKey:
          "pages.externalLibraries.providers.s3.fields.presigned_expiry_seconds.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.s3.fields.presigned_expiry_seconds.description",
        default: 3600,
      },
      {
        name: "extensions",
        type: "string-array",
        labelI18nKey:
          "pages.externalLibraries.providers.s3.fields.extensions.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.s3.fields.extensions.description",
        default: DEFAULT_LOCAL_EXTENSIONS,
      },
      {
        name: "exclude",
        type: "string-array",
        labelI18nKey:
          "pages.externalLibraries.providers.s3.fields.exclude.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.s3.fields.exclude.description",
        default: "",
      },
      {
        name: "recursive",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.s3.fields.recursive.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.s3.fields.recursive.description",
        default: true,
      },
      {
        name: "allow_hashing",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.s3.fields.allow_hashing.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.s3.fields.allow_hashing.description",
        default: true,
      },
      {
        name: "fast_hash",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.s3.fields.fast_hash.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.s3.fields.fast_hash.description",
        default: false,
      },
      {
        name: "allow_write_tags",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.s3.fields.allow_write_tags.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.s3.fields.allow_write_tags.description",
        default: false,
      },
      {
        name: "allow_rename_source",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.s3.fields.allow_rename_source.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.s3.fields.allow_rename_source.description",
        default: false,
      },
      {
        name: "allow_delete_source",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.s3.fields.allow_delete_source.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.s3.fields.allow_delete_source.description",
        default: false,
      },
    ],
  },
  sftp: {
    providerType: "sftp",
    fields: [
      {
        name: "host",
        type: "string",
        labelI18nKey:
          "pages.externalLibraries.providers.sftp.fields.host.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.sftp.fields.host.description",
        required: true,
      },
      {
        name: "port",
        type: "number",
        labelI18nKey:
          "pages.externalLibraries.providers.sftp.fields.port.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.sftp.fields.port.description",
        default: 22,
      },
      {
        name: "username",
        type: "string",
        labelI18nKey:
          "pages.externalLibraries.providers.sftp.fields.username.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.sftp.fields.username.description",
        required: true,
      },
      {
        name: "password",
        type: "password",
        labelI18nKey:
          "pages.externalLibraries.providers.sftp.fields.password.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.sftp.fields.password.description",
      },
      {
        name: "private_key",
        type: "textarea",
        labelI18nKey:
          "pages.externalLibraries.providers.sftp.fields.private_key.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.sftp.fields.private_key.description",
      },
      {
        name: "private_key_passphrase",
        type: "password",
        labelI18nKey:
          "pages.externalLibraries.providers.sftp.fields.private_key_passphrase.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.sftp.fields.private_key_passphrase.description",
      },
      {
        name: "verify_host_key",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.sftp.fields.verify_host_key.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.sftp.fields.verify_host_key.description",
        default: true,
      },
      {
        name: "known_hosts",
        type: "textarea",
        labelI18nKey:
          "pages.externalLibraries.providers.sftp.fields.known_hosts.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.sftp.fields.known_hosts.description",
      },
      {
        name: "root",
        type: "string",
        labelI18nKey:
          "pages.externalLibraries.providers.sftp.fields.root.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.sftp.fields.root.description",
      },
      {
        name: "connect_timeout",
        type: "number",
        labelI18nKey:
          "pages.externalLibraries.providers.sftp.fields.connect_timeout.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.sftp.fields.connect_timeout.description",
        default: 15,
      },
      {
        name: "follow_symlinks",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.sftp.fields.follow_symlinks.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.sftp.fields.follow_symlinks.description",
        default: false,
      },
      {
        name: "recursive",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.sftp.fields.recursive.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.sftp.fields.recursive.description",
        default: true,
      },
      {
        name: "extensions",
        type: "string-array",
        labelI18nKey:
          "pages.externalLibraries.providers.sftp.fields.extensions.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.sftp.fields.extensions.description",
        default: DEFAULT_LOCAL_EXTENSIONS,
      },
      {
        name: "exclude",
        type: "string-array",
        labelI18nKey:
          "pages.externalLibraries.providers.sftp.fields.exclude.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.sftp.fields.exclude.description",
        default: "",
      },
      {
        name: "allow_hashing",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.sftp.fields.allow_hashing.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.sftp.fields.allow_hashing.description",
        default: true,
      },
      {
        name: "fast_hash",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.sftp.fields.fast_hash.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.sftp.fields.fast_hash.description",
        default: false,
      },
      {
        name: "allow_write_tags",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.sftp.fields.allow_write_tags.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.sftp.fields.allow_write_tags.description",
        default: false,
      },
      {
        name: "allow_rename_source",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.sftp.fields.allow_rename_source.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.sftp.fields.allow_rename_source.description",
        default: false,
      },
      {
        name: "allow_delete_source",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.sftp.fields.allow_delete_source.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.sftp.fields.allow_delete_source.description",
        default: false,
      },
    ],
  },
  webdav: {
    providerType: "webdav",
    fields: [
      {
        name: "url",
        type: "string",
        labelI18nKey:
          "pages.externalLibraries.providers.webdav.fields.url.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.webdav.fields.url.description",
        required: true,
      },
      {
        name: "root",
        type: "string",
        labelI18nKey:
          "pages.externalLibraries.providers.webdav.fields.root.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.webdav.fields.root.description",
      },
      {
        name: "username",
        type: "string",
        labelI18nKey:
          "pages.externalLibraries.providers.webdav.fields.username.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.webdav.fields.username.description",
      },
      {
        name: "password",
        type: "password",
        labelI18nKey:
          "pages.externalLibraries.providers.webdav.fields.password.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.webdav.fields.password.description",
      },
      {
        name: "token",
        type: "password",
        labelI18nKey:
          "pages.externalLibraries.providers.webdav.fields.token.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.webdav.fields.token.description",
      },
      {
        name: "verify_ssl",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.webdav.fields.verify_ssl.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.webdav.fields.verify_ssl.description",
        default: true,
      },
      {
        // The backend's ``verify_ssl`` accepts either a boolean or a CA
        // bundle path; a non-empty path written here overrides the boolean.
        name: "ca_bundle",
        type: "string",
        configKey: "verify_ssl",
        labelI18nKey:
          "pages.externalLibraries.providers.webdav.fields.ca_bundle.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.webdav.fields.ca_bundle.description",
      },
      {
        name: "timeout",
        type: "number",
        labelI18nKey:
          "pages.externalLibraries.providers.webdav.fields.timeout.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.webdav.fields.timeout.description",
        default: 30,
      },
      {
        name: "extensions",
        type: "string-array",
        labelI18nKey:
          "pages.externalLibraries.providers.webdav.fields.extensions.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.webdav.fields.extensions.description",
        default: DEFAULT_LOCAL_EXTENSIONS,
      },
      {
        name: "exclude",
        type: "string-array",
        labelI18nKey:
          "pages.externalLibraries.providers.webdav.fields.exclude.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.webdav.fields.exclude.description",
        default: "",
      },
      {
        name: "recursive",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.webdav.fields.recursive.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.webdav.fields.recursive.description",
        default: true,
      },
      {
        name: "allow_hashing",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.webdav.fields.allow_hashing.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.webdav.fields.allow_hashing.description",
        default: true,
      },
      {
        name: "fast_hash",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.webdav.fields.fast_hash.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.webdav.fields.fast_hash.description",
        default: false,
      },
      {
        name: "allow_write_tags",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.webdav.fields.allow_write_tags.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.webdav.fields.allow_write_tags.description",
        default: false,
      },
      {
        name: "allow_rename_source",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.webdav.fields.allow_rename_source.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.webdav.fields.allow_rename_source.description",
        default: false,
      },
      {
        name: "allow_delete_source",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.webdav.fields.allow_delete_source.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.webdav.fields.allow_delete_source.description",
        default: false,
      },
    ],
  },
  dropbox: {
    providerType: "dropbox",
    helpI18nKey: "pages.externalLibraries.providers.dropbox.help",
    helpLinkUrl: "https://www.dropbox.com/developers/apps",
    helpRequiredScopes: [
      "account_info.read",
      "files.metadata.read",
      "files.content.read",
    ],
    helpOptionalScopes: ["files.metadata.write", "files.content.write"],
    fields: [
      {
        name: "access_token",
        type: "password",
        labelI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.access_token.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.access_token.description",
      },
      {
        name: "refresh_token",
        type: "password",
        labelI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.refresh_token.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.refresh_token.description",
      },
      {
        name: "app_key",
        type: "string",
        labelI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.app_key.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.app_key.description",
        required: true,
      },
      {
        name: "app_secret",
        type: "password",
        labelI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.app_secret.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.app_secret.description",
      },
      {
        name: "root",
        type: "string",
        labelI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.root.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.root.description",
      },
      {
        name: "timeout",
        type: "number",
        labelI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.timeout.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.timeout.description",
        default: 30,
      },
      {
        name: "temporary_links",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.temporary_links.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.temporary_links.description",
        default: false,
      },
      {
        name: "extensions",
        type: "string-array",
        labelI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.extensions.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.extensions.description",
        default: DEFAULT_LOCAL_EXTENSIONS,
      },
      {
        name: "exclude",
        type: "string-array",
        labelI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.exclude.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.exclude.description",
        default: "",
      },
      {
        name: "recursive",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.recursive.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.recursive.description",
        default: true,
      },
      {
        name: "allow_hashing",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.allow_hashing.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.allow_hashing.description",
        default: true,
      },
      {
        name: "fast_hash",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.fast_hash.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.fast_hash.description",
        default: false,
      },
      {
        name: "allow_write_tags",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.allow_write_tags.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.allow_write_tags.description",
        default: false,
      },
      {
        name: "allow_rename_source",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.allow_rename_source.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.allow_rename_source.description",
        default: false,
      },
      {
        name: "allow_delete_source",
        type: "boolean",
        labelI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.allow_delete_source.label",
        descriptionI18nKey:
          "pages.externalLibraries.providers.dropbox.fields.allow_delete_source.description",
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
  const existing = source?.[field.configKey ?? field.name];

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
    // String-family fields only prefill from actual strings: when another
    // field shares the same config key (see ``configKey``), a boolean or
    // number stored there must not render as text.
    if (typeof existing === "string") {
      return existing;
    }
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
    const key = field.configKey ?? field.name;

    if (field.type === "boolean") {
      config[key] = Boolean(raw);
      continue;
    }

    if (
      field.type === "string" ||
      field.type === "password" ||
      field.type === "enum" ||
      field.type === "textarea"
    ) {
      const str = isEmpty(raw) ? "" : String(raw);
      if (str === "" && !field.required) continue;
      config[key] = str;
      continue;
    }

    if (field.type === "number") {
      const num = isEmpty(raw) ? NaN : Number(raw);
      if (Number.isNaN(num)) {
        if (!field.required) continue;
        throw new Error(`Invalid number for ${field.name}`);
      }
      config[key] = num;
      continue;
    }

    if (field.type === "string-array") {
      const str = isEmpty(raw) ? "" : String(raw);
      if (str.trim() === "") {
        if (field.required) config[key] = [];
        continue;
      }
      config[key] = str
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
      continue;
    }
  }

  return config;
}
