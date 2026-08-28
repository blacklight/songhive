<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import { resendVerificationEmail } from "@/api/auth";
import { uploadFile } from "@/api/files";
import { getApiErrorMessage } from "@/api/client";
import { deleteMe, type UserProfileUpdate } from "@/api/users";
import AppInput from "@/components/ui/AppInput.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppAvatar from "@/components/ui/AppAvatar.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";
import AppModal from "@/components/feedback/AppModal.vue";

const { t } = useI18n();
const router = useRouter();
const authStore = useAuthStore();
const toast = useToastStore();

const DELETE_CONFIRMATION = "Yes, I really want to delete my account" as const;

const showDeleteDialog = ref(false);
const deleteConfirmation = ref("");
const deleteRecursive = ref(false);
const isDeleting = ref(false);

interface Link {
  name: string;
  url: string;
}

const displayName = ref(authStore.user?.display_name || "");
const bio = ref(authStore.user?.bio || "");
const avatarUrl = ref(authStore.user?.avatar_url || "");
const links = ref<Link[]>(
  authStore.user?.links
    ? authStore.user.links.map((l) => ({ name: l.name, url: l.url }))
    : [],
);
const isLoading = ref(false);
const error = ref<string | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const isUploading = ref(false);
const isResendingVerification = ref(false);

const showResendVerification = computed(() => {
  return !!authStore.user && authStore.user.email_verified !== true;
});

watch(
  () => authStore.user,
  (next) => {
    if (!next) return;
    displayName.value = next.display_name || "";
    bio.value = next.bio || "";
    avatarUrl.value = next.avatar_url || "";
    links.value = next.links
      ? next.links.map((l) => ({ name: l.name, url: l.url }))
      : [];
  },
  { deep: true },
);

function isHttpUrl(value: string): boolean {
  return /^https?:\/\//.test(value);
}

function addLink() {
  links.value.push({ name: "", url: "" });
}

function removeLink(index: number) {
  links.value.splice(index, 1);
}

function triggerFileInput() {
  fileInput.value?.click();
}

async function onFileChange(event: Event) {
  const target = event.target as HTMLInputElement;
  const file = target.files?.[0];
  if (!file) return;

  isUploading.value = true;
  try {
    const response = await uploadFile(file, "public");
    const relative = response.url;
    avatarUrl.value = relative.startsWith("http")
      ? relative
      : `${window.location.origin}${relative}`;
  } catch (err) {
    error.value = getApiErrorMessage(err, t("errors.unknown"));
  } finally {
    isUploading.value = false;
    if (target) target.value = "";
  }
}

async function onResendVerification() {
  if (!authStore.user || isResendingVerification.value) return;

  isResendingVerification.value = true;
  try {
    await resendVerificationEmail({
      username_or_email: authStore.user.username,
    });
    toast.push({
      type: "success",
      message: t("profile.resendVerificationSuccess"),
    });
  } catch (err) {
    toast.push({
      type: "error",
      message: t("profile.resendVerificationError", {
        message: getApiErrorMessage(err, t("errors.unknown")),
      }),
    });
  } finally {
    isResendingVerification.value = false;
  }
}

function openDeleteDialog() {
  error.value = null;
  deleteConfirmation.value = "";
  deleteRecursive.value = false;
  showDeleteDialog.value = true;
}

function closeDeleteDialog() {
  showDeleteDialog.value = false;
}

async function onDeleteAccount() {
  if (deleteConfirmation.value.trim() !== DELETE_CONFIRMATION) {
    error.value = t("profile.deleteAccountConfirmationError");
    return;
  }

  isDeleting.value = true;
  try {
    await deleteMe({
      confirmation: DELETE_CONFIRMATION,
      recursive: deleteRecursive.value,
    });
    toast.push({
      type: "success",
      message: t("profile.deleteAccountSuccess"),
    });
    closeDeleteDialog();
    await authStore.logout();
    router.push("/");
  } catch (err) {
    error.value = t("profile.deleteAccountError", {
      message: getApiErrorMessage(err, t("errors.unknown")),
    });
  } finally {
    isDeleting.value = false;
  }
}

async function onSubmit() {
  error.value = null;

  const patch: UserProfileUpdate = {};

  if (displayName.value.trim()) {
    patch.display_name = displayName.value.trim();
  }

  if (bio.value.trim()) {
    patch.bio = bio.value.trim();
  }

  if (avatarUrl.value.trim()) {
    if (!isHttpUrl(avatarUrl.value.trim())) {
      error.value = t("profile.saveError", { message: t("profile.avatarUrl") });
      return;
    }
    patch.avatar_url = avatarUrl.value.trim();
  }

  const validLinks = links.value.filter((l) => l.name.trim() && l.url.trim());
  if (validLinks.some((l) => !isHttpUrl(l.url.trim()))) {
    error.value = t("profile.saveError", { message: t("profile.linkUrl") });
    return;
  }
  if (validLinks.length > 0) {
    patch.links = validLinks.map((l) => ({
      name: l.name.trim(),
      url: l.url.trim(),
    }));
  }

  isLoading.value = true;
  try {
    await authStore.updateProfile(patch);
    toast.push({ type: "success", message: t("profile.saveSuccess") });
  } catch (err) {
    error.value = t("profile.saveError", {
      message: getApiErrorMessage(err, t("errors.unknown")),
    });
  } finally {
    isLoading.value = false;
  }
}
</script>

<template>
  <form class="profile-tab" @submit.prevent="onSubmit">
    <div class="profile-tab__avatar">
      <AppAvatar
        v-if="authStore.user"
        :src="avatarUrl"
        :name="authStore.user.username"
        size="lg"
      />
      <div class="profile-tab__avatar-fields">
        <AppInput
          v-model="avatarUrl"
          type="url"
          :label="t('profile.avatarUrl')"
          :hint="t('profile.avatar')"
        />
        <AppButton
          type="button"
          variant="secondary"
          :loading="isUploading"
          icon="upload"
          @click="triggerFileInput"
        >
          {{ t("profile.avatarUpload") }}
        </AppButton>
        <input
          ref="fileInput"
          type="file"
          accept="image/*"
          class="profile-tab__file-input"
          @change="onFileChange"
        />
      </div>
    </div>

    <AppInput
      v-model="displayName"
      type="text"
      :label="t('profile.displayName')"
    />

    <AppInput v-model="bio" as="textarea" :label="t('profile.bio')" />

    <fieldset class="profile-tab__links">
      <legend>{{ t("profile.links") }}</legend>
      <p class="profile-tab__hint">{{ t("profile.linksHint") }}</p>

      <div
        v-for="(link, index) in links"
        :key="index"
        class="profile-tab__link-row"
      >
        <AppInput
          v-model="link.name"
          type="text"
          :label="t('profile.linkName')"
        />
        <AppInput v-model="link.url" type="url" :label="t('profile.linkUrl')" />
        <AppButton
          type="button"
          variant="ghost"
          size="sm"
          icon="xmark"
          @click="removeLink(index)"
        >
          {{ t("profile.removeLink") }}
        </AppButton>
      </div>

      <AppButton
        type="button"
        variant="secondary"
        size="sm"
        icon="plus"
        @click="addLink"
      >
        {{ t("profile.addLink") }}
      </AppButton>
    </fieldset>

    <p v-if="error" class="profile-tab__error" role="alert" aria-live="polite">
      {{ error }}
    </p>

    <AppButton type="submit" :loading="isLoading" icon="floppy-disk">
      {{ t("profile.save") }}
    </AppButton>

    <div class="profile-tab__gated">
      <div v-if="showResendVerification" class="profile-tab__gated-item">
        <AppButton
          type="button"
          :loading="isResendingVerification"
          @click="onResendVerification"
        >
          {{ t("profile.resendVerification") }}
        </AppButton>
      </div>

      <div class="profile-tab__gated-item">
        <AppButton
          type="button"
          variant="danger"
          icon="trash-can"
          @click="openDeleteDialog"
        >
          {{ t("profile.deleteAccount") }}
        </AppButton>
        <p class="profile-tab__hint">
          {{ t("profile.deleteAccountHint") }}
        </p>
      </div>
    </div>

    <AppModal
      :open="showDeleteDialog"
      :title="t('profile.deleteAccountDialogTitle')"
      @close="closeDeleteDialog"
    >
      <div class="profile-tab__delete-dialog">
        <p class="profile-tab__delete-warning">
          {{ t("profile.deleteAccountWarning") }}
        </p>

        <AppCheckbox
          v-model="deleteRecursive"
          :label="t('profile.deleteAccountRecursiveLabel')"
        />

        <AppInput
          v-model="deleteConfirmation"
          type="text"
          :label="t('profile.deleteAccountConfirmationLabel')"
          :hint="t('profile.deleteAccountConfirmationHint')"
        />

        <p
          v-if="error"
          class="profile-tab__error"
          role="alert"
          aria-live="polite"
        >
          {{ error }}
        </p>
      </div>

      <template #actions>
        <AppButton
          type="button"
          variant="secondary"
          :disabled="isDeleting"
          @click="closeDeleteDialog"
        >
          {{ t("profile.deleteAccountCancel") }}
        </AppButton>
        <AppButton
          type="button"
          variant="danger"
          :loading="isDeleting"
          icon="trash-can"
          @click="onDeleteAccount"
        >
          {{ t("profile.deleteAccountConfirm") }}
        </AppButton>
      </template>
    </AppModal>
  </form>
</template>

<style scoped>
.profile-tab {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.profile-tab__avatar {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}

.profile-tab__avatar-fields {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: var(--space-2);
}

.profile-tab__file-input {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.profile-tab__links {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.profile-tab__links legend {
  font-weight: 600;
  color: var(--color-text);
}

.profile-tab__hint {
  margin: 0;
  font-size: 0.875rem;
  color: var(--color-text-muted);
}

.profile-tab__link-row {
  display: grid;
  grid-template-columns: 1fr 1fr auto;
  gap: var(--space-2);
  align-items: end;
}

.profile-tab__error {
  margin: 0;
  color: var(--color-danger);
  font-size: 0.875rem;
}

.profile-tab__gated {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-top: var(--space-4);
  border-top: 1px solid var(--color-border);
}

.profile-tab__gated-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.profile-tab__delete-dialog {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.profile-tab__delete-warning {
  margin: 0;
  color: var(--color-danger);
  font-size: 0.875rem;
  line-height: 1.5;
}
</style>
