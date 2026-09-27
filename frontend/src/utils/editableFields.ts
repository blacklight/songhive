/**
 * ``editable_fields`` semantics on provider-backed entities: ``null`` /
 * ``undefined`` means the entity is not provider-managed and every field is
 * editable; a list restricts edits to the named capabilities. ``visibility``
 * is local ACL state and always stays editable (mirrors
 * ``enforce_editable_fields`` in ``api/routes/_common.py``).
 */
const ALWAYS_EDITABLE = new Set(["visibility"]);

export function isProviderManaged(
  editableFields: string[] | null | undefined,
): boolean {
  return editableFields != null;
}

export function isFieldEditable(
  editableFields: string[] | null | undefined,
  field: string,
): boolean {
  if (editableFields == null) return true;
  return editableFields.includes(field) || ALWAYS_EDITABLE.has(field);
}

/**
 * Capability names present in ``editableFields``; ``null`` yields ``null``
 * so callers can distinguish "unrestricted" from an explicit list.
 */
export function disabledFieldSet(
  editableFields: string[] | null | undefined,
  allFields: string[],
): Set<string> {
  if (editableFields == null) return new Set();
  const allowed = new Set(editableFields);
  return new Set(
    allFields.filter(
      (field) => !allowed.has(field) && !ALWAYS_EDITABLE.has(field),
    ),
  );
}
