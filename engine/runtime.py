import os
import sys
import shutil
import tempfile
import ctypes
import ctypes.util

from engine.image import ImageStore
from engine.layer import extract_layers

# Linux clone flags
CLONE_NEWNS   = 0x00020000
CLONE_NEWPID  = 0x20000000
CLONE_NEWUTS  = 0x04000000
CLONE_NEWIPC  = 0x08000000

libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


def _pivot_root(new_root, put_old):
    ret = libc.syscall(155, new_root.encode(), put_old.encode())
    if ret != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno))


def isolate_and_run(rootfs, cmd, env_vars, workdir="/"):
    """
    Fork a child process, enter a new mount namespace via unshare,
    chroot into rootfs, set env and workdir, exec cmd.
    Returns exit code.
    """
    pid = os.fork()
    if pid == 0:
        # Child process
        try:
            # Unshare mount namespace so mounts don't leak to host
            ret = libc.unshare(CLONE_NEWNS)
            if ret != 0:
                errno = ctypes.get_errno()
                os.write(2, f"unshare failed: {os.strerror(errno)}\n".encode())
                os._exit(1)

            # chroot into the assembled filesystem
            os.chroot(rootfs)
            os.chdir(workdir if workdir else "/")

            # Build environment
            full_env = dict(os.environ)
            full_env.update(env_vars)

            # Exec the command
            os.execvpe(cmd[0], cmd, full_env)
        except Exception as e:
            os.write(2, f"isolate_and_run child error: {e}\n".encode())
            os._exit(127)
    else:
        # Parent waits
        _, status = os.waitpid(pid, 0)
        exit_code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1
        return exit_code


def run_container(docksmith_dir, name_tag, cmd_override, env_overrides):
    store = ImageStore(docksmith_dir)
    manifest = store.load_image(name_tag)

    layer_digests = [l["digest"] for l in manifest.get("layers", [])]
    config = manifest.get("config", {})
    image_env = {}
    for e in config.get("Env", []):
        if "=" in e:
            k, v = e.split("=", 1)
            image_env[k] = v
    workdir = config.get("WorkingDir", "/") or "/"

    # Merge env: image env first, then overrides
    final_env = {**image_env, **env_overrides}

    # Determine command
    cmd = cmd_override if cmd_override else config.get("Cmd", [])
    if not cmd:
        print("Error: no CMD defined in image and no command provided")
        sys.exit(1)

    # Assemble filesystem in a temp dir
    tmp_dir = tempfile.mkdtemp(prefix="docksmith_run_")
    try:
        extract_layers(layer_digests, store.layers_dir, tmp_dir)
        # Ensure workdir exists inside rootfs
        wdir_in_root = os.path.join(tmp_dir, workdir.lstrip("/"))
        os.makedirs(wdir_in_root, exist_ok=True)

        exit_code = isolate_and_run(tmp_dir, cmd, final_env, workdir)
        print(f"\nContainer exited with code {exit_code}")
        return exit_code
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

