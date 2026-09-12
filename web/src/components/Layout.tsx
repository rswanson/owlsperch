import type { ReactNode } from "react";
import { Link } from "react-router-dom";

interface LayoutProps {
  children: ReactNode;
}

export function Layout({ children }: LayoutProps) {
  return (
    <div className="page">
      <header className="site-header">
        <Link to="/" className="site-title">
          owlsperch
        </Link>
      </header>
      <main>{children}</main>
    </div>
  );
}
