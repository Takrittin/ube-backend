"""Download public official weights once; run before starting automatic checks."""
import json
from pathlib import Path


def main():
    from huggingface_hub import HfApi, snapshot_download
    from app.config import SAM2_MODEL_ID, SAM2_MODEL_DIR
    revision = HfApi().model_info(SAM2_MODEL_ID).sha
    snapshot_download(SAM2_MODEL_ID, revision=revision, local_dir=SAM2_MODEL_DIR,
                      allow_patterns=["*.json", "*.safetensors"])
    Path(SAM2_MODEL_DIR, "ube-model-source.json").write_text(json.dumps({"repo_id": SAM2_MODEL_ID, "revision": revision}))
    print("Official SAM 2.1 weights prepared. Revision:", revision)


if __name__ == "__main__":
    main()
