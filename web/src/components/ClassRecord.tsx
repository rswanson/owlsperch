import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { browseRecords, type BrowseItem, type ClassFields, type RecordDetail } from "../api";
import { RecordTables } from "./RecordTables";

interface ClassRecordProps {
  record: RecordDetail;
}

const PAGE_SIZE = 200;

/** Fetches every spell record whose `levels[].class` includes `spellList`,
 * paging through `GET /records/spell?class=<spellList>` until every result
 * has been collected. A pure async helper (not inlined in the effect) so
 * it's easy to call from a test without rendering. */
export async function fetchSpellsForClass(
  spellList: string,
  signal?: AbortSignal,
): Promise<BrowseItem[]> {
  const collected: BrowseItem[] = [];
  let page = 1;
  for (;;) {
    const params = new URLSearchParams({
      class: spellList,
      page_size: String(PAGE_SIZE),
      page: String(page),
    });
    // Pages must be fetched in order (there's no way to know the total
    // page count up front), so a sequential await in this loop is correct.
    const response = await browseRecords("spell", params, signal);
    collected.push(...response.items);
    if (response.items.length === 0 || collected.length >= response.total) break;
    page += 1;
  }
  return collected;
}

/** The level a `BrowseItem` was cast at by `spellList`, parsed from its
 * `facets.levels` combined values (`"Wizard 1"`, `"Cleric 3"`, ...) --
 * `/records/spell` items carry `facets` as `{key: [values]}`, and `levels`
 * holds the array-of-object field's COMBINED rows (`build_db.runner
 * .flatten_fields`), not the per-sub-property ones. Returns `null` if this
 * item has no `levels` entry for `spellList` at all (shouldn't happen for
 * anything the `class=` filter itself returned, but defensive all the
 * same). */
export function levelForClass(item: BrowseItem, spellList: string): number | null {
  const prefix = `${spellList} `;
  for (const combined of item.facets.levels ?? []) {
    if (combined.startsWith(prefix)) {
      const level = Number(combined.slice(prefix.length));
      if (!Number.isNaN(level)) return level;
    }
  }
  return null;
}

/** Groups spell `BrowseItem`s by `levelForClass`, sorted by level then
 * name -- the shape `ClassRecord`'s Spells section renders. */
export function groupSpellsByLevel(
  items: BrowseItem[],
  spellList: string,
): { level: number; spells: BrowseItem[] }[] {
  const byLevel = new Map<number, BrowseItem[]>();
  for (const item of items) {
    const level = levelForClass(item, spellList);
    if (level === null) continue;
    const group = byLevel.get(level);
    if (group) {
      group.push(item);
    } else {
      byLevel.set(level, [item]);
    }
  }
  return [...byLevel.entries()]
    .sort(([a], [b]) => a - b)
    .map(([level, spells]) => ({
      level,
      spells: [...spells].sort((a, b) => a.name.localeCompare(b.name)),
    }));
}

function SpellsSection({ spellcasting }: { spellcasting: ClassFields["spellcasting"] }) {
  const [groups, setGroups] = useState<{ level: number; spells: BrowseItem[] }[] | null>(null);

  useEffect(() => {
    if (!spellcasting) {
      setGroups(null);
      return;
    }
    const controller = new AbortController();
    fetchSpellsForClass(spellcasting.spell_list, controller.signal)
      .then((items) => setGroups(groupSpellsByLevel(items, spellcasting.spell_list)))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setGroups([]);
      });
    return () => controller.abort();
  }, [spellcasting]);

  // Render nothing (not an error) when this class doesn't cast spells.
  if (!spellcasting) return null;
  if (groups === null) return null;
  if (groups.length === 0) return null;

  return (
    <section className="class-spells">
      <h2>Spells</h2>
      {groups.map((group) => (
        <div className="class-spells-level" key={group.level}>
          <h3>Level {group.level}</h3>
          <ul>
            {group.spells.map((spell) => (
              <li key={spell.id}>
                <Link to={`/r/spell/${spell.slug}`}>{spell.name}</Link>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}

/** Renders a `class`/`prestige_class` record's structured page (batch
 * B10c, design decision D13), in place of the generic `FieldGroups` +
 * `text_md` + `RecordTables` body `RecordPage` uses for every other type:
 * header facts, description sections, class skills, weapon and armor
 * proficiency, the progression table (via the existing `RecordTables`,
 * fed from `record.tables` -- already resolved by the server), class
 * features, and (for a caster) a live Spells section. */
export function ClassRecord({ record }: ClassRecordProps) {
  const fields = record.fields as ClassFields;

  return (
    <div className="class-record">
      <dl className="class-facts">
        {fields.hit_die && (
          <div className="class-fact">
            <dt>Hit Die</dt>
            <dd>{fields.hit_die}</dd>
          </div>
        )}
        {fields.alignment && (
          <div className="class-fact">
            <dt>Alignment</dt>
            <dd>{fields.alignment}</dd>
          </div>
        )}
        {fields.bab_progression && (
          <div className="class-fact">
            <dt>Base Attack Bonus</dt>
            <dd>{fields.bab_progression}</dd>
          </div>
        )}
        {fields.save_progressions && (
          <div className="class-fact">
            <dt>Saves</dt>
            <dd>
              Fort {fields.save_progressions.fort}, Ref {fields.save_progressions.ref}, Will{" "}
              {fields.save_progressions.will}
            </dd>
          </div>
        )}
        {fields.skill_points && (
          <div className="class-fact">
            <dt>Skill Points</dt>
            <dd>
              {fields.skill_points.base} + {fields.skill_points.ability} modifier
              {typeof fields.skill_points.first_level_multiplier === "number"
                ? ` (×${fields.skill_points.first_level_multiplier} at 1st level)`
                : ""}
            </dd>
          </div>
        )}
        {fields.requirements && fields.requirements.length > 0 && (
          <div className="class-fact">
            <dt>Requirements</dt>
            <dd>
              <ul className="class-requirements">
                {fields.requirements.map((req, i) => (
                  <li key={i}>
                    {req.kind}: {req.text}
                  </li>
                ))}
              </ul>
            </dd>
          </div>
        )}
      </dl>

      {record.text_md && (
        <div className="text-md">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{record.text_md}</ReactMarkdown>
        </div>
      )}

      {fields.description_sections?.map((section, i) => (
        <section className="class-description-section" key={i}>
          <h2>{section.heading}</h2>
          <div className="text-md">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{section.text_md}</ReactMarkdown>
          </div>
        </section>
      ))}

      {fields.class_skills && fields.class_skills.length > 0 && (
        <section className="class-skills">
          <h2>Class Skills</h2>
          <ul>
            {fields.class_skills.map((cs, i) => (
              <li key={i}>
                {cs.skill} ({cs.key_ability})
              </li>
            ))}
          </ul>
        </section>
      )}

      {fields.weapon_and_armor_proficiency && (
        <section className="class-proficiency">
          <h2>Weapon and Armor Proficiency</h2>
          <p>{fields.weapon_and_armor_proficiency}</p>
        </section>
      )}

      {record.tables.length > 0 && (
        <section className="class-progression">
          <h2>Class Progression</h2>
          <RecordTables tables={record.tables} />
        </section>
      )}

      {fields.class_features && fields.class_features.length > 0 && (
        <section className="class-features">
          <h2>Class Features</h2>
          {fields.class_features.map((feature, i) => (
            <div className="class-feature" key={i}>
              <h3>
                {feature.name} <span className="class-feature-level">(Level {feature.level})</span>
              </h3>
              {feature.text_md && (
                <div className="text-md">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{feature.text_md}</ReactMarkdown>
                </div>
              )}
            </div>
          ))}
        </section>
      )}

      <SpellsSection spellcasting={fields.spellcasting} />
    </div>
  );
}
