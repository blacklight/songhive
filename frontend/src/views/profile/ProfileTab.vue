<script setup lang="ts">
import { ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useAuthStore } from "@/stores/auth";
import { useToastStore } from "@/stores/toast";
import { uploadFile } from "@/api/files";
import { ApiError } from "@/api/client";
import type { UserProfileUpdate } from "@/api/users";
import AppInput from "@/components/ui/AppInput.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppAvatar from "@/components/ui/AppAvatar.vue";

const { t } = useI18n();
const authStore = useAuthStore();
const toast = useToastStore();

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
    const message =
      err instanceof ApiError ? err.detail || err.message : undefined;
    error.value = message || t("errors.unknown");
  } finally {
    isUploading.value = false;
    if (target) target.value = "";
  }
}

function validateLinks(): boolean {
  for (const link of links.value) {
    if (!link.name || !link.url) {
      continue;
    }
    if (!isHttpUrl(link.url)) {
      error.value = t("profile.saveError", { message: t("profile.linkUrl") });
      return false;
    }
  }
  return true;
}

async function onSubmit() {
  error.value = null;

  if (!validateLinks()) {
    return;
  }

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
  if (validLinks.length > 0) {
    if (!validLinks.every((l) => isHttpUrl(l.url.trim()))) {
      error.value = t("profile.saveError", { message: t("profile.linkUrl") });
      return;
    }
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
    const message =
      err instanceof ApiError ? err.detail || err.message : undefined;
    error.value = t("profile.saveError", { message: message || t("errors.unknown") });
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

    <div class="app-input">
      <label for="profile-bio" class="profile-tab__label">{{ t("profile.bio") }}</label>
      <textarea
        id="profile-bio"
        v-model="bio"
        class="profile-tab__bio"
        rows="4"
      />
    </div>

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
        <AppInput
          v-model="link.url"
          type="url"
          :label="t('profile.linkUrl')"
        />
        <AppButton
          type="button"
          variant="ghost"
          size="sm"
          @click="removeLink(index)"
        >
          {{ t("profile.removeLink") }}
        </AppButton>
      </div>

      <AppButton type="button" variant="secondary" size="sm" @click="addLink">
        {{ t("profile.addLink") }}
      </AppButton>
    </fieldset>

    <p
      v-if="error"
      class="profile-tab__error"
      role="alert"
      aria-live="polite"
    >
      {{ error }}
    </p>

    <AppButton type="submit" :loading="isLoading">
      {{ t("profile.save") }}
    </AppButton>

    <div class="profile-tab__gated">
      <div class="profile-tab__gated-item">
        <AppButton type="button" disabled>
          {{ t("profile.passwordChange") }}
        </AppButton>
        <p class="profile-tab__hint">{{ t("profile.passwordChangeDisabled") }}</p>
      </div>

      <div class="profile-tab__gated-item">
        <AppButton type="button" disabled>
          {{ t("profile.resendVerification") }}
        </AppButton>
        <p class="profile-tab__hint">{{ t("profile.resendVerificationDisabled") }}</p>
      </div>

      <div class="profile-tab__gated-item">
        <AppButton type="button" variant="danger" disabled>
          {{ t("profile.deleteAccount") }}
        </AppButton>
        <p class="profile-tab__hint">{{ t("profile.deleteAccountDisabled") }}</p>
      </div>
    </div>
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

.profile-tab__label {
  color: var(--color-text);
  font-size: 0.875rem;
  font-weight: 500;
}

.profile-tab__bio {
  width: 95%;
  display: block;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background-color: var(--color-surface);
  color: var(--color-text);
  font-size: 1rem;
  font-family: inherit;
  resize: vertical;
}

.profile-tab__bio:focus {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
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
</style>
