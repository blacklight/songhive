<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import type { ActivityAttachment, ActivityResponse } from "@/api/activities";
import { useActivitiesStore } from "@/stores/activities";
import AppModal from "@/components/feedback/AppModal.vue";
import StatusComposer, {
  type StatusComposerPayload,
} from "@/components/statuses/StatusComposer.vue";

const props = defineProps<{ open: boolean; activity: ActivityResponse }>();
const emit = defineEmits<{ close: [] }>();

const { t } = useI18n();
const store = useActivitiesStore();

// User-managed attachments carry a ``songhive:`` source id in the AP doc
// (see ATTACHMENT_*_ID_KEY in songhive/federation/serializers.py); docs
// without one belong to the entity itself (e.g. a shared track's own
// Audio attachment) and are not editable through the composer.
const FILE_ID_KEY = "songhive:fileId";
const TRACK_ID_KEY = "songhive:trackId";

function attachmentName(attachment: ActivityAttachment): string {
  return typeof attachment.name === "string" ? attachment.name : "";
}

const initialMedia = computed(() =>
  (props.activity.attachments ?? [])
    .filter((a) => typeof a[FILE_ID_KEY] === "string")
    .map((a) => ({ id: a[FILE_ID_KEY] as string, name: attachmentName(a) })),
);

const initialTracks = computed(() =>
  (props.activity.attachments ?? [])
    .filter((a) => typeof a[TRACK_ID_KEY] === "string")
    .map((a) => ({ id: a[TRACK_ID_KEY] as string, title: attachmentName(a) })),
);

async function save(payload: StatusComposerPayload) {
  await store.update(props.activity.id, {
    content: payload.status,
    visibility: payload.visibility,
    content_type: payload.content_type,
    language: payload.language,
    media_ids: payload.media_ids,
    track_ids: payload.track_ids,
  });
}
</script>

<template>
  <AppModal
    :open="open"
    :title="t('activities.edit.title')"
    @close="emit('close')"
  >
    <!-- ``v-if`` remounts the composer on each open so initial values are
         re-seeded from the activity. -->
    <StatusComposer
      v-if="open"
      :submit="save"
      :submit-label="t('common.save')"
      :initial-status="activity.content_source ?? ''"
      :initial-content-type="activity.content_type"
      :initial-visibility="activity.visibility"
      :initial-language="activity.language"
      :initial-media="initialMedia"
      :initial-tracks="initialTracks"
      :allow-empty="activity.entity_type !== 'user'"
      autofocus
      @submitted="emit('close')"
    />
  </AppModal>
</template>
