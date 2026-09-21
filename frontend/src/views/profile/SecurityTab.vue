<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import * as twoFactorApi from "@/api/twoFactor";
import type { TwoFactorStatus } from "@/api/twoFactor";
import { getApiErrorMessage } from "@/api/client";
import { useToastStore } from "@/stores/toast";
import { useConfirm } from "@/composables/useConfirm";
import { formatDateTime } from "@/i18n";
import { credentialToJson, creationOptionsFromJson } from "@/utils/webauthn";
import AppInput from "@/components/ui/AppInput.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppTable from "@/components/ui/AppTable.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";

const { t } = useI18n();
const toast = useToastStore();
const { confirm } = useConfirm();

const status = ref<TwoFactorStatus | null>(null);
const isLoading = ref(false);
const error = ref<string | null>(null);

// TOTP enrollment: the setup payload is only persisted after the user proves
// they can produce a valid code.
const totpSetup = ref<twoFactorApi.TotpSetupResponse | null>(null);
const totpCode = ref("");
const totpPassword = ref("");
const isBusy = ref(false);

const webAuthnSupported = ref(
  typeof window !== "undefined" && !!window.PublicKeyCredential,
);
const newKeyName = ref("");
const isAddingKey = ref(false);

const recoveryPassword = ref("");
const revealedCodes = ref<string[] | null>(null);

const credentialColumns = [
  { key: "name", label: t("profile.security.keys.name") },
  { key: "created_at", label: t("profile.security.keys.createdAt") },
  { key: "actions", label: "", align: "right" as const },
];

async function fetchStatus() {
  isLoading.value = true;
  try {
    status.value = await twoFactorApi.getTwoFactorStatus();
  } catch (err) {
    error.value = getApiErrorMessage(err, t("errors.unknown"));
  } finally {
    isLoading.value = false;
  }
}

async function onStartTotpSetup() {
  error.value = null;
  isBusy.value = true;
  try {
    totpSetup.value = await twoFactorApi.startTotpSetup();
    totpCode.value = "";
  } catch (err) {
    error.value = getApiErrorMessage(err, t("errors.unknown"));
  } finally {
    isBusy.value = false;
  }
}

function onCancelTotpSetup() {
  totpSetup.value = null;
  totpCode.value = "";
}

async function onConfirmTotpSetup() {
  error.value = null;
  if (!totpSetup.value || !totpCode.value) return;
  isBusy.value = true;
  try {
    const response = await twoFactorApi.confirmTotpSetup(totpCode.value);
    revealedCodes.value = response.recovery_codes;
    totpSetup.value = null;
    totpCode.value = "";
    toast.push({
      type: "success",
      message: t("profile.security.totp.enabled"),
    });
    await fetchStatus();
  } catch (err) {
    error.value = getApiErrorMessage(err, t("errors.unknown"));
  } finally {
    isBusy.value = false;
  }
}

async function onDisableTotp() {
  error.value = null;
  if (!totpPassword.value) return;
  const ok = await confirm({
    title: t("profile.security.totp.disable"),
    message: t("profile.security.totp.disableConfirm"),
    danger: true,
  });
  if (!ok) return;

  isBusy.value = true;
  try {
    await twoFactorApi.disableTotp(totpPassword.value);
    totpPassword.value = "";
    toast.push({
      type: "success",
      message: t("profile.security.totp.disabled"),
    });
    await fetchStatus();
  } catch (err) {
    error.value = getApiErrorMessage(err, t("errors.unknown"));
  } finally {
    isBusy.value = false;
  }
}

async function onAddSecurityKey() {
  error.value = null;
  isAddingKey.value = true;
  try {
    const options = await twoFactorApi.webAuthnRegisterBegin();
    const credential = (await navigator.credentials.create({
      publicKey: creationOptionsFromJson(options),
    })) as PublicKeyCredential | null;
    if (!credential) throw new Error(t("profile.security.keys.cancelled"));
    const response = await twoFactorApi.webAuthnRegisterComplete(
      credentialToJson(credential),
      newKeyName.value || undefined,
    );
    if (response.recovery_codes) {
      revealedCodes.value = response.recovery_codes;
    }
    newKeyName.value = "";
    toast.push({
      type: "success",
      message: t("profile.security.keys.added"),
    });
    await fetchStatus();
  } catch (err) {
    error.value = getApiErrorMessage(err, t("errors.unknown"));
  } finally {
    isAddingKey.value = false;
  }
}

async function onDeleteKey(id: string, name: string | null) {
  const ok = await confirm({
    title: t("profile.security.keys.remove"),
    message: t("profile.security.keys.removeConfirm", {
      name: name || t("profile.security.keys.unnamed"),
    }),
    danger: true,
  });
  if (!ok) return;

  try {
    await twoFactorApi.deleteWebAuthnCredential(id);
    await fetchStatus();
  } catch (err) {
    error.value = getApiErrorMessage(err, t("errors.unknown"));
  }
}

async function onRegenerateCodes() {
  error.value = null;
  if (!recoveryPassword.value) return;
  const ok = await confirm({
    title: t("profile.security.recovery.regenerate"),
    message: t("profile.security.recovery.regenerateConfirm"),
    danger: true,
  });
  if (!ok) return;

  isBusy.value = true;
  try {
    const response = await twoFactorApi.regenerateRecoveryCodes(
      recoveryPassword.value,
    );
    revealedCodes.value = response.recovery_codes;
    recoveryPassword.value = "";
    await fetchStatus();
  } catch (err) {
    error.value = getApiErrorMessage(err, t("errors.unknown"));
  } finally {
    isBusy.value = false;
  }
}

async function copyRecoveryCodes() {
  if (!revealedCodes.value || !navigator.clipboard) return;
  try {
    await navigator.clipboard.writeText(revealedCodes.value.join("\n"));
    toast.push({
      type: "success",
      message: t("profile.security.recovery.copied"),
    });
  } catch {
    toast.push({ type: "error", message: t("errors.unknown") });
  }
}

onMounted(fetchStatus);
</script>

<template>
  <div class="security-tab">
    <AppSpinner v-if="isLoading && !status" />

    <template v-else>
      <p
        v-if="error"
        class="security-tab__error"
        role="alert"
        aria-live="polite"
      >
        {{ error }}
      </p>

      <section class="security-tab__section">
        <h3 class="security-tab__heading">
          {{ t("profile.security.totp.heading") }}
        </h3>

        <template v-if="status?.totp_enabled">
          <p class="security-tab__hint">
            {{ t("profile.security.totp.enabledHint") }}
          </p>
          <form
            class="security-tab__row security-tab__totp-pass"
            @submit.prevent="onDisableTotp"
          >
            <AppInput
              v-model="totpPassword"
              type="password"
              :label="t('profile.security.passwordLabel')"
              :required="true"
              :disabled="isBusy"
            />
            <AppButton
              type="submit"
              variant="danger"
              :loading="isBusy"
              icon="trash-can"
            >
              {{ t("profile.security.totp.disable") }}
            </AppButton>
          </form>
        </template>

        <template v-else-if="!totpSetup">
          <p class="security-tab__hint">
            {{ t("profile.security.totp.disabledHint") }}
          </p>
          <AppButton
            type="button"
            icon="qrcode"
            :loading="isBusy"
            @click="onStartTotpSetup"
          >
            {{ t("profile.security.totp.setup") }}
          </AppButton>
        </template>

        <template v-else>
          <p class="security-tab__hint">
            {{ t("profile.security.totp.scanHint") }}
          </p>
          <img
            :src="totpSetup.qr_code"
            :alt="t('profile.security.totp.qrAlt')"
            class="security-tab__qr"
          />
          <p class="security-tab__secret">
            {{ t("profile.security.totp.manualEntry") }}
            <code>{{ totpSetup.secret }}</code>
          </p>
          <form class="security-tab__row" @submit.prevent="onConfirmTotpSetup">
            <AppInput
              v-model="totpCode"
              type="text"
              :label="t('profile.security.totp.codeLabel')"
              :hint="t('profile.security.totp.codeHint')"
              :required="true"
              :disabled="isBusy"
              autocomplete="one-time-code"
            />
            <AppButton type="submit" :loading="isBusy" icon="check">
              {{ t("profile.security.totp.confirm") }}
            </AppButton>
            <AppButton
              type="button"
              variant="secondary"
              :disabled="isBusy"
              @click="onCancelTotpSetup"
            >
              {{ t("common.cancel") }}
            </AppButton>
          </form>
        </template>
      </section>

      <section class="security-tab__section">
        <h3 class="security-tab__heading">
          {{ t("profile.security.keys.heading") }}
        </h3>

        <p v-if="!webAuthnSupported" class="security-tab__hint">
          {{ t("profile.security.keys.unsupported") }}
        </p>

        <template v-else>
          <AppTable
            :columns="credentialColumns"
            :rows="status?.webauthn_credentials ?? []"
            :empty-label="t('profile.security.keys.empty')"
          >
            <template #row-name="{ value }">
              {{ (value as string) || t("profile.security.keys.unnamed") }}
            </template>
            <template #row-created_at="{ value }">
              {{ formatDateTime(value as string) }}
            </template>
            <template #row-actions="{ row }">
              <AppButton
                type="button"
                variant="danger"
                size="sm"
                icon="trash-can"
                @click="
                  onDeleteKey(
                    (row as { id: string; name: string | null }).id,
                    (row as { id: string; name: string | null }).name,
                  )
                "
              >
                {{ t("profile.security.keys.remove") }}
              </AppButton>
            </template>
          </AppTable>

          <form class="security-tab__row" @submit.prevent="onAddSecurityKey">
            <AppInput
              v-model="newKeyName"
              type="text"
              :label="t('profile.security.keys.nameLabel')"
              :hint="t('profile.security.keys.nameHint')"
              :disabled="isAddingKey"
            />
            <AppButton type="submit" :loading="isAddingKey" icon="key">
              {{ t("profile.security.keys.add") }}
            </AppButton>
          </form>
        </template>
      </section>

      <section v-if="status?.enabled" class="security-tab__section">
        <h3 class="security-tab__heading">
          {{ t("profile.security.recovery.heading") }}
        </h3>
        <p class="security-tab__hint">
          {{
            t("profile.security.recovery.remaining", {
              count: status.recovery_codes_remaining,
            })
          }}
        </p>

        <form
          class="security-tab__row security-tab__regenerate-codes"
          @submit.prevent="onRegenerateCodes"
        >
          <AppInput
            v-model="recoveryPassword"
            type="password"
            :label="t('profile.security.passwordLabel')"
            :required="true"
            :disabled="isBusy"
          />
          <AppButton type="submit" :loading="isBusy" icon="rotate">
            {{ t("profile.security.recovery.regenerate") }}
          </AppButton>
        </form>
      </section>

      <section v-if="revealedCodes" class="security-tab__section">
        <h3 class="security-tab__heading">
          {{ t("profile.security.recovery.newCodes") }}
        </h3>
        <p class="security-tab__hint">
          {{ t("profile.security.recovery.newCodesHint") }}
        </p>
        <ul class="security-tab__codes">
          <li v-for="code in revealedCodes" :key="code">
            <code>{{ code }}</code>
          </li>
        </ul>
        <AppButton type="button" icon="copy" @click="copyRecoveryCodes">
          {{ t("common.copy") }}
        </AppButton>
      </section>
    </template>
  </div>
</template>

<style scoped>
.security-tab {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

.security-tab__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.security-tab__heading {
  margin: 0;
  font-size: 1.125rem;
}

.security-tab__hint {
  margin: 0;
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.security-tab__row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.security-tab__totp-pass,
.security-tab__regenerate-codes {
  align-items: flex-end;
}

.security-tab__error {
  margin: 0;
  color: var(--color-danger);
  font-size: 0.875rem;
}

.security-tab__qr {
  width: 200px;
  height: 200px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: #fff;
  padding: var(--space-2);
}

.security-tab__secret {
  margin: 0;
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.security-tab__codes {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr));
  gap: var(--space-2);
  margin: 0;
  padding: var(--space-4);
  list-style: none;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface-raised);
}
</style>
