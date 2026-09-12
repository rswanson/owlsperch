import { Link } from "react-router-dom";
import type { SearchGroup, SearchHit } from "../api";

interface ResultGroupProps {
  group: SearchGroup;
  activeHit: SearchHit | null;
  onHover: (hit: SearchHit) => void;
}

export function ResultGroup({ group, activeHit, onHover }: ResultGroupProps) {
  return (
    <li className="result-group">
      <div className="result-group-label">{group.label}</div>
      <ul>
        {group.hits.map((hit) => (
          <li key={hit.id}>
            <Link
              to={`/r/${hit.type}/${hit.slug}`}
              className={
                "result-hit" + (activeHit?.id === hit.id ? " result-hit-active" : "")
              }
              role="option"
              aria-selected={activeHit?.id === hit.id}
              onMouseEnter={() => {
                onHover(hit);
              }}
            >
              <span className="type-badge">{group.type}</span>
              <span className="result-hit-name">{hit.name}</span>
              {hit.citation && <span className="result-hit-citation">{hit.citation}</span>}
            </Link>
          </li>
        ))}
      </ul>
    </li>
  );
}
