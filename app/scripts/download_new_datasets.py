"""Download additional Kaggle datasets into data/kaggle/.

New datasets pulled:
  open-powerlifting/powerlifting-database -> data/kaggle/powerlifting/

Uses kagglehub (reads KAGGLE_API_TOKEN from env). Loads .env automatically.
Idempotent: skips if destination already has files.

Run:
    python -m app.scripts.download_new_datasets
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_DATASETS = [
    ("open-powerlifting/powerlifting-database", "powerlifting"),
]


def _load_env() -> None:
    env_file = Path(".env")
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


def main() -> None:
    _load_env()

    token = os.environ.get("KAGGLE_API_TOKEN")
    if not token:
        raise SystemExit("KAGGLE_API_TOKEN not set. Check .env or export it.")

    import kagglehub

    dest_base = Path("data/kaggle")
    dest_base.mkdir(parents=True, exist_ok=True)

    for dataset_slug, local_name in _DATASETS:
        dest = dest_base / local_name
        if dest.exists() and any(dest.rglob("*.csv")):
            print(f"  {local_name}: already present at data/kaggle/{local_name}/, skipping.")
            continue

        print(f"Downloading {dataset_slug} ...")
        cache_path = Path(kagglehub.dataset_download(dataset_slug))
        dest.mkdir(parents=True, exist_ok=True)

        copied = 0
        for src_file in cache_path.rglob("*"):
            if not src_file.is_file():
                continue
            rel = src_file.relative_to(cache_path)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, target)
            copied += 1

        print(f"  -> data/kaggle/{local_name}/ ({copied} files copied)")

    print("Done.")


if __name__ == "__main__":
    main()
