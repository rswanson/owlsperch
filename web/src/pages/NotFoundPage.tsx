import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="not-found">
      <h1>Not found</h1>
      <p>That record doesn&apos;t exist.</p>
      <Link to="/">Back to search</Link>
    </div>
  );
}
