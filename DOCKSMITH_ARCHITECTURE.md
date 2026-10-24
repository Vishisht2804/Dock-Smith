# Docksmith Architecture, Usage, and Limitations

## 1. Overview

Docksmith is a small, educational container build and execution system implemented
in Python. It demonstrates the core mechanics behind a container workflow without
depending on a daemon or a registry:

1. Read a `Docksmithfile`.
2. Resolve a local base image.
3. Build filesystem layers for `COPY` and `RUN`.
4. Store image metadata and layers in a user-local directory.
5. Reassemble the layers into a temporary root filesystem.
6. Run a command inside a Linux mount namespace and `chroot`.

Docksmith is best understood as a learning-oriented container prototype, not as a
production replacement for Docker, Podman, containerd, or another OCI runtime.

## 2. Goals and scope

### Supported goals

- Build a small image from a local base image.
- Apply environment, working-directory, file-copy, command, and default-command
  configuration.
- Store image manifests and content-addressed tar layers locally.
- Reuse cached `COPY` and `RUN` results when the cache key matches.
- Run a built image with optional command and environment overrides.
- Demonstrate filesystem isolation from the host using Linux primitives.

### Deliberately out of scope

- Image pulling, pushing, or registry authentication.
- OCI image/layout compatibility as a complete standard.
- Multi-stage builds, build arguments, volumes, ports, networking, or capabilities.
- A long-running daemon, API server, orchestration layer, or service supervisor.
- Full Linux container security hardening.

## 3. High-level architecture

```text
                         +----------------------+
                         |      User / CLI       |
                         |   docksmith.py       |
                         +----------+-----------+
                                    |
                 +------------------+------------------+
                 |                                     |
                 v                                     v
        +------------------+                  +------------------+
        |   Build path     |                  |    Run path      |
        |  engine/build.py |                  | engine/runtime.py|
        +--------+---------+                  +--------+---------+
                 |                                     |
       +---------+----------+                 +---------+----------+
       |                    |                 |                    |
       v                    v                 v                    v
  Docksmithfile        CacheIndex       ImageStore         Layer extractor
  parser               cache_index.json  manifests          tar layers
       |                    |                 |                    |
       +----------+---------+-----------------+--------------------+
                  |
                  v
       ~/.docksmith/images/
       ~/.docksmith/layers/
       ~/.docksmith/cache_index.json
                  |
                  v
       Linux mount namespace + chroot
                  |
                  v
             Container process
```

The CLI is intentionally thin. It delegates image construction to `BuildEngine`,
metadata and layer persistence to `ImageStore`, and execution to
`run_container`.

## 4. Low-level component architecture

### 4.1 CLI entry point: `docksmith.py`

The command-line entry point:

- Defines the `build`, `images`, `rmi`, and `run` subcommands.
- Uses `~/.docksmith` as the default state directory.
- Validates image references in `name:tag` form.
- Parses repeated `-e KEY=VALUE` runtime environment overrides.
- Prints build, image-listing, removal, and runtime status messages.

The CLI does not maintain a daemon or a separate process manager. Each operation
opens the local store, performs the operation, and exits.

### 4.2 Docksmithfile parser: `engine/parser.py`

The parser reads a file named `Docksmithfile` from the build context. Blank lines
and lines beginning with `#` are ignored. Supported instructions are:

| Instruction | Parsed representation | Purpose |
|---|---|---|
| `FROM name:tag` | Base image reference | Selects an image already present in the local store |
| `COPY src... dest` | Source patterns plus destination | Adds context files to a new layer |
| `RUN command` | Shell command string | Executes `/bin/sh -c command` and records file changes |
| `WORKDIR path` | Path string | Sets the working directory for build and runtime |
| `ENV KEY=VALUE` | Key/value tuple | Adds or updates image environment state |
| `CMD ["arg", "..."]` | JSON list | Sets the default executable and arguments |

`CMD` must be a JSON array. `FROM` must contain a colon, and `ENV` must contain
an equals sign. Invalid syntax exits with an error message.

The parser is line-oriented and does not implement Dockerfile continuation lines,
quoted instruction parsing, variable expansion, or instruction-specific escape
rules.

### 4.3 Build engine: `engine/build.py`

`BuildEngine.build()` performs the following sequence:

1. Validate the target image reference.
2. Locate and parse `<context>/Docksmithfile`.
3. Load the local `FROM` manifest.
4. Copy the base image's layer list and configuration into the new image state.
5. Process instructions in order.
6. Save a new image manifest.

Build state includes:

- `layers`: ordered layer descriptors.
- `env_state`: accumulated environment values.
- `workdir`: current working directory.
- `cmd`: current default command.
- `prev_digest`: digest used as the parent input to the next cache key.

#### `COPY`

`COPY` resolves source patterns relative to the context directory using Python
glob matching. It creates a deterministic tar archive:

- Files are sorted.
- Tar timestamps, user IDs, group IDs, and names are normalized.
- Source symlinks and non-regular files are skipped.
- The archive is hashed with SHA-256 and stored as a layer.

#### `RUN`

For `RUN`, the builder:

1. Extracts all current layers into a temporary filesystem.
2. Takes a regular-file snapshot of that filesystem.
3. Executes `/bin/sh -c "<command>"` inside the temporary root.
4. Compares the resulting regular files with the snapshot.
5. Archives added or modified regular files as a new layer.

The command receives the build-time environment accumulated from `FROM` and
`ENV`, and runs from the current `WORKDIR`.

#### Image manifest

The manifest contains:

```json
{
  "name": "example",
  "tag": "latest",
  "digest": "sha256:<manifest-digest>",
  "created": "<ISO-8601 timestamp>",
  "config": {
    "Env": ["KEY=value"],
    "Cmd": ["/bin/sh", "-c", "echo hello"],
    "WorkingDir": "/app"
  },
  "layers": [
    {
      "digest": "sha256:<layer-digest>",
      "size": 1234,
      "createdBy": "COPY main.py /app"
    }
  ]
}
```

The manifest digest is computed from a canonical JSON representation with the
digest field temporarily blanked.

### 4.4 Layer handling: `engine/layer.py`

The layer module provides helpers for creating and extracting tar layers. Layers
are applied in order, so later files overwrite earlier files when tar extraction
encounters the same path.

The build engine currently contains its own `COPY` and `RUN` layer creation logic;
the helper functions in `engine/layer.py` provide related, reusable behavior.

### 4.5 Image store: `engine/image.py`

`ImageStore` creates and manages:

```text
~/.docksmith/
├── images/
│   └── <name>_<tag>.json
├── layers/
│   └── <sha256-without-prefix>.tar
└── cache_index.json
```

Images are addressed by `name:tag`. Layer files are addressed by SHA-256 content
digest. The store has no remote registry and no database; JSON files and tar files
are the persistence format.

### 4.6 Cache: `engine/cache.py`

The cache is a JSON mapping from a computed cache key to a layer digest.

For `COPY`, the key includes:

- Previous layer or base-image digest.
- Instruction text.
- Working directory.
- Sorted environment state.
- SHA-256 hashes of matched source files.

For `RUN`, the key includes:

- Previous layer or base-image digest.
- Instruction text.
- Working directory.
- Sorted environment state.

Once a cache miss occurs, later build steps are forced to miss as well. The
`--no-cache` option disables both cache lookup and cache writes for the build.

### 4.7 Base-image import: `setup_base_image.py`

Docksmith does not pull base images itself. The import utility accepts a Docker
image tar archive, reads its `manifest.json` and configuration, and imports:

- Base image environment variables.
- Default command.
- Working directory.
- Each image layer.

Imported layers are repacked with normalized metadata before their digests are
calculated. This creates a Docksmith-local representation of a Docker-exported
base image.

### 4.8 Runtime: `engine/runtime.py`

The runtime:

1. Loads the requested image manifest.
2. Extracts all image layers into a temporary directory.
3. Ensures the configured working directory exists.
4. Forks a child process.
5. In the child, creates a new mount namespace with `unshare`.
6. Calls `chroot` into the assembled temporary root filesystem.
7. Changes to the configured working directory.
8. Merges image environment variables with command-line overrides.
9. Executes the command using `os.execvpe`.
10. Waits in the parent and reports the exit code.

The runtime defines additional Linux namespace constants, but the current
implementation uses only `CLONE_NEWNS`. PID, UTS, and IPC namespace isolation
are not currently enabled.

## 5. End-to-end build flow

```text
Build context
    |
    v
Read Docksmithfile
    |
    v
Load FROM manifest from ~/.docksmith/images
    |
    v
For each instruction:
    - WORKDIR / ENV / CMD update image configuration
    - COPY creates or reuses a tar layer
    - RUN executes in assembled rootfs and creates or reuses a diff layer
    |
    v
Create manifest with ordered layers and final config
    |
    v
Write ~/.docksmith/images/name_tag.json
```

## 6. End-to-end run flow

```text
name:tag
   |
   v
Load image manifest
   |
   v
Extract ordered layer tar files into a temporary directory
   |
   v
Resolve command and environment
   |
   v
fork()
   |
   +--> child: unshare mount namespace -> chroot -> chdir -> exec
   |
   +--> parent: waitpid() -> return child exit code
   |
   v
Delete temporary root filesystem
```

The temporary root filesystem is removed after the process exits. Changes made
at runtime are therefore not persisted into the image store.

## 7. Installation and prerequisites

### Required

- Python 3.
- A Linux environment with `fork`, `unshare`, mount namespaces, and `chroot`.
- Permission to create mount namespaces and use `chroot`; this commonly means
  running with root privileges or suitable Linux capabilities.
- A base image imported into the local Docksmith store.

### Windows note

The repository may be edited on Windows, but the implemented runtime is Linux
specific. Native Windows execution is not supported because the code uses
`os.fork`, Linux `libc.unshare`, Linux syscall behavior, and `chroot`. Use a
Linux host or a Linux VM/WSL environment with the required namespace and
privilege support. WSL/container restrictions may still prevent namespace
operations.

## 8. Usage

All commands below are run from the Docksmith project directory or with paths
adjusted to the installation location.

### 8.1 Import a base image

The import step is normally done once per base image:

```bash
docker pull alpine:3.18
docker save alpine:3.18 -o alpine_3.18.tar
python setup_base_image.py alpine_3.18.tar alpine 3.18
```

The resulting local reference is `alpine:3.18`.

### 8.2 Build the sample application

The sample context contains `sampleapp/Docksmithfile` and `sampleapp/main.py`:

```bash
python docksmith.py build -t sampleapp:latest sampleapp
``` 

Use `--no-cache` to force layer rebuilding:

```bash
python docksmith.py build --no-cache -t sampleapp:latest sampleapp
```

### 8.3 List images

```bash
python docksmith.py images
```

This lists image name, tag, short manifest ID, and creation time.

### 8.4 Run an image

Run the image's configured `CMD`:

```bash
python docksmith.py run sampleapp:latest
```

Override image environment variables:

```bash
python docksmith.py run -e APP_NAME=TestRun sampleapp:latest
```

Override the image command. The command is passed as an argument vector, not as
one shell string:

```bash
python docksmith.py run sampleapp:latest /bin/sh -c "echo hello"
```

### 8.5 Remove an image

```bash
python docksmith.py rmi sampleapp:latest
```

This removes the image manifest and the layer files referenced by that manifest.

## 9. Sample Docksmithfile

```dockerfile
FROM alpine:3.18
WORKDIR /app
ENV APP_NAME=DocksmithDemo
COPY main.py /app
RUN echo "Build complete for $APP_NAME"
CMD ["/bin/sh", "-c", "echo Hello from $APP_NAME && echo WorkingDir: $(pwd) && ls /app && echo --- && touch /tmp/testfile_in_container && echo Written testfile inside container"]
```

The sample demonstrates:

- Inheriting an Alpine root filesystem.
- Setting a working directory.
- Persisting an environment variable in image configuration.
- Copying an application file into `/app`.
- Running a build-time shell command.
- Defining a runtime shell command.
- Writing inside the temporary container filesystem without persisting the file
  to the host.

## 10. Security and isolation considerations

Docksmith provides useful process/filesystem isolation for demonstration, but
the isolation should not be treated as a security boundary.

- Build and runtime operations may require high privileges.
- `RUN` executes arbitrary shell commands from the build file.
- The child starts with a copy of the host environment and then applies image
  and override values; this can expose host environment variables to commands.
- Only a mount namespace is created. There is no PID, network, UTS, or IPC
  namespace isolation.
- No seccomp profile, Linux capabilities drop, user namespace, resource limit,
  read-only root filesystem, device policy, or cgroup limit is configured.
- The implementation does not provide a dedicated non-root container user.
- The imported tar extraction path should only be used with trusted image archives.
  Archive extraction must be hardened before accepting untrusted input.
- Image and cache files are ordinary files under the user's home directory and
  have no locking or concurrent-access protocol.

Use trusted build files and base archives, and do not use Docksmith as a
production sandbox for hostile workloads.

## 11. Functional limitations

### Image and build limitations

- `FROM` resolves only images already imported into `~/.docksmith`.
- Image references must use the simple `name:tag` form.
- There is no registry, pull, push, login, or remote digest resolution.
- There is no `.docksmithignore`-style context exclusion mechanism.
- `COPY` supports basic glob expansion but not full Dockerfile copy semantics.
- `COPY` skips symlinks, special files, and empty directories.
- Source paths are not comprehensively validated against context escape cases.
- `RUN` requires `/bin/sh` to exist in the base image.
- `RUN` filesystem diffing records only added or modified regular files.
- Deleted files are not represented by whiteouts, so deletions are not faithfully
  carried into later layers.
- File metadata changes, permissions-only changes, ownership changes, hard links,
  device nodes, FIFOs, sockets, and symlink changes are not reliably preserved.
- Files removed during a `RUN` may reappear when the layer stack is reconstructed.
- Build-time network behavior depends on the host network and is not isolated.
- There are no build arguments, secrets, mounts, package-cache mounts, or
  multi-stage builds.

### Runtime limitations

- The runtime requires Linux primitives and suitable privileges.
- Runtime writes are ephemeral and are discarded on exit.
- There are no port mappings, volume mounts, bind mounts, or persistent volumes.
- There is no network configuration or network namespace isolation.
- There is no signal-management or init-process behavior comparable to a
  container supervisor.
- Exit-status handling is basic and does not provide detailed signal metadata.
- There is no resource accounting or enforcement for CPU, memory, disk, or PIDs.
- The runtime does not provide a container name, ID lifecycle, attach, logs, or
  exec API.
- A command must be available inside the image root filesystem; host binaries
  are not automatically visible after `chroot`.

### Store and cache limitations

- Manifests and cache indexes are JSON files with no transactional updates.
- Concurrent builds or runs can race while writing shared state.
- Layer garbage collection and reference counting are not implemented.
- `rmi` removes every layer referenced by the deleted manifest, even if another
  image also references the same layer.
- Cache validity for `RUN` does not include the external world, such as network
  content, package repository state, time, or external files.
- Cache keys do not capture every filesystem or execution side effect.
- A changed instruction causes a cache-miss cascade for subsequent steps.

## 12. Recommended improvements

For a stronger container implementation, prioritize:

1. Replace ad-hoc manifests with an OCI-compatible image and layer layout.
2. Add secure archive extraction with path traversal and link validation.
3. Implement whiteouts and metadata preservation for correct layer semantics.
4. Add user namespaces, PID/network/IPC namespaces, capability dropping,
   seccomp, cgroups, and a non-root default user.
5. Add context boundary validation and an ignore file.
6. Add manifest/layer reference counting and safe garbage collection.
7. Add file locking and atomic writes for manifests and cache indexes.
8. Make cache inputs explicit and provide reproducibility controls.
9. Add automated tests for parser errors, layer reproducibility, cache behavior,
   deletion semantics, and runtime isolation.
10. Add a clear Linux installation path and a supported WSL/VM execution guide.

## 13. Source map

| Area | File |
|---|---|
| CLI | `docksmith.py` |
| Build orchestration | `engine/build.py` |
| Docksmithfile parsing | `engine/parser.py` |
| Image manifests and local store | `engine/image.py` |
| Layer creation/extraction helpers | `engine/layer.py` |
| Build cache | `engine/cache.py` |
| Linux runtime isolation | `engine/runtime.py` |
| Docker tar base-image import | `setup_base_image.py` |
| Demonstration application | `sampleapp/main.py` |
| Demonstration build file | `sampleapp/Docksmithfile` |

