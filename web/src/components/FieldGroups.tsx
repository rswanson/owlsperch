import { useMemo } from "react";
import type { SchemaField } from "../api";

interface FieldGroupsProps {
  fields: Record<string, unknown>;
  schemaFields: SchemaField[];
}

interface FieldRow {
  key: string;
  label: string;
  order: number;
  value: string;
}

interface FieldGroup {
  name: string;
  order: number;
  rows: FieldRow[];
}

/** Renders one field's value as display text, or `null` when it should be
 * hidden (null, undefined, empty string, empty array/object). Arrays of
 * scalars render as a comma list; arrays of objects (e.g. spell `levels`,
 * `[{class, level}]`) render each object's own values space-joined, then
 * comma-joined across the array ("Cleric 3, Wizard 3"); plain nested objects
 * (e.g. spell `costs`) render as `key: value` pairs, comma-joined, skipping
 * null/empty sub-fields. */
export function formatFieldValue(value: unknown): string | null {
  if (value === null || value === undefined) return null;

  if (Array.isArray(value)) {
    const nonEmpty = value.filter((item) => item !== null && item !== undefined && item !== "");
    if (nonEmpty.length === 0) return null;
    if (typeof nonEmpty[0] === "object") {
      const parts = nonEmpty
        .map((item) =>
          Object.values(item as Record<string, unknown>)
            .filter((v) => v !== null && v !== undefined && v !== "")
            .map(String)
            .join(" "),
        )
        .filter((part) => part.length > 0);
      return parts.length > 0 ? parts.join(", ") : null;
    }
    return nonEmpty.map(String).join(", ");
  }

  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).filter(
      ([, v]) => v !== null && v !== undefined && v !== "",
    );
    if (entries.length === 0) return null;
    return entries.map(([k, v]) => `${k}: ${String(v)}`).join(", ");
  }

  if (value === "") return null;
  return String(value);
}

/** Groups `schemaFields` by their `x-ui.group` hint, ordered by the group's
 * lowest member `order` and then by each field's own `order` within the
 * group, and drops any field whose value is null/empty. */
export function buildFieldGroups(
  fields: Record<string, unknown>,
  schemaFields: SchemaField[],
): FieldGroup[] {
  const byGroup = new Map<string, FieldGroup>();

  for (const schemaField of schemaFields) {
    const hint = schemaField["x-ui"] ?? {};
    const displayValue = formatFieldValue(fields[schemaField.name]);
    if (displayValue === null) continue;

    const groupName = hint.group ?? "other";
    const order = hint.order ?? 0;
    let group = byGroup.get(groupName);
    if (!group) {
      group = { name: groupName, order, rows: [] };
      byGroup.set(groupName, group);
    }
    group.order = Math.min(group.order, order);
    group.rows.push({
      key: schemaField.name,
      label: hint.label ?? schemaField.name,
      order,
      value: displayValue,
    });
  }

  return [...byGroup.values()]
    .sort((a, b) => a.order - b.order)
    .map((group) => ({ ...group, rows: [...group.rows].sort((a, b) => a.order - b.order) }));
}

export function FieldGroups({ fields, schemaFields }: FieldGroupsProps) {
  const groups = useMemo(() => buildFieldGroups(fields, schemaFields), [fields, schemaFields]);

  if (groups.length === 0) return null;

  return (
    <div className="field-groups">
      {groups.map((group) => (
        <dl className="field-group" key={group.name}>
          {group.rows.map((row) => (
            <div className="field-row" key={row.key}>
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
      ))}
    </div>
  );
}
