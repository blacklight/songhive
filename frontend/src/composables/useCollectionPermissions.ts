import { computed, toValue, type ComputedRef, type MaybeRef } from "vue";

/**
 * Server-provided capability flags on playlist/library responses.
 *
 * Clients must gate edit/manage affordances on these fields rather than
 * inferring rights from ``owner_id`` — collaborator grants give edit access
 * without ownership.
 */
export interface CollectionPermissions {
  can_write?: boolean;
  can_manage?: boolean;
  is_collaborator?: boolean;
  share_grant_id?: string | null;
}

export function useCollectionPermissions(
  collection: MaybeRef<CollectionPermissions | null | undefined>,
) {
  /** The requester may edit metadata and contents (owner, admin, collaborator). */
  const canWrite: ComputedRef<boolean> = computed(
    () => !!toValue(collection)?.can_write,
  );
  /** The requester may delete, change visibility, manage grants, or sync. */
  const canManage: ComputedRef<boolean> = computed(
    () => !!toValue(collection)?.can_manage,
  );
  /** The requester's access comes from a collaborator share grant. */
  const isCollaborator: ComputedRef<boolean> = computed(
    () => !!toValue(collection)?.is_collaborator,
  );
  /** The requester's own share grant id — enables the "leave" action. */
  const shareGrantId: ComputedRef<string | null> = computed(
    () => toValue(collection)?.share_grant_id ?? null,
  );

  return { canWrite, canManage, isCollaborator, shareGrantId };
}
