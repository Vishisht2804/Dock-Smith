import os
import tarfile
import hashlib
import glob
import shutil


def _sorted_files(path):
    """Walk a directory and return sorted list of (arcname, filepath)."""
    result = []
    if os.path.isfile(path):
        result.append((os.path.basename(path), path))
    else:
        for root, dirs, files in os.walk(path):
            dirs.sort()
            for fname in sorted(files):
                full = os.path.join(root, fname)
                arcname = os.path.relpath(full, os.path.dirname(path))
                result.append((arcname, full))
    return sorted(result, key=lambda x: x[0])


def create_copy_layer(src_patterns, dest, context_dir, tmp_fs_root):
    """
    Create a tar layer for a COPY instruction.
    Returns (tar_bytes, digest_string).
    """
    import io
    buf = io.BytesIO()

    # Resolve glob patterns relative to context_dir
    matched_files = []
    for pattern in src_patterns[:-1]:
        full_pattern = os.path.join(context_dir, pattern)
        matches = sorted(glob.glob(full_pattern, recursive=True))
        for m in matches:
            matched_files.append(m)

    dest_path = dest.lstrip("/")

    with tarfile.open(fileobj=buf, mode="w") as tar:
        for src_file in sorted(matched_files):
            rel = os.path.relpath(src_file, context_dir)
            if os.path.isdir(src_file):
                for root, dirs, files in os.walk(src_file):
                    dirs.sort()
                    for fname in sorted(files):
                        full = os.path.join(root, fname)
                        arc = os.path.join(dest_path, os.path.relpath(full, context_dir))
                        _add_to_tar(tar, full, arc)
            else:
                arc = os.path.join(dest_path, os.path.basename(src_file))
                _add_to_tar(tar, src_file, arc)

    tar_bytes = buf.getvalue()
    digest = "sha256:" + hashlib.sha256(tar_bytes).hexdigest()
    return tar_bytes, digest


def _add_to_tar(tar, filepath, arcname):
    """Add a file to a tar with zeroed timestamps for reproducibility."""
    info = tar.gettarinfo(filepath, arcname=arcname)
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    with open(filepath, "rb") as f:
        tar.addfile(info, f)


def create_run_layer(tmp_fs_root, changed_files_root, original_root_snapshot):
    """
    After RUN executes, diff the filesystem to find added/modified files.
    Returns (tar_bytes, digest_string).
    """
    import io
    buf = io.BytesIO()
    new_files = []

    for root, dirs, files in os.walk(changed_files_root):
        dirs.sort()
        for fname in sorted(files):
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, changed_files_root)
            orig = os.path.join(original_root_snapshot, rel)
            if not os.path.exists(orig):
                new_files.append((rel, full))
            else:
                with open(full, "rb") as f1, open(orig, "rb") as f2:
                    if f1.read() != f2.read():
                        new_files.append((rel, full))

    new_files.sort(key=lambda x: x[0])

    with tarfile.open(fileobj=buf, mode="w") as tar:
        for arcname, full in new_files:
            _add_to_tar(tar, full, arcname)

    tar_bytes = buf.getvalue()
    digest = "sha256:" + hashlib.sha256(tar_bytes).hexdigest()
    return tar_bytes, digest


def extract_layers(layer_digests, layers_dir, dest_dir):
    """Extract a list of layer tars (in order) into dest_dir."""
    for digest in layer_digests:
        tar_path = os.path.join(layers_dir, digest.replace("sha256:", "") + ".tar")
        if not os.path.exists(tar_path):
            raise FileNotFoundError(f"Layer file missing for digest {digest}")
        with tarfile.open(tar_path, "r") as tar:
            tar.extractall(path=dest_dir)


def hash_file(filepath):
    """Return SHA-256 hex digest of a file's content."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

