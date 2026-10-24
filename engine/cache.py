import os
import json
import hashlib
import glob


CACHE_INDEX_FILE = "cache_index.json"


class CacheIndex:
    def __init__(self, docksmith_dir):
        self.path = os.path.join(docksmith_dir, CACHE_INDEX_FILE)
        self._data = {}
        if os.path.exists(self.path):
            with open(self.path, "r") as f:
                self._data = json.load(f)

    def get(self, key):
        return self._data.get(key)

    def put(self, key, digest):
        self._data[key] = digest
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2)


def compute_cache_key(prev_digest, instruction_text, workdir, env_state, context_dir=None, src_patterns=None):
    """
    Compute a deterministic cache key for a COPY or RUN instruction.
    """
    parts = [
        prev_digest or "",
        instruction_text,
        workdir or "",
        _serialize_env(env_state),
    ]

    if src_patterns is not None and context_dir is not None:
        file_hashes = _hash_sources(context_dir, src_patterns)
        parts.append(file_hashes)

    combined = "\n".join(parts)
    return "sha256:" + hashlib.sha256(combined.encode()).hexdigest()


def _serialize_env(env_state):
    if not env_state:
        return ""
    return "\n".join(f"{k}={v}" for k, v in sorted(env_state.items()))


def _hash_sources(context_dir, patterns):
    import hashlib
    matched = []
    for pat in patterns:
        full_pattern = os.path.join(context_dir, pat)
        for m in sorted(glob.glob(full_pattern, recursive=True)):
            if os.path.isfile(m):
                matched.append(m)
    matched = sorted(set(matched))
    result = []
    for fpath in matched:
        h = hashlib.sha256()
        with open(fpath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        result.append(h.hexdigest())
    return "\n".join(result)

