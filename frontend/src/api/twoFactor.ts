import { apiRequest } from "./client";
import type { TokenPairResponse } from "./auth";

export interface WebAuthnCredentialSummary {
  id: string;
  credential_id: string;
  name: string | null;
  transports: string | null;
  created_at: string;
}

export interface TwoFactorStatus {
  enabled: boolean;
  totp_enabled: boolean;
  webauthn_credentials: WebAuthnCredentialSummary[];
  recovery_codes_remaining: number;
}

export interface TotpSetupResponse {
  secret: string;
  otpauth_url: string;
  qr_code: string;
}

export interface RecoveryCodesResponse {
  recovery_codes: string[];
}

export interface WebAuthnRegisterCompleteResponse {
  credential: WebAuthnCredentialSummary;
  recovery_codes: string[] | null;
}

// Login returns either a token pair or an mfa challenge.
export interface MfaRequiredResponse {
  mfa_required: boolean;
  mfa_token: string;
  methods: string[];
  expires_in: number;
}

export type LoginResponse = TokenPairResponse | MfaRequiredResponse;

export function isMfaRequired(
  response: LoginResponse,
): response is MfaRequiredResponse {
  return (response as MfaRequiredResponse).mfa_required === true;
}

// The server keeps the pending WebAuthn ceremony state; the options payload is
// the publicKey dictionary consumed by navigator.credentials.
export type WebAuthnPublicKeyOptions = Record<string, unknown>;
export type WebAuthnCredentialJson = Record<string, unknown>;

export function getTwoFactorStatus(): Promise<TwoFactorStatus> {
  return apiRequest<TwoFactorStatus>("/auth/2fa");
}

export function startTotpSetup(): Promise<TotpSetupResponse> {
  return apiRequest<TotpSetupResponse>("/auth/2fa/totp/setup", {
    method: "POST",
  });
}

export function confirmTotpSetup(code: string): Promise<RecoveryCodesResponse> {
  return apiRequest<RecoveryCodesResponse>("/auth/2fa/totp/confirm", {
    method: "POST",
    body: { code },
  });
}

export function disableTotp(password: string): Promise<{ success: boolean }> {
  return apiRequest<{ success: boolean }>("/auth/2fa/totp/disable", {
    method: "POST",
    body: { password },
  });
}

export function webAuthnRegisterBegin(): Promise<WebAuthnPublicKeyOptions> {
  return apiRequest<WebAuthnPublicKeyOptions>(
    "/auth/2fa/webauthn/register/begin",
    {
      method: "POST",
    },
  );
}

export function webAuthnRegisterComplete(
  credential: WebAuthnCredentialJson,
  name?: string,
): Promise<WebAuthnRegisterCompleteResponse> {
  return apiRequest<WebAuthnRegisterCompleteResponse>(
    "/auth/2fa/webauthn/register/complete",
    {
      method: "POST",
      body: { credential, name },
    },
  );
}

export function deleteWebAuthnCredential(id: string): Promise<unknown> {
  return apiRequest<unknown>(`/auth/2fa/webauthn/${id}`, { method: "DELETE" });
}

export function regenerateRecoveryCodes(
  password: string,
): Promise<RecoveryCodesResponse> {
  return apiRequest<RecoveryCodesResponse>("/auth/2fa/recovery-codes", {
    method: "POST",
    body: { password },
  });
}

export function mfaLogin(
  mfaToken: string,
  code: string,
): Promise<TokenPairResponse> {
  return apiRequest<TokenPairResponse>("/auth/2fa/login", {
    method: "POST",
    body: { mfa_token: mfaToken, code },
    skipAuth: true,
  });
}

export function mfaWebAuthnBegin(
  mfaToken: string,
): Promise<WebAuthnPublicKeyOptions> {
  return apiRequest<WebAuthnPublicKeyOptions>(
    "/auth/2fa/login/webauthn/begin",
    {
      method: "POST",
      body: { mfa_token: mfaToken },
      skipAuth: true,
    },
  );
}

export function mfaWebAuthnComplete(
  mfaToken: string,
  credential: WebAuthnCredentialJson,
): Promise<TokenPairResponse> {
  return apiRequest<TokenPairResponse>("/auth/2fa/login/webauthn/complete", {
    method: "POST",
    body: { mfa_token: mfaToken, credential },
    skipAuth: true,
  });
}
