import { i18n } from "@/i18n";
import type { Visibility } from "@/api/libraries";

export function parseNumber(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isNaN(parsed) ? null : parsed;
}

const BYTES_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"];

export function formatBytes(
  bytes: number,
  decimals = 1,
  locale?: string,
): string {
  if (Number.isNaN(bytes) || bytes <= 0) return "0 B";
  if (bytes === 1) return "1 B";

  const k = 1024;
  const i = Math.min(
    BYTES_UNITS.length - 1,
    Math.floor(Math.log(bytes) / Math.log(k)),
  );

  if (i === 0) return `${bytes} B`;

  const value = bytes / Math.pow(k, i);
  const resolvedLocale = locale || (i18n.global.locale.value as string);
  const numberFormat =
    decimals === 1
      ? new Intl.NumberFormat(resolvedLocale, {
          minimumFractionDigits: 0,
          maximumFractionDigits: 1,
        })
      : new Intl.NumberFormat(resolvedLocale, {
          minimumFractionDigits: decimals,
          maximumFractionDigits: decimals,
        });

  return `${numberFormat.format(value)} ${BYTES_UNITS[i]}`;
}

function isVisibility(value: string): value is Visibility {
  return value === "private" || value === "local" || value === "public";
}

export function toVisibility(value: string | null | undefined): Visibility {
  if (value && isVisibility(value)) {
    return value;
  }
  return "private";
}
