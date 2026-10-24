import json
import sys


VALID_INSTRUCTIONS = {"FROM", "COPY", "RUN", "WORKDIR", "ENV", "CMD"}


def parse_docksmithfile(filepath):
    """
    Parse a Docksmithfile and return a list of (instruction, args, line_number).
    Fails with a clear error on unrecognised instructions.
    """
    instructions = []
    with open(filepath, "r") as f:
        lines = f.readlines()

    for lineno, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split(None, 1)
        instruction = parts[0].upper()
        rest = parts[1] if len(parts) > 1 else ""

        if instruction not in VALID_INSTRUCTIONS:
            print(f"Error: unrecognised instruction '{instruction}' at line {lineno}")
            sys.exit(1)

        if instruction == "CMD":
            try:
                cmd_args = json.loads(rest)
            except json.JSONDecodeError:
                print(f"Error: CMD must be JSON array at line {lineno}, got: {rest}")
                sys.exit(1)
            instructions.append((instruction, cmd_args, lineno))
        elif instruction == "ENV":
            if "=" not in rest:
                print(f"Error: ENV must be KEY=VALUE at line {lineno}")
                sys.exit(1)
            k, v = rest.split("=", 1)
            instructions.append((instruction, (k.strip(), v.strip()), lineno))
        elif instruction == "FROM":
            if ":" not in rest:
                print(f"Error: FROM must be name:tag at line {lineno}")
                sys.exit(1)
            instructions.append((instruction, rest.strip(), lineno))
        elif instruction == "WORKDIR":
            instructions.append((instruction, rest.strip(), lineno))
        elif instruction == "COPY":
            copy_parts = rest.split()
            if len(copy_parts) < 2:
                print(f"Error: COPY requires src and dest at line {lineno}")
                sys.exit(1)
            instructions.append((instruction, copy_parts, lineno))
        elif instruction == "RUN":
            instructions.append((instruction, rest.strip(), lineno))

    return instructions

