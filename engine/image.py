import os
import json
import hashlib
import sys


class ImageStore:
    def __init__(self, docksmith_dir):
        self.docksmith_dir = docksmith_dir
        self.images_dir = os.path.join(docksmith_dir, "images")
        self.layers_dir = os.path.join(docksmith_dir, "layers")
        os.makedirs(self.images_dir, exist_ok=True)
        os.makedirs(self.layers_dir, exist_ok=True)

    def _manifest_path(self, name, tag):
        return os.path.join(self.images_dir, f"{name}_{tag}.json")

    def load_image(self, name_tag):
        if ":" not in name_tag:
            print(f"Error: image must be name:tag, got '{name_tag}'")
            sys.exit(1)
        name, tag = name_tag.split(":", 1)
        path = self._manifest_path(name, tag)
        if not os.path.exists(path):
            print(f"Error: image '{name_tag}' not found in local store")
            sys.exit(1)
        with open(path, "r") as f:
            return json.load(f)

    def save_image(self, manifest):
        name = manifest["name"]
        tag = manifest["tag"]
        # compute digest: serialize with digest="" then sha256
        tmp = dict(manifest)
        tmp["digest"] = ""
        canonical = json.dumps(tmp, sort_keys=True, separators=(",", ":"))
        digest = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
        manifest["digest"] = digest
        path = self._manifest_path(name, tag)
        with open(path, "w") as f:
            json.dump(manifest, f, indent=2)
        return digest

    def list_images(self):
        images = []
        for fname in os.listdir(self.images_dir):
            if fname.endswith(".json"):
                with open(os.path.join(self.images_dir, fname), "r") as f:
                    images.append(json.load(f))
        return images

    def remove_image(self, name_tag):
        if ":" not in name_tag:
            print(f"Error: image must be name:tag, got '{name_tag}'")
            sys.exit(1)
        name, tag = name_tag.split(":", 1)
        path = self._manifest_path(name, tag)
        if not os.path.exists(path):
            print(f"Error: image '{name_tag}' not found")
            sys.exit(1)
        with open(path, "r") as f:
            manifest = json.load(f)
        os.remove(path)
        removed_layers = []
        for layer in manifest.get("layers", []):
            digest = layer["digest"]
            layer_path = os.path.join(self.layers_dir, digest.replace("sha256:", "") + ".tar")
            if os.path.exists(layer_path):
                os.remove(layer_path)
                removed_layers.append(digest[:19])
        print(f"Removed image {name_tag}")
        if removed_layers:
            print(f"Removed layers: {', '.join(removed_layers)}")

    def layer_path(self, digest):
        return os.path.join(self.layers_dir, digest.replace("sha256:", "") + ".tar")

