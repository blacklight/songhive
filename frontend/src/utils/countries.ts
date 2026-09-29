/**
 * Countries where Stripe Connect connected accounts can be created
 * (Express/v2 hosted onboarding). Stripe requires the seller's country of
 * operation upfront — it determines the legal entity type, payout rails and
 * the verification fields onboarding collects.
 *
 * Source: https://docs.stripe.com/connect/connect-cross-border-payouts
 */
export const CONNECT_COUNTRIES: ReadonlyArray<{ code: string; name: string }> =
  [
    { code: "AT", name: "Austria" },
    { code: "AU", name: "Australia" },
    { code: "BE", name: "Belgium" },
    { code: "BG", name: "Bulgaria" },
    { code: "BR", name: "Brazil" },
    { code: "CA", name: "Canada" },
    { code: "CH", name: "Switzerland" },
    { code: "CY", name: "Cyprus" },
    { code: "CZ", name: "Czechia" },
    { code: "DE", name: "Germany" },
    { code: "DK", name: "Denmark" },
    { code: "EE", name: "Estonia" },
    { code: "ES", name: "Spain" },
    { code: "FI", name: "Finland" },
    { code: "FR", name: "France" },
    { code: "GB", name: "United Kingdom" },
    { code: "GI", name: "Gibraltar" },
    { code: "GR", name: "Greece" },
    { code: "HK", name: "Hong Kong" },
    { code: "HR", name: "Croatia" },
    { code: "HU", name: "Hungary" },
    { code: "IE", name: "Ireland" },
    { code: "IT", name: "Italy" },
    { code: "JP", name: "Japan" },
    { code: "LI", name: "Liechtenstein" },
    { code: "LT", name: "Lithuania" },
    { code: "LU", name: "Luxembourg" },
    { code: "LV", name: "Latvia" },
    { code: "MT", name: "Malta" },
    { code: "MX", name: "Mexico" },
    { code: "MY", name: "Malaysia" },
    { code: "NL", name: "Netherlands" },
    { code: "NO", name: "Norway" },
    { code: "NZ", name: "New Zealand" },
    { code: "PH", name: "Philippines" },
    { code: "PL", name: "Poland" },
    { code: "PT", name: "Portugal" },
    { code: "RO", name: "Romania" },
    { code: "SE", name: "Sweden" },
    { code: "SG", name: "Singapore" },
    { code: "SI", name: "Slovenia" },
    { code: "SK", name: "Slovakia" },
    { code: "TH", name: "Thailand" },
    { code: "US", name: "United States" },
  ];
