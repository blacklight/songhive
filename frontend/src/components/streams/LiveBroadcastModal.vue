<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import type { StreamResponse } from "@/api/streams";
import { useLiveBroadcastStore } from "@/stores/liveBroadcast";
import AppModal from "@/components/feedback/AppModal.vue";
import AppButton from "@/components/ui/AppButton.vue";
import AppCheckbox from "@/components/ui/AppCheckbox.vue";
import AppIcon from "@/components/ui/AppIcon.vue";
import AppInput from "@/components/ui/AppInput.vue";
import AppSelect from "@/components/ui/AppSelect.vue";
import AppSpinner from "@/components/feedback/AppSpinner.vue";

interface Props {
  open: boolean;
  stream: StreamResponse;
}

const props = defineProps<Props>();
const emit = defineEmits<{ close: [] }>();
const { t } = useI18n();
const broadcast = useLiveBroadcastStore();

const title = ref(props.stream.name);
const now = ref(Date.now());
const closed = ref(false);
let elapsedTimer: ReturnType<typeof setInterval> | null = null;

const broadcastingHere = computed(
  () => broadcast.isActive && broadcast.outputId === props.stream.id,
);

const deviceOptions = computed(() => [
  { value: "", label: t("pages.streams.broadcast.defaultDevice") },
  ...broadcast.devices.map((device, index) => ({
    value: device.deviceId,
    label:
      device.label ||
      t("pages.streams.broadcast.unnamedDevice", { n: index + 1 }),
  })),
]);

const elapsed = computed(() => {
  if (broadcast.startedAt === null) return "0:00";
  const total = Math.max(
    0,
    Math.floor((now.value - broadcast.startedAt) / 1000),
  );
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const mm = String(minutes).padStart(hours > 0 ? 2 : 1, "0");
  const ss = String(seconds).padStart(2, "0");
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
});

const statusMessage = computed(() => {
  switch (broadcast.status) {
    case "requesting-permission":
      return t("pages.streams.broadcast.requestingPermission");
    case "connecting":
      return t("pages.streams.broadcast.connecting");
    case "on-air":
      return t("pages.streams.broadcast.onAir");
    case "error":
      return broadcast.error
        ? t(`pages.streams.broadcast.error.${broadcast.error.key}`)
        : "";
    default:
      return "";
  }
});

watch(
  () => props.open,
  (open) => {
    if (open) {
      closed.value = false;
      void prepare();
    }
  },
  { immediate: true },
);

watch(
  () => broadcast.status,
  (status) => {
    if (status === "on-air") {
      now.value = Date.now();
      elapsedTimer = setInterval(() => {
        now.value = Date.now();
      }, 1000);
    } else if (elapsedTimer !== null) {
      clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
    // Cancelling mid-connect or a stopped broadcast returns to the
    // pre-flight preview so the flow can be retried from the same modal.
    if (
      props.open &&
      !closed.value &&
      status === "idle" &&
      broadcast.outputId === null
    ) {
      void prepare();
    }
  },
);

onUnmounted(() => {
  if (elapsedTimer !== null) clearInterval(elapsedTimer);
});

async function prepare() {
  // Already on air for this stream from an earlier modal — just show the
  // live panel; the broadcast belongs to the store, not the modal.
  if (broadcastingHere.value) return;
  const result = await broadcast.ensurePermission();
  if (result === "granted" && !broadcast.isActive) {
    await broadcast.openPreview();
  }
}

function goLive() {
  void broadcast.start(props.stream, { title: title.value.trim() });
}

function cancel() {
  broadcast.stop();
}

function retry() {
  broadcast.reset();
  void prepare();
}

function onClose() {
  // Closing the modal while on air keeps broadcasting (the store owns the
  // state); a pre-flight preview is released so the mic light goes out.
  closed.value = true;
  if (!broadcast.isActive) broadcast.stop();
  emit("close");
}
</script>

<template>
  <AppModal
    :open="props.open"
    :title="t('pages.streams.broadcast.modalTitle')"
    @close="onClose"
  >
    <div class="live-broadcast">
      <p class="live-broadcast__stream">{{ stream.name }}</p>
      <p class="visually-hidden" role="status" aria-live="polite">
        {{ statusMessage }}
      </p>

      <div
        v-if="broadcast.status === 'requesting-permission'"
        class="live-broadcast__center"
      >
        <AppSpinner />
        <p>{{ t("pages.streams.broadcast.requestingPermission") }}</p>
        <p class="live-broadcast__hint">
          {{ t("pages.streams.broadcast.permissionPrompt") }}
        </p>
      </div>

      <div
        v-else-if="broadcast.permission === 'unsupported'"
        class="live-broadcast__center"
      >
        <AppIcon name="microphone-slash" />
        <p>{{ t("pages.streams.broadcast.unsupported") }}</p>
      </div>

      <div
        v-else-if="broadcast.permission === 'denied'"
        class="live-broadcast__center"
      >
        <AppIcon name="microphone-slash" />
        <p>{{ t("pages.streams.broadcast.permissionDenied") }}</p>
        <AppButton variant="secondary" @click="retry">
          {{ t("common.retry") }}
        </AppButton>
      </div>

      <div
        v-else-if="broadcast.status === 'error'"
        class="live-broadcast__center"
        role="alert"
      >
        <AppIcon name="triangle-exclamation" />
        <p>
          {{
            broadcast.error
              ? t(`pages.streams.broadcast.error.${broadcast.error.key}`)
              : t("pages.streams.broadcast.error.generic")
          }}
        </p>
        <AppButton variant="secondary" @click="retry">
          {{ t("common.retry") }}
        </AppButton>
      </div>

      <div
        v-else-if="broadcastingHere && broadcast.status !== 'stopping'"
        class="live-broadcast__body"
      >
        <div class="live-broadcast__on-air">
          <span class="live-broadcast__on-air-badge">
            <AppIcon name="circle" />
            {{ t("pages.streams.broadcast.onAir") }}
          </span>
          <span class="live-broadcast__elapsed">{{ elapsed }}</span>
          <span
            v-if="broadcast.listeners > 0"
            class="live-broadcast__listeners"
          >
            <AppIcon name="headphones" />
            {{ t("pages.streams.listeners", broadcast.listeners) }}
          </span>
        </div>
        <div
          class="live-broadcast__meter"
          role="meter"
          :aria-label="t('pages.streams.broadcast.levelMeter')"
          aria-valuemin="0"
          aria-valuemax="100"
          :aria-valuenow="Math.round(broadcast.level * 100)"
        >
          <div
            class="live-broadcast__meter-fill"
            :style="{ width: `${Math.min(100, broadcast.level * 100)}%` }"
          />
        </div>
        <p v-if="broadcast.slowConnection" class="live-broadcast__warning">
          {{ t("pages.streams.broadcast.slowConnection") }}
        </p>
        <p
          v-if="broadcast.status === 'connecting'"
          class="live-broadcast__hint"
        >
          {{ t("pages.streams.broadcast.connecting") }}
        </p>
      </div>

      <div v-else class="live-broadcast__form">
        <AppSelect
          :model-value="broadcast.deviceId"
          :options="deviceOptions"
          :label="t('pages.streams.broadcast.sourceLabel')"
          @update:model-value="broadcast.selectDevice"
        />
        <p v-if="broadcast.devices.length === 0" class="live-broadcast__hint">
          {{ t("pages.streams.broadcast.noDevices") }}
        </p>
        <div
          class="live-broadcast__meter"
          role="meter"
          :aria-label="t('pages.streams.broadcast.levelMeter')"
          aria-valuemin="0"
          aria-valuemax="100"
          :aria-valuenow="Math.round(broadcast.level * 100)"
        >
          <div
            class="live-broadcast__meter-fill"
            :style="{ width: `${Math.min(100, broadcast.level * 100)}%` }"
          />
        </div>
        <AppInput
          v-model="title"
          :label="t('pages.streams.broadcast.titleLabel')"
          :placeholder="t('pages.streams.broadcast.titlePlaceholder')"
          maxlength="256"
        />
        <AppCheckbox
          :model-value="broadcast.voiceMode"
          :label="t('pages.streams.broadcast.voiceMode')"
          :hint="t('pages.streams.broadcast.voiceModeHint')"
          @update:model-value="broadcast.setVoiceMode"
        />
        <p class="live-broadcast__hint">
          {{ t("pages.streams.broadcast.latencyHint") }}
        </p>
      </div>
    </div>

    <template #actions>
      <AppButton variant="ghost" @click="onClose">
        {{
          broadcast.isActive
            ? t("pages.streams.broadcast.keepBroadcasting")
            : t("common.cancel")
        }}
      </AppButton>
      <AppButton
        v-if="broadcastingHere"
        variant="danger"
        icon="stop"
        :loading="broadcast.status === 'stopping'"
        @click="cancel"
      >
        {{ t("pages.streams.broadcast.stop") }}
      </AppButton>
      <AppButton
        v-else-if="broadcast.status === 'ready'"
        icon="microphone"
        :disabled="broadcast.devices.length === 0"
        @click="goLive"
      >
        {{ t("pages.streams.broadcast.goLive") }}
      </AppButton>
      <AppButton
        v-else-if="broadcast.status === 'connecting'"
        variant="secondary"
        @click="cancel"
      >
        {{ t("common.cancel") }}
      </AppButton>
    </template>
  </AppModal>
</template>

<style scoped>
.live-broadcast {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.live-broadcast__stream {
  margin: 0;
  font-weight: 600;
}

.live-broadcast__center {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-4) 0;
  text-align: center;
}

.live-broadcast__form,
.live-broadcast__body {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.live-broadcast__on-air {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.live-broadcast__on-air-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: 0 var(--space-2);
  border-radius: var(--radius-full, 999px);
  border: 1px solid var(--color-danger);
  color: var(--color-danger);
  font-size: 0.875rem;
  font-weight: 600;
}

.live-broadcast__elapsed {
  font-variant-numeric: tabular-nums;
  color: var(--color-text-muted);
}

.live-broadcast__listeners {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.live-broadcast__meter {
  height: 0.5rem;
  border-radius: var(--radius-full, 999px);
  background-color: var(--color-surface-hover);
  overflow: hidden;
}

.live-broadcast__meter-fill {
  height: 100%;
  background-color: var(--color-accent);
  transition: width 80ms linear;
}

.live-broadcast__hint {
  margin: 0;
  color: var(--color-text-muted);
  font-size: 0.875rem;
}

.live-broadcast__warning {
  margin: 0;
  color: var(--color-warning);
  font-size: 0.875rem;
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
</style>
