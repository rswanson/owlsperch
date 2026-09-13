import { useMemo } from "react";
import { Link } from "react-router-dom";
import type { BrowseItem, FacetValue } from "../api";

/** The pseudo-group label used at the chapter or section level when an
 * item has no chapter/section (design decision D13). */
const UNSECTIONED = "(Unsectioned)";

export interface TreeItem {
  id: string;
  type: string;
  name: string;
  slug: string;
  citation: string | null;
}

export interface TreeSectionGroup {
  key: string;
  label: string;
  count: number;
  items: TreeItem[];
}

export interface TreeChapterGroup {
  key: string;
  label: string;
  count: number;
  sections: TreeSectionGroup[];
}

export interface TreeCategoryGroup {
  key: string;
  label: string;
  count: number;
  chapters: TreeChapterGroup[];
}

function toTreeItem(item: BrowseItem): TreeItem {
  return { id: item.id, type: item.type, name: item.name, slug: item.slug, citation: item.citation };
}

interface Bucket {
  minPage: number;
  items: BrowseItem[];
}

/** Groups `items` into category -> chapter -> section -> records (design
 * decision D13): categories ordered by `categoryOrder` (the `category`
 * facet's own value order, itself ordered by `schemas/categories.json`);
 * chapters and sections ordered by their group's minimum `page`, then by
 * title; records with no chapter (or no section) grouped under an
 * "(Unsectioned)" leaf at that level. Pure and independently unit-tested,
 * following the `FieldGroups.tsx` / `buildFieldGroups` pattern. */
export function buildTree(items: BrowseItem[], categoryOrder: FacetValue[]): TreeCategoryGroup[] {
  const byCategory = new Map<string, Map<string, Map<string, Bucket>>>();
  const categoryLabels = new Map<string, string>();

  for (const item of items) {
    const category = item.toc.category;
    const chapter = item.toc.chapter ?? UNSECTIONED;
    const section = item.toc.section ?? UNSECTIONED;
    categoryLabels.set(category, item.toc.category_label);

    let chapters = byCategory.get(category);
    if (!chapters) {
      chapters = new Map();
      byCategory.set(category, chapters);
    }
    let sections = chapters.get(chapter);
    if (!sections) {
      sections = new Map();
      chapters.set(chapter, sections);
    }
    let bucket = sections.get(section);
    if (!bucket) {
      bucket = { minPage: Number.POSITIVE_INFINITY, items: [] };
      sections.set(section, bucket);
    }
    bucket.items.push(item);
    const page = item.page ?? Number.POSITIVE_INFINITY;
    if (page < bucket.minPage) bucket.minPage = page;
  }

  const orderIndex = new Map(categoryOrder.map((c, i) => [c.value, i]));
  const categoryKeys = [...byCategory.keys()].sort((a, b) => {
    const ai = orderIndex.get(a) ?? Number.MAX_SAFE_INTEGER;
    const bi = orderIndex.get(b) ?? Number.MAX_SAFE_INTEGER;
    if (ai !== bi) return ai - bi;
    return a.localeCompare(b);
  });

  return categoryKeys.map((categoryKey) => {
    const chaptersMap = byCategory.get(categoryKey);
    if (!chaptersMap) throw new Error("unreachable: category key came from byCategory's own keys");

    type RankedSection = TreeSectionGroup & { minPage: number };
    type RankedChapter = TreeChapterGroup & { minPage: number };

    const rankedChapters: RankedChapter[] = [...chaptersMap.entries()]
      .map(([chapterKey, sectionsMap]) => {
        const rankedSections: RankedSection[] = [...sectionsMap.entries()]
          .map(([sectionKey, bucket]) => {
            const items = [...bucket.items].sort((a, b) => a.name.localeCompare(b.name));
            return {
              key: sectionKey,
              label: sectionKey,
              count: items.length,
              minPage: bucket.minPage,
              items: items.map(toTreeItem),
            };
          })
          .sort((a, b) => a.minPage - b.minPage || a.label.localeCompare(b.label));

        return {
          key: chapterKey,
          label: chapterKey,
          count: rankedSections.reduce((sum, s) => sum + s.count, 0),
          minPage: Math.min(...rankedSections.map((s) => s.minPage)),
          sections: rankedSections,
        };
      })
      .sort((a, b) => a.minPage - b.minPage || a.label.localeCompare(b.label));

    return {
      key: categoryKey,
      label: categoryLabels.get(categoryKey) ?? categoryKey,
      count: rankedChapters.reduce((sum, c) => sum + c.count, 0),
      chapters: rankedChapters,
    };
  });
}

interface RecordTreeProps {
  items: BrowseItem[];
  categoryOrder: FacetValue[];
  /** When exactly one category is selected in the URL, its `<details>`
   * renders open by default (design decision D13). */
  autoExpandCategory?: string | null;
}

/** The `/browse/:type?view=tree` tree (design decisions D13-D14): nested,
 * collapsible `<details>`/`<summary>` groups (accessible, and clickable by
 * Playwright via `getByText` on the summary) for category -> chapter ->
 * section -> records. */
export function RecordTree({ items, categoryOrder, autoExpandCategory = null }: RecordTreeProps) {
  const tree = useMemo(() => buildTree(items, categoryOrder), [items, categoryOrder]);

  if (tree.length === 0) return <p className="search-status">No results.</p>;

  return (
    <div className="record-tree">
      {tree.map((category) => (
        <details
          key={category.key}
          className="tree-category"
          open={autoExpandCategory === category.key}
        >
          <summary>
            {category.label} <span className="tree-count">({category.count})</span>
          </summary>
          <div className="tree-children">
            {category.chapters.map((chapter) => (
              <details key={chapter.key} className="tree-chapter">
                <summary>
                  {chapter.label} <span className="tree-count">({chapter.count})</span>
                </summary>
                <div className="tree-children">
                  {chapter.sections.map((section) => (
                    <details key={section.key} className="tree-section">
                      <summary>
                        {section.label} <span className="tree-count">({section.count})</span>
                      </summary>
                      <ul className="tree-items">
                        {section.items.map((item) => (
                          <li key={item.id}>
                            <Link to={`/r/${item.type}/${item.slug}`}>{item.name}</Link>
                            {item.citation && (
                              <span className="browse-result-citation"> {item.citation}</span>
                            )}
                          </li>
                        ))}
                      </ul>
                    </details>
                  ))}
                </div>
              </details>
            ))}
          </div>
        </details>
      ))}
    </div>
  );
}
