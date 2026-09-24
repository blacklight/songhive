<script setup lang="ts">
import { ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import {
  listRemoteObjects,
  type RemoteObject,
  type RemoteResourceKind,
} from "@/api/remote";
import { removeFromCollection as removeFromCollectionApi } from "@/api/collection";
import RemoteObjectList from "@/components/library/RemoteObjectList.vue";

/**
 * Collected remote (federated) resources of one kind — rendered inside the
 * per-type browse views while the "my collection" filter is active, since
 * remote objects never join the local entity tables the grid queries. The
 * backend expands the collection closure: a collected remote track also
 * surfaces its cached album and artist here.
 */
const props = defineProps<{
  kind: RemoteResourceKind;
  /** Whether the "my collection" filter is on — the section only renders then. */
  active: boolean;
}>();

const { t } = useI18n();

const items = ref<RemoteObject[]>([]);
const loaded = ref(false);

async function load() {
  if (!props.active) {
    items.value = [];
    loaded.value = false;
    return;
  }
  loaded.value = false;
  try {
    const res = await listRemoteObjects({
      resource_type: props.kind,
      collection: true,
      limit: 50,
    });
    items.value = res.items;
  } catch {
    items.value = [];
  } finally {
    loaded.value = true;
  }
}

async function removeFromCollection(item: RemoteObject) {
  await removeFromCollectionApi("remote", item.id);
  items.value = items.value.filter((i) => i.id !== item.id);
}

watch(() => [props.kind, props.active], load, { immediate: true });
</script>

<template>
  <section
    v-if="loaded && items.length"
    class="remote-collection"
    data-testid="remote-collection"
  >
    <h2 class="remote-collection__title">
      {{ t("remote.collectedTitle", { kind: t(`remote.kinds.${kind}`) }) }}
    </h2>
    <RemoteObjectList :items="items" removable @remove="removeFromCollection" />
  </section>
</template>

<style scoped>
.remote-collection {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  max-width: 60rem;
}

.remote-collection__title {
  margin: 0;
  font-size: 1.125rem;
}
</style>
