"""Vertex AI CustomJob entry point: train and write the artifact.

Vertex mounts Cloud Storage at /gcs/<bucket>/..., so a gs:// output is written through it.
"""

import argparse
from pathlib import Path

from reclaim_api.classifier import model


def gcs_path(uri: str) -> Path:
    return Path("/gcs/" + uri.removeprefix("gs://")) if uri.startswith("gs://") else Path(uri)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    pipe, report = model.train()
    out = model.save(pipe, report, gcs_path(args.output))
    print(f"trained {report.model_version} → {out}")


if __name__ == "__main__":
    main()
