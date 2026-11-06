"""Read-only search over the Soundly desktop app's local library (LocalFilesDB).

The database layer is adapted from soundly-mcp by Gian-carlo Lacanilao
(https://github.com/1800doublegee/soundly-mcp, MIT License). Changes here: several
phrasings per search merged into one ranking, stop-word and plural handling, and
weighting so description/tag hits outrank matches buried in the search blob.

Schema (LocalFilesDB, SQLite 3):
    Files(Id, TimeCreated, Description, CategoryID, Tags, Length, Location,
          SampleRate, BitRate, Channels, Format, LibraryID, SearchString, ...)
    Libraries(Id, TimeCreated, LibraryID, Name, Categories, Path, DirList, Monitor)
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any


def default_db_path() -> str:
    env = os.environ.get("SOUNDLY_DB")
    if env:
        return str(Path(env).expanduser())
    home = Path.home()
    mac = home / "Library" / "Application Support" / "Soundly" / "LocalFilesDB"
    win = home / "Soundly Data" / "LocalFilesDB"
    if not mac.exists() and win.exists():
        return str(win)
    return str(mac)


COLUMNS = ["Id", "Description", "CategoryID", "Tags", "Length", "Location",
           "SampleRate", "BitRate", "Channels", "Format", "LibraryID"]

# field -> weight; description and tags are curated, the search blob is broad
FIELD_WEIGHTS = {"Description": 3, "Tags": 3, "CategoryID": 2, "SearchString": 1}

STOPWORDS = {"a", "an", "the", "of", "and", "or", "with", "for", "to", "in", "on", "some", "me", "find",
             "sound", "sounds", "sfx", "effect", "effects", "like", "kind", "that", "very"}


def _tokens(query: str) -> list[str]:
    out = []
    for raw in query.lower().replace(",", " ").split():
        t = raw.strip("\"'()[]")
        if not t or t in STOPWORDS:
            continue
        # crude plural folding so 'pines' finds 'pine' and vice versa via LIKE
        if len(t) > 4 and t.endswith("es") and not t.endswith("ses"):
            t = t[:-2]
        elif len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return out


class SoundlyDB:
    def __init__(self, path: str | None = None):
        self.path = path or default_db_path()
        if not os.path.exists(self.path):
            raise FileNotFoundError(
                f"Soundly's library database wasn't found at {self.path}. Open Soundly and add a local "
                "library, or set SOUNDLY_DB to the LocalFilesDB file."
            )
        uri = f"file:{Path(self.path).as_posix()}?mode=ro"
        self._con = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self._con.row_factory = sqlite3.Row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._con.close()

    # ---- introspection -------------------------------------------------
    def count(self) -> int:
        return self._con.execute("SELECT COUNT(*) FROM Files").fetchone()[0]

    def libraries(self) -> list[dict[str, Any]]:
        rows = self._con.execute("SELECT Name, Path, Categories, Monitor FROM Libraries ORDER BY Name").fetchall()
        return [dict(r) for r in rows]

    def categories(self) -> list[dict[str, Any]]:
        rows = self._con.execute(
            "SELECT CategoryID AS category, COUNT(*) AS count FROM Files "
            "WHERE CategoryID IS NOT NULL AND CategoryID != '' "
            "GROUP BY CategoryID ORDER BY count DESC, category ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- search --------------------------------------------------------
    def _search_one(self, query: str, filters: dict, limit: int) -> list[dict]:
        tokens = _tokens(query)
        if not tokens:
            return []
        score_terms, score_params, where_terms, where_params = [], [], [], []
        for tok in tokens:
            like = f"%{tok}%"
            ors = []
            for field, w in FIELD_WEIGHTS.items():
                score_terms.append(f"(CASE WHEN {field} LIKE ? THEN {w} ELSE 0 END)")
                score_params.append(like)
                ors.append(f"{field} LIKE ?")
                where_params.append(like)
            where_terms.append("(" + " OR ".join(ors) + ")")
        clauses = ["(" + " OR ".join(where_terms) + ")"]
        params: list[Any] = []
        if filters.get("category"):
            clauses.append("CategoryID LIKE ?"); params.append(f"%{filters['category']}%")
        if filters.get("fmt"):
            clauses.append("Format LIKE ?"); params.append(f"%{filters['fmt']}%")
        if filters.get("max_length") is not None:
            clauses.append("Length <= ?"); params.append(filters["max_length"])
        if filters.get("min_length") is not None:
            clauses.append("Length >= ?"); params.append(filters["min_length"])
        if filters.get("min_sample_rate") is not None:
            clauses.append("SampleRate >= ?"); params.append(filters["min_sample_rate"])
        if filters.get("channels") is not None:
            clauses.append("Channels = ?"); params.append(filters["channels"])

        # tokens matched (for coverage) = count of tokens with any hit
        coverage = " + ".join(
            "(CASE WHEN " + " OR ".join(f"{f} LIKE ?" for f in FIELD_WEIGHTS) + " THEN 1 ELSE 0 END)"
            for _ in tokens)
        coverage_params = [f"%{t}%" for t in tokens for _ in FIELD_WEIGHTS]

        sql = (f"SELECT {', '.join(COLUMNS)}, ({' + '.join(score_terms)}) AS score, ({coverage}) AS matched "
               f"FROM Files WHERE {' AND '.join(clauses)} "
               f"ORDER BY matched DESC, score DESC, (Length IS NULL), Length ASC LIMIT ?")
        rows = self._con.execute(sql, score_params + coverage_params + where_params + params + [limit]).fetchall()
        return [dict(r) | {"tokens": len(tokens)} for r in rows]

    def search(self, queries: list[str], limit: int = 25, **filters) -> list[dict]:
        """Run several phrasings and merge: sounds found by more phrasings and matching
        more of each phrasing's words rank first."""
        merged: dict[int, dict] = {}
        for q in queries:
            for r in self._search_one(q, filters, limit * 2):
                rel = r["matched"] / r["tokens"] + r["score"] / (10 * r["tokens"])
                m = merged.setdefault(r["Id"], {k: r[k] for k in COLUMNS} | {"relevance": 0.0, "matched_queries": []})
                m["relevance"] += rel
                m["matched_queries"].append(q)
        ranked = sorted(merged.values(), key=lambda s: (-s["relevance"], s["Length"] is None, s["Length"] or 0))
        for s in ranked:
            s["relevance"] = round(s["relevance"], 2)
        return ranked[:limit]

    def get(self, sound_id: int) -> dict | None:
        r = self._con.execute(f"SELECT {', '.join(COLUMNS)} FROM Files WHERE Id = ?", (sound_id,)).fetchone()
        return dict(r) if r else None

    # ---- organise ------------------------------------------------------
    def copy_file(self, sound_id: int, dest: str | Path, rename: bool = True) -> Path:
        s = self.get(sound_id)
        if not s or not s.get("Location"):
            raise ValueError(f"no Soundly record with a file location for id {sound_id}")
        src = Path(s["Location"])
        if not src.exists():
            raise ValueError(f"sound {sound_id} isn't on disk at {src} (download it in Soundly first)")
        out = Path(dest).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        name = src.name
        if rename and s.get("Description"):
            safe = "".join(c if c.isalnum() or c in " -_." else "_" for c in s["Description"]).strip()
            if safe:
                name = f"{safe[:60]}{src.suffix}"
        target = out / name
        i = 1
        while target.exists():
            target = out / f"{Path(name).stem}_{i}{src.suffix}"
            i += 1
        shutil.copy2(src, target)
        return target
