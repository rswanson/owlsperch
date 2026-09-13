import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { getSchemas } from "../api";

interface LayoutProps {
  children: ReactNode;
}

interface NavType {
  type: string;
  pluralLabel: string;
}

/** The site header's nav (spec 4.10, batch B9 acceptance criterion 5): one
 * link per registered type (`GET /schemas`), each pointing at
 * `/browse/<type>`, sorted alphabetically by its plural label. Failing to
 * load the registry just leaves the nav empty -- the header/search/record
 * pages still work without it. */
export function Layout({ children }: LayoutProps) {
  const [navTypes, setNavTypes] = useState<NavType[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    getSchemas(controller.signal)
      .then((schemas) => {
        const types = Object.entries(schemas.types)
          .map(([type, info]) => ({ type, pluralLabel: info.plural_label }))
          .sort((a, b) => a.pluralLabel.localeCompare(b.pluralLabel));
        setNavTypes(types);
      })
      .catch(() => {
        setNavTypes([]);
      });
    return () => controller.abort();
  }, []);

  return (
    <div className="page">
      <header className="site-header">
        <Link to="/" className="site-title">
          owlsperch
        </Link>
        {navTypes.length > 0 && (
          <nav className="site-nav" aria-label="Browse">
            {navTypes.map((navType) => (
              <Link key={navType.type} to={`/browse/${navType.type}`}>
                {navType.pluralLabel}
              </Link>
            ))}
          </nav>
        )}
      </header>
      <main>{children}</main>
    </div>
  );
}
