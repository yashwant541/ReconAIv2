"""A library of pre-processed 'reference' documents to reconcile against.

Storage backends
----------------
* Local filesystem  — a directory of JSON files (default; good for dev).
* Dataiku managed folder — reads/writes through the Dataiku folder API, so it
  works whether the folder is backed by local disk, S3, GCS or Azure.

Selection is by environment variable (see `from_config`):
    FINRECON_DATAIKU_FOLDER   managed-folder name or id  -> Dataiku backend
    FINRECON_DATAIKU_PROJECT  (optional) project key for the folder
    FINRECON_LIBRARY          local directory            -> local backend
Each entry is a processed-document JSON (see api.export_processed). A global
synonyms list is stored alongside as "_synonyms.<ext>".
"""
from __future__ import annotations

import io
import json
import os
import re
from typing import Dict, List, Optional, Tuple

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(name: str) -> str:
    return _SAFE.sub("_", name.strip()).strip("_") or "reference"


# --------------------------------------------------------------------------- #
# Storage backends (same tiny interface: list_files / read / write / delete)
# --------------------------------------------------------------------------- #
class _LocalStore:
    def __init__(self, root: str) -> None:
        self.root = root
        os.makedirs(root, exist_ok=True)

    def list_files(self) -> List[str]:
        return [f for f in os.listdir(self.root)
                if os.path.isfile(os.path.join(self.root, f))]

    def read(self, name: str) -> bytes:
        with open(os.path.join(self.root, name), "rb") as f:
            return f.read()

    def write(self, name: str, data: bytes) -> None:
        with open(os.path.join(self.root, name), "wb") as f:
            f.write(data)

    def delete(self, name: str) -> bool:
        path = os.path.join(self.root, name)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False


class _DataikuStore:
    """Storage backed by a Dataiku managed folder (any backend: local/S3/GCS…)."""

    def __init__(self, folder_id: str, project_key: Optional[str] = None) -> None:
        import dataiku  # imported lazily so non-Dataiku envs still load this module
        self.folder = (dataiku.Folder(folder_id, project_key=project_key)
                       if project_key else dataiku.Folder(folder_id))

    @staticmethod
    def _p(name: str) -> str:
        return "/" + name.lstrip("/")

    def list_files(self) -> List[str]:
        out = []
        for p in self.folder.list_paths_in_partition():
            rel = p.lstrip("/")
            if "/" not in rel:          # root-level files only
                out.append(rel)
        return out

    def read(self, name: str) -> bytes:
        with self.folder.get_download_stream(self._p(name)) as stream:
            return stream.read()

    def write(self, name: str, data: bytes) -> None:
        self.folder.upload_stream(self._p(name), io.BytesIO(data))

    def delete(self, name: str) -> bool:
        try:
            self.folder.delete_path(self._p(name))
            return True
        except Exception:  # noqa: BLE001
            return False


# --------------------------------------------------------------------------- #
# Reference library
# --------------------------------------------------------------------------- #
class ReferenceLibrary:
    def __init__(self, store) -> None:
        # Accept a store object, or a path string for backward compatibility.
        self._store = _LocalStore(store) if isinstance(store, str) else store

    @classmethod
    def from_config(cls) -> "ReferenceLibrary":
        folder_id = os.environ.get("FINRECON_DATAIKU_FOLDER")
        if folder_id:
            return cls(_DataikuStore(folder_id, os.environ.get("FINRECON_DATAIKU_PROJECT")))
        return cls(_LocalStore(os.environ.get("FINRECON_LIBRARY", "./reference_library")))

    # -- references ---------------------------------------------------------
    def list(self) -> List[Dict]:
        out = []
        for fn in sorted(self._store.list_files()):
            if not fn.endswith(".json") or fn.startswith("_"):
                continue
            try:
                obj = json.loads(self._store.read(fn).decode("utf-8"))
                out.append({"id": fn[:-5], "source": obj.get("source", fn[:-5]),
                            "tables": len(obj.get("tables", []))})
            except Exception:  # noqa: BLE001
                continue
        return out

    def get(self, ref_id: str) -> Dict:
        return json.loads(self._store.read(f"{_slug(ref_id)}.json").decode("utf-8"))

    def save(self, ref_id: str, processed: Dict) -> str:
        fn = f"{_slug(ref_id)}.json"
        self._store.write(fn, json.dumps(processed, ensure_ascii=False,
                                         indent=2).encode("utf-8"))
        return fn[:-5]

    def delete(self, ref_id: str) -> bool:
        return self._store.delete(f"{_slug(ref_id)}.json")

    # -- global synonyms ----------------------------------------------------
    def read_synonyms(self) -> Tuple[Optional[str], Optional[bytes]]:
        for fn in self._store.list_files():
            if fn.startswith("_synonyms."):
                return fn, self._store.read(fn)
        return None, None

    def write_synonyms(self, ext: str, data: bytes) -> None:
        for fn in list(self._store.list_files()):
            if fn.startswith("_synonyms."):
                self._store.delete(fn)
        self._store.write(f"_synonyms.{ext}", data)


def admin_ok(token: Optional[str]) -> bool:
    """Retained for backward compatibility; auth now lives in auth.py."""
    expected = os.environ.get("FINRECON_ADMIN_TOKEN")
    return bool(expected) and token == expected
