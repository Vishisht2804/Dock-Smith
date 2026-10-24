# This file is copied into the container image via the COPY instruction.
# It demonstrates that COPY works correctly during the build.
# The container runs using /bin/sh (alpine has no python3 by default).

import os

app_name = os.environ.get("APP_NAME", "Unknown")
print(f"[{app_name}] DOCKSMITH sample app is running!")
print(f"Working directory: {os.getcwd()}")
print(f"Files in /app: {os.listdir('.')}")
print("Isolation test: writing /tmp/testfile inside container...")
with open("/tmp/testfile", "w") as f:
    f.write("this should NOT appear on the host\n")
print("Done. Check that /tmp/testfile does NOT exist on your host.")

