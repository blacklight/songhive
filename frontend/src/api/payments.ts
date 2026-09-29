/**
 * Payments API client — artist sales, checkout, guest redemption, and
 * instance membership billing.
 *
 * These paths are not yet in the generated `types.ts`, so request/response
 * shapes are defined here and kept in sync with `api/routes/payments.py`.
 */

import { apiRequest } from "./client";

export type SaleEntityType = "track" | "album";
export type UnpaidPolicy = "full_stream" | "sample" | "none";
export type SampleRenderPolicy = "materialized" | "on_demand";

export interface Sale {
  id: string;
  entity_type: SaleEntityType;
  entity_id: string;
  status: string;
  price_minor: number;
  currency: string;
  unpaid_policy: UnpaidPolicy;
  sample_start_seconds: number;
  sample_length_seconds: number | null;
}

export interface SaleUpsertRequest {
  entity_type: SaleEntityType;
  entity_id: string;
  price_minor: number;
  currency: string;
  unpaid_policy: UnpaidPolicy;
  sample_start_seconds?: number;
  sample_length_seconds?: number | null;
  sample_render_policy?: SampleRenderPolicy;
  publish?: boolean;
}

export interface SalePatchRequest {
  price_minor?: number;
  currency?: string;
  unpaid_policy?: UnpaidPolicy;
  sample_start_seconds?: number;
  sample_length_seconds?: number | null;
  sample_render_policy?: SampleRenderPolicy;
  publish?: boolean;
}

export interface ConnectStatus {
  connected: boolean;
  charges_enabled?: boolean;
  payouts_enabled?: boolean;
  details_submitted?: boolean;
  onboarded?: boolean;
}

export interface CheckoutResponse {
  checkout_url: string;
  order_id: string;
}

export interface OrderTrackSummary {
  track_id: string;
  title: string | null;
  position: number;
}

export interface Order {
  id: string;
  kind: string;
  status: string;
  sale_id: string | null;
  currency: string;
  total_minor: number;
  created_at: string | null;
  tracks: OrderTrackSummary[];
}

export interface RedeemResponse {
  order: Order;
  download_token: string;
  expires_in_days: number;
}

export interface MembershipQuote {
  price_minor: number;
  currency: string;
  interval: string;
}

export interface MembershipStatus {
  status: string;
  paid_through: string | null;
  cancel_at_period_end: boolean;
  payments_required: boolean;
  /** Account activation — stays false for paid registrations until the
   * email is verified. */
  is_active: boolean;
  email_verified: boolean;
}

// ---------------------------------------------------------------------------
// Seller connect account
// ---------------------------------------------------------------------------

export function connectOnboard(body: {
  refresh_path: string;
  return_path: string;
  /** ISO 3166-1 alpha-2 — required when the connected account is created. */
  country?: string;
}): Promise<{ onboarding_url: string }> {
  return apiRequest("/payments/connect/onboard", { method: "POST", body });
}

export function connectStatus(): Promise<ConnectStatus> {
  return apiRequest("/payments/connect/status");
}

export function connectDisconnect(): Promise<{ disconnected: boolean }> {
  return apiRequest("/payments/connect/disconnect", { method: "POST" });
}

// ---------------------------------------------------------------------------
// Sales
// ---------------------------------------------------------------------------

export function createSale(body: SaleUpsertRequest): Promise<Sale> {
  return apiRequest("/payments/sales", { method: "POST", body });
}

export function listMySales(): Promise<Sale[]> {
  return apiRequest("/payments/sales/mine");
}

export function saleOffer(
  entityType: SaleEntityType,
  entityId: string,
): Promise<Sale> {
  return apiRequest(`/payments/sales/offer/${entityType}/${entityId}`);
}

export function updateSale(
  saleId: string,
  body: SalePatchRequest,
): Promise<Sale> {
  return apiRequest(`/payments/sales/${saleId}`, { method: "PATCH", body });
}

export function deleteSale(saleId: string): Promise<{ status: string }> {
  return apiRequest(`/payments/sales/${saleId}`, { method: "DELETE" });
}

// ---------------------------------------------------------------------------
// Checkout / orders / redemption
// ---------------------------------------------------------------------------

export function checkout(body: {
  item_type: SaleEntityType;
  item_id: string;
  guest_email?: string;
}): Promise<CheckoutResponse> {
  return apiRequest("/payments/checkout", { method: "POST", body });
}

export function listMyOrders(): Promise<Order[]> {
  return apiRequest("/payments/orders/mine");
}

export function getOrder(orderId: string, token?: string): Promise<Order> {
  return apiRequest(`/payments/orders/${orderId}`, {
    query: { token },
  });
}

export function redeem(token: string): Promise<RedeemResponse> {
  return apiRequest("/payments/redeem", { method: "POST", body: { token } });
}

export function resendRedeem(orderId: string): Promise<unknown> {
  return apiRequest("/payments/redeem/resend", {
    method: "POST",
    body: { order_id: orderId },
  });
}

// ---------------------------------------------------------------------------
// Membership billing
// ---------------------------------------------------------------------------

export function membershipQuote(): Promise<MembershipQuote> {
  return apiRequest("/payments/membership/quote");
}

export function membershipCheckout(
  billingToken?: string,
): Promise<CheckoutResponse> {
  return apiRequest("/payments/membership/checkout", {
    method: "POST",
    // The billing capability is the credential — a 401 here must not kick
    // off the session refresh/logout path (the caller has no valid session).
    skipAuth: !!billingToken,
    query: { billing_token: billingToken },
  });
}

export function membershipStatus(
  billingToken?: string,
  orderToken?: string,
): Promise<MembershipStatus> {
  return apiRequest("/payments/membership/status", {
    // Scoped credentials (billing capability / checkout order token) are
    // sufficient — skipAuth avoids a refresh+logout storm on inactive
    // accounts whose session tokens are rejected.
    skipAuth: !!(billingToken || orderToken),
    query: { billing_token: billingToken, order: orderToken },
  });
}

/**
 * Exchange a post-checkout scoped credential (billing capability or order
 * token) for a full session. The backend only mints once the webhook-confirmed
 * status shows the account active, and each credential mints a single time.
 * skipAuth: the caller has no session yet — a failure must not kick off the
 * refresh/logout path.
 */
export function mintMembershipSession(
  billingToken?: string,
  orderToken?: string,
): Promise<unknown> {
  return apiRequest("/payments/membership/session", {
    method: "POST",
    skipAuth: true,
    body: { billing_token: billingToken, order: orderToken },
  });
}

export function membershipPortal(
  billingToken?: string,
  orderToken?: string,
): Promise<{ portal_url: string }> {
  return apiRequest("/payments/membership/portal", {
    method: "POST",
    skipAuth: !!(billingToken || orderToken),
    query: { billing_token: billingToken, order: orderToken },
  });
}

export function membershipCancel(
  immediate = false,
  billingToken?: string,
  orderToken?: string,
): Promise<MembershipStatus> {
  return apiRequest("/payments/membership/cancel", {
    method: "POST",
    skipAuth: !!(billingToken || orderToken),
    query: { billing_token: billingToken, order: orderToken },
    body: { immediate },
  });
}

// ---------------------------------------------------------------------------
// Admin
// ---------------------------------------------------------------------------

export function adminListOrders(): Promise<Order[]> {
  return apiRequest("/admin/payments/orders");
}

export function adminRefundOrder(
  orderId: string,
): Promise<{ refunded: boolean }> {
  return apiRequest(`/admin/payments/orders/${orderId}/refund`, {
    method: "POST",
  });
}

export function adminSetPaymentsRequired(
  userId: string,
  required: boolean,
  cancelSubscription = true,
): Promise<unknown> {
  return apiRequest(`/admin/users/${userId}/payments-required`, {
    method: "POST",
    body: { required, cancel_subscription: cancelSubscription },
  });
}
