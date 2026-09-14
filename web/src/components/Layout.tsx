import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { getFacets, getSchemas } from "../api";

interface LayoutProps {
  children: ReactNode;
}

interface NavType {
  type: string;
  pluralLabel: string;
}

interface QuickLink {
  key: string;
  label: string;
}

/** Categories that today hold content mixed into `rules_section` but will
 * later become their own record type (batch B10b, design decision D14) --
 * the header nav gets a quick link into each one that actually has any
 * records this build. "classes" is deliberately absent (batch B10c): class
 * is now its own registered type with its own nav link (`Layout` already
 * renders one per registered type below), so a rules quick-link into it
 * too would just duplicate that -- and the class-chapter rules_section
 * fragments it used to point at are non-canonical (superseded) now
 * anyway. */
const QUICK_LINK_CATEGORIES = ["equipment", "skills", "races"];

/** The site header's nav (spec 4.10, batch B9 acceptance criterion 5): one
 * link per registered type (`GET /schemas`) EXCEPT `rules_section`, each
 * pointing at `/browse/<type>`, sorted alphabetically by its plural label.
 * `rules_section` (when registered) instead gets a dedicated "Rules" link
 * plus quick links into whichever of `QUICK_LINK_CATEGORIES` have records
 * (batch B10b, D14): `/facets/rules_section`'s `category` facet, fetched
 * once, already carries both the presence check (count > 0) and the
 * display label. Either fetch failing just leaves that part of the nav
 * empty -- the header/search/record pages still work without it. */
export function Layout({ children }: LayoutProps) {
  const [navTypes, setNavTypes] = useState<NavType[]>([]);
  const [hasRulesSection, setHasRulesSection] = useState(false);
  const [quickLinks, setQuickLinks] = useState<QuickLink[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    getSchemas(controller.signal)
      .then((schemas) => {
        const types = Object.entries(schemas.types)
          .filter(([type]) => type !== "rules_section")
          .map(([type, info]) => ({ type, pluralLabel: info.plural_label }))
          .sort((a, b) => a.pluralLabel.localeCompare(b.pluralLabel));
        setNavTypes(types);
        setHasRulesSection("rules_section" in schemas.types);
      })
      .catch(() => {
        setNavTypes([]);
        setHasRulesSection(false);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!hasRulesSection) return;
    const controller = new AbortController();
    getFacets("rules_section", new URLSearchParams(), controller.signal)
      .then((facetsResponse) => {
        const categoryFacet = facetsResponse.facets.find((f) => f.field === "category");
        const links = (categoryFacet?.values ?? [])
          .filter((v) => QUICK_LINK_CATEGORIES.includes(v.value) && v.count > 0)
          .map((v) => ({ key: v.value, label: v.label ?? v.value }));
        setQuickLinks(links);
      })
      .catch(() => {
        setQuickLinks([]);
      });
    return () => controller.abort();
  }, [hasRulesSection]);

  return (
    <div className="page">
      <header className="site-header">
        <Link to="/" className="site-title">
          owlsperch
        </Link>
        {(navTypes.length > 0 || hasRulesSection) && (
          <nav className="site-nav" aria-label="Browse">
            {navTypes.map((navType) => (
              <Link key={navType.type} to={`/browse/${navType.type}`}>
                {navType.pluralLabel}
              </Link>
            ))}
            {hasRulesSection && (
              <span className="site-nav-rules">
                <Link to="/browse/rules_section">Rules</Link>
                {quickLinks.length > 0 && (
                  <span className="site-nav-quicklinks">
                    {quickLinks.map((quickLink) => (
                      <Link
                        key={quickLink.key}
                        to={`/browse/rules_section?category=${quickLink.key}`}
                      >
                        {quickLink.label}
                      </Link>
                    ))}
                  </span>
                )}
              </span>
            )}
          </nav>
        )}
      </header>
      <main>{children}</main>
    </div>
  );
}
