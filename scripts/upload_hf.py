#!/usr/bin/env python3
"""Upload generated data to a HuggingFace dataset repo."""

from __future__ import annotations

import os
from pathlib import Path

from huggingface_hub import HfApi


def main() -> None:
    repo_id = os.environ.get("HF_REPO_ID", "loffenauer/fuel-prices-germany")
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise SystemExit("HF_TOKEN is required for upload.")

    root = Path(__file__).resolve().parents[1]
    api = HfApi()
    api.create_repo(repo_id=repo_id, repo_type="dataset", exist_ok=True, token=token, private=False)

    for folder in (root / "data2"):
        if folder.exists():
            api.upload_folder(
                repo_id=repo_id,
                repo_type="dataset",
                folder_path=str(folder),
                path_in_repo=folder.name,
                token=token,
            )


if __name__ == "__main__":
    main()
