import os
import sys
import json
import time
import shutil
import tempfile
import tarfile
import hashlib
import glob
from datetime import datetime, timezone

from engine.parser import parse_docksmithfile
from engine.image import ImageStore
from engine.cache import CacheIndex, compute_cache_key
from engine.layer import extract_layers, _add_to_tar
from engine.runtime import isolate_and_run


class BuildEngine:
    def __init__(self, docksmith_dir, no_cache=False):
        self.docksmith_dir = docksmith_dir
        self.no_cache = no_cache
        self.store = ImageStore(docksmith_dir)
        self.cache = CacheIndex(docksmith_dir)

    def build(self, context_dir, name_tag):
        if ":" not in name_tag:
            print(f"Error: -t must be name:tag, got '{name_tag}'")
            sys.exit(1)
        name, tag = name_tag.split(":", 1)

        docksmithfile = os.path.join(context_dir, "Docksmithfile")
        if not os.path.exists(docksmithfile):
            print(f"Error: Docksmithfile not found in '{context_dir}'")
            sys.exit(1)

        instructions = parse_docksmithfile(docksmithfile)

        base_manifest = None
        layers = []
        env_state = {}
        workdir = ""
        cmd = []
        prev_digest = None
        cache_miss_cascade = False
        total_start = time.time()
        step = 0
        original_created = None
        all_cache_hits = True

        # Preserve created timestamp if image already exists
        try:
            existing = self.store.load_image(name_tag)
            original_created = existing.get("created")
        except SystemExit:
            pass

        for instruction, args, lineno in instructions:
            step += 1

            if instruction == "FROM":
                print(f"Step {step}/{len(instructions)} : FROM {args}")
                base_manifest = self.store.load_image(args)
                layers = list(base_manifest.get("layers", []))
                base_config = base_manifest.get("config", {})
                for e in base_config.get("Env", []):
                    if "=" in e:
                        k, v = e.split("=", 1)
                        env_state[k] = v
                workdir = base_config.get("WorkingDir", "")
                cmd = base_config.get("Cmd", [])
                prev_digest = base_manifest.get("digest", "")

            elif instruction == "WORKDIR":
                workdir = args
                print(f"Step {step}/{len(instructions)} : WORKDIR {args}")

            elif instruction == "ENV":
                k, v = args
                env_state[k] = v
                print(f"Step {step}/{len(instructions)} : ENV {k}={v}")

            elif instruction == "CMD":
                cmd = args
                print(f"Step {step}/{len(instructions)} : CMD {json.dumps(args)}")

            elif instruction == "COPY":
                src_patterns = args[:-1]
                dest = args[-1]
                instr_text = f"COPY {' '.join(args)}"
                t_start = time.time()

                cache_key = compute_cache_key(
                    prev_digest, instr_text, workdir, env_state,
                    context_dir=context_dir, src_patterns=src_patterns
                )

                cached_digest = None if self.no_cache or cache_miss_cascade else self.cache.get(cache_key)
                layer_path = self.store.layer_path(cached_digest) if cached_digest else None

                if cached_digest and layer_path and os.path.exists(layer_path):
                    elapsed = time.time() - t_start
                    size = os.path.getsize(layer_path)
                    print(f"Step {step}/{len(instructions)} : {instr_text}  CACHE HIT  {elapsed:.2f}s")
                    layers.append({"digest": cached_digest, "size": size, "createdBy": instr_text})
                    prev_digest = cached_digest
                else:
                    all_cache_hits = False
                    cache_miss_cascade = True
                    tar_bytes, digest = self._make_copy_layer(src_patterns, dest, context_dir)
                    lpath = self.store.layer_path(digest)
                    with open(lpath, "wb") as f:
                        f.write(tar_bytes)
                    if not self.no_cache:
                        self.cache.put(cache_key, digest)
                    elapsed = time.time() - t_start
                    print(f"Step {step}/{len(instructions)} : {instr_text}  CACHE MISS  {elapsed:.2f}s")
                    layers.append({"digest": digest, "size": len(tar_bytes), "createdBy": instr_text})
                    prev_digest = digest

            elif instruction == "RUN":
                instr_text = f"RUN {args}"
                t_start = time.time()

                cache_key = compute_cache_key(
                    prev_digest, instr_text, workdir, env_state
                )

                cached_digest = None if self.no_cache or cache_miss_cascade else self.cache.get(cache_key)
                layer_path = self.store.layer_path(cached_digest) if cached_digest else None

                if cached_digest and layer_path and os.path.exists(layer_path):
                    elapsed = time.time() - t_start
                    size = os.path.getsize(layer_path)
                    print(f"Step {step}/{len(instructions)} : {instr_text}  CACHE HIT  {elapsed:.2f}s")
                    layers.append({"digest": cached_digest, "size": size, "createdBy": instr_text})
                    prev_digest = cached_digest
                else:
                    all_cache_hits = False
                    cache_miss_cascade = True
                    digest, size = self._execute_run(args, layers, env_state, workdir)
                    if not self.no_cache:
                        self.cache.put(cache_key, digest)
                    elapsed = time.time() - t_start
                    print(f"Step {step}/{len(instructions)} : {instr_text}  CACHE MISS  {elapsed:.2f}s")
                    layers.append({"digest": digest, "size": size, "createdBy": instr_text})
                    prev_digest = digest

        total_elapsed = time.time() - total_start

        env_list = [f"{k}={v}" for k, v in sorted(env_state.items())]

        if all_cache_hits and original_created:
            created = original_created
        else:
            created = datetime.now(timezone.utc).isoformat()

        manifest = {
            "name": name,
            "tag": tag,
            "digest": "",
            "created": created,
            "config": {
                "Env": env_list,
                "Cmd": cmd,
                "WorkingDir": workdir,
            },
            "layers": layers,
        }

        digest = self.store.save_image(manifest)
        short_id = digest[7:19]
        print(f"\nSuccessfully built {short_id}  {name_tag}  {total_elapsed:.2f}s")

    def _make_copy_layer(self, src_patterns, dest, context_dir):
        import io
        buf = io.BytesIO()
        dest_path = dest.lstrip("/")
        matched = []
        for pattern in src_patterns:
            full_pattern = os.path.join(context_dir, pattern)
            for m in sorted(glob.glob(full_pattern, recursive=True)):
                matched.append(m)
        matched = sorted(set(matched))

        with tarfile.open(fileobj=buf, mode="w") as tar:
            for src in matched:
                if os.path.isdir(src):
                    for root, dirs, files in os.walk(src):
                        dirs.sort()
                        for fname in sorted(files):
                            full = os.path.join(root, fname)
                            if os.path.islink(full) or not os.path.isfile(full):
                                continue
                            arc = os.path.join(dest_path, os.path.relpath(full, context_dir))
                            _add_to_tar(tar, full, arc)
                else:
                    if not os.path.islink(src) and os.path.isfile(src):
                        arc = os.path.join(dest_path, os.path.basename(src))
                        _add_to_tar(tar, src, arc)

        tar_bytes = buf.getvalue()
        digest = "sha256:" + hashlib.sha256(tar_bytes).hexdigest()
        return tar_bytes, digest

    def _execute_run(self, command, current_layers, env_state, workdir):
        """Execute RUN in isolation, capture filesystem delta as a new layer."""
        tmp_before = tempfile.mkdtemp(prefix="docksmith_before_")
        tmp_after = tempfile.mkdtemp(prefix="docksmith_after_")

        try:
            # Assemble current filesystem
            layer_digests = [l["digest"] for l in current_layers]
            extract_layers(layer_digests, self.store.layers_dir, tmp_after)

            # Snapshot before-state for diffing
            _snapshot_dir(tmp_after, tmp_before)

            # Ensure workdir exists inside rootfs
            if workdir:
                wdir_full = os.path.join(tmp_after, workdir.lstrip("/"))
                os.makedirs(wdir_full, exist_ok=True)

            # Run command inside isolated filesystem
            cmd = ["/bin/sh", "-c", command]
            exit_code = isolate_and_run(tmp_after, cmd, env_state, workdir or "/")

            if exit_code != 0:
                print(f"Error: RUN command failed with exit code {exit_code}")
                sys.exit(exit_code)

            # Compute delta layer (only new/changed files)
            import io
            buf = io.BytesIO()
            new_files = []

            for root, dirs, files in os.walk(tmp_after, followlinks=False):
                dirs.sort()
                for fname in sorted(files):
                    full = os.path.join(root, fname)
                    # Skip symlinks and unreadable special files
                    if os.path.islink(full) or not os.path.isfile(full):
                        continue
                    rel = os.path.relpath(full, tmp_after)
                    orig = os.path.join(tmp_before, rel)
                    try:
                        if not os.path.exists(orig):
                            new_files.append((rel, full))
                        else:
                            with open(full, "rb") as f1, open(orig, "rb") as f2:
                                if f1.read() != f2.read():
                                    new_files.append((rel, full))
                    except (OSError, PermissionError):
                        continue

            new_files.sort(key=lambda x: x[0])

            with tarfile.open(fileobj=buf, mode="w") as tar:
                for arcname, full in new_files:
                    _add_to_tar(tar, full, arcname)

            tar_bytes = buf.getvalue()
            digest = "sha256:" + hashlib.sha256(tar_bytes).hexdigest()
            lpath = self.store.layer_path(digest)
            with open(lpath, "wb") as f:
                f.write(tar_bytes)

            return digest, len(tar_bytes)

        finally:
            shutil.rmtree(tmp_before, ignore_errors=True)
            shutil.rmtree(tmp_after, ignore_errors=True)


def _snapshot_dir(src, dst):
    """Copy directory tree src → dst for diffing. Skips symlinks and special files."""
    for root, dirs, files in os.walk(src, followlinks=False):
        dirs.sort()
        rel_root = os.path.relpath(root, src)
        dst_root = os.path.join(dst, rel_root)
        os.makedirs(dst_root, exist_ok=True)
        for fname in files:
            src_file = os.path.join(root, fname)
            dst_file = os.path.join(dst_root, fname)
            # Skip symlinks and anything that isn't a plain file
            if os.path.islink(src_file) or not os.path.isfile(src_file):
                continue
            try:
                shutil.copy2(src_file, dst_file)
            except (OSError, PermissionError):
                continue

