/**
 * Helpers bridging the JSON WebAuthn payloads the API exchanges with the
 * binary types the browser `navigator.credentials` API expects.
 *
 * The server serializes binary fields (challenge, credential ids, user handle)
 * as unpadded base64url strings.
 */

export function base64urlToBuffer(value: string): ArrayBuffer {
  const base64 = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

export function bufferToBase64url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function decodeDescriptor(
  descriptor: Record<string, unknown>,
): PublicKeyCredentialDescriptor {
  return {
    ...descriptor,
    id: base64urlToBuffer(descriptor.id as string),
  } as PublicKeyCredentialDescriptor;
}

/** Convert the server's JSON create options into CredentialCreationOptions. */
export function creationOptionsFromJson(
  options: Record<string, unknown>,
): PublicKeyCredentialCreationOptions {
  const publicKey = (options.publicKey ?? options) as Record<string, unknown>;
  const user = publicKey.user as Record<string, unknown>;
  return {
    ...publicKey,
    challenge: base64urlToBuffer(publicKey.challenge as string),
    user: { ...user, id: base64urlToBuffer(user.id as string) },
    excludeCredentials: (
      (publicKey.excludeCredentials as Record<string, unknown>[]) ?? []
    ).map(decodeDescriptor),
  } as unknown as PublicKeyCredentialCreationOptions;
}

/** Convert the server's JSON request options into CredentialRequestOptions. */
export function requestOptionsFromJson(
  options: Record<string, unknown>,
): PublicKeyCredentialRequestOptions {
  const publicKey = (options.publicKey ?? options) as Record<string, unknown>;
  return {
    ...publicKey,
    challenge: base64urlToBuffer(publicKey.challenge as string),
    allowCredentials: (
      (publicKey.allowCredentials as Record<string, unknown>[]) ?? []
    ).map(decodeDescriptor),
  } as unknown as PublicKeyCredentialRequestOptions;
}

/** Serialize a PublicKeyCredential from the browser into the API's JSON shape. */
export function credentialToJson(
  credential: PublicKeyCredential,
): Record<string, unknown> {
  const response = credential.response as
    AuthenticatorAttestationResponse | AuthenticatorAssertionResponse;
  const body: Record<string, unknown> = {
    id: credential.id,
    rawId: bufferToBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: bufferToBase64url(response.clientDataJSON),
    },
  };

  const responseBody = body.response as Record<string, unknown>;
  if ("attestationObject" in response) {
    responseBody.attestationObject = bufferToBase64url(
      response.attestationObject,
    );
    if (typeof response.getTransports === "function") {
      responseBody.transports = response.getTransports();
    }
  } else {
    responseBody.authenticatorData = bufferToBase64url(
      response.authenticatorData,
    );
    responseBody.signature = bufferToBase64url(response.signature);
    if (response.userHandle) {
      responseBody.userHandle = bufferToBase64url(response.userHandle);
    }
  }
  return body;
}
