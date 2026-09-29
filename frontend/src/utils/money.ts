/**
 * Money formatting helpers for payment prices (stored in minor units).
 */

import { i18n } from "@/i18n";

function currentLocale(): string {
  return i18n.global.locale.value as string;
}

const ZERO_DECIMAL_CURRENCIES = new Set([
  "BIF",
  "CLP",
  "DJF",
  "GNF",
  "JPY",
  "KMF",
  "KRW",
  "MGA",
  "PYG",
  "RWF",
  "UGX",
  "VND",
  "VUV",
  "XAF",
  "XOF",
  "XPF",
]);

/** Format a minor-unit amount as a localized currency string. */
export function formatPrice(minor: number, currency: string): string {
  const code = currency.toUpperCase();
  const divisor = ZERO_DECIMAL_CURRENCIES.has(code) ? 1 : 100;
  try {
    return new Intl.NumberFormat(currentLocale(), {
      style: "currency",
      currency: code,
    }).format(minor / divisor);
  } catch {
    return `${(minor / divisor).toFixed(2)} ${code}`;
  }
}

/** Convert a major-unit decimal string to minor units for the API. */
export function toMinorUnits(amount: string, currency: string): number | null {
  const parsed = Number.parseFloat(amount.replace(",", "."));
  if (!Number.isFinite(parsed) || parsed < 0) return null;
  const multiplier = ZERO_DECIMAL_CURRENCIES.has(currency.toUpperCase())
    ? 1
    : 100;
  return Math.round(parsed * multiplier);
}

/** Convert minor units to a major-unit decimal string for inputs. */
export function toMajorUnits(minor: number, currency: string): string {
  const divisor = ZERO_DECIMAL_CURRENCIES.has(currency.toUpperCase()) ? 1 : 100;
  return (minor / divisor).toFixed(2);
}
