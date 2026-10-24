#!/usr/bin/env python3
"""
Import a base Docker image tarball into .docksmith local store.

Usage:
    # First pull and save a minimal image with Docker (one time only):
    docker pull alpine:3.18
    docker save alpine:3.18 -o alpine_3.18.tar

    # Then import it into docksmith:
    python setup_base_image.py alpine_3.18.tar alpine 3.18
"""
import sys
import os
import tarfile
import json
import hashlib
import shutil
import tempfile

DOCKSMITH_DIR = os.path.join(os.path.expanduser("~"), ".docksmith")
IMAGES_DIR = os.path.join(DOCKSMITH_DIR, "images")
LAYERS_DIR = os.path.join(DOCKSMITH_DIR, "layers")

os.makedirs(IMAGES_DIR, exist_ok=True)
os.makedirs(LAYERS_DIR, exist_ok=True)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def import_docker_tar(tar_path, name, tag):
    tmp = tempfile.mkdtemp()
    try:
        with tarfile.open(tar_path, "r") as outer:
            outer.extractall(tmp)

        manifest_path = os.path.join(tmp, "manifest.json")
        with open(manifest_path) as f:
            docker_manifest = json.load(f)

        entry = docker_manifest[0]
        layer_tars = entry["Layers"]

        # Try to get config for Env/Cmd/WorkingDir
        config_file = entry.get("Config", "")
        config_data = {}
        if config_file:
            cfg_path = os.path.join(tmp, config_file)
            if os.path.exists(cfg_path):
                with open(cfg_path) as f:
                    docker_config = json.load(f)
                container_cfg = docker_config.get("config", docker_config.get("Config", {}))
                config_data = {
                    "Env": container_cfg.get("Env", []),
                    "Cmd": container_cfg.get("Cmd", []),
                    "WorkingDir": container_cfg.get("WorkingDir", ""),
                }

        layers = []
        for layer_tar_rel in layer_tars:
            layer_tar_path = os.path.join(tmp, layer_tar_rel)

            # Repack layer tar with sorted, zeroed-timestamp entries for reproducibility
            import io
            buf = io.BytesIO()
            with tarfile.open(layer_tar_path, "r:*") as src_tar, \
                 tarfile.open(fileobj=buf, mode="w") as dst_tar:
                members = sorted(src_tar.getmembers(), key=lambda m: m.name)
                for member in members:
                    member.mtime = 0
                    member.uid = 0
                    member.gid = 0
                    member.uname = ""
                    member.gname = ""
                    if member.isfile():
                        f = src_tar.extractfile(member)
                        dst_tar.addfile(member, f)
                    else:
                        dst_tar.addfile(member)

            tar_bytes = buf.getvalue()
            digest = "sha256:" + sha256_bytes(tar_bytes)
            dest_path = os.path.join(LAYERS_DIR, digest.replace("sha256:", "") + ".tar")
            with open(dest_path, "wb") as f:
                f.write(tar_bytes)

            layers.append({
                "digest": digest,
                "size": len(tar_bytes),
                "createdBy": f"{name} layer"
            })
            print(f"  Imported layer {digest[:19]}")

        manifest = {
            "name": name,
            "tag": tag,
            "digest": "",
            "created": "2024-01-01T00:00:00+00:00",
            "config": config_data,
            "layers": layers,
        }

        # Compute manifest digest
        tmp_manifest = dict(manifest)
        tmp_manifest["digest"] = ""
        canonical = json.dumps(tmp_manifest, sort_keys=True, separators=(",", ":"))
        digest = "sha256:" + sha256_bytes(canonical.encode())
        manifest["digest"] = digest

        out_path = os.path.join(IMAGES_DIR, f"{name}_{tag}.json")
        with open(out_path, "w") as f:
            json.dump(manifest, f, indent=2)

        print(f"\nImported {name}:{tag}  →  {digest[7:19]}")

    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python setup_base_image.py <docker_tar> <name> <tag>")
        sys.exit(1)
    import_docker_tar(sys.argv[1], sys.argv[2], sys.argv[3])

