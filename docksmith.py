#!/usr/bin/env python3
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.build import BuildEngine
from engine.image import ImageStore
from engine.runtime import run_container

DOCKSMITH_DIR = os.path.join(os.path.expanduser("~"), ".docksmith")


def cmd_build(args):
    engine = BuildEngine(DOCKSMITH_DIR, no_cache=args.no_cache)
    engine.build(args.context, args.t)


def cmd_images(args):
    store = ImageStore(DOCKSMITH_DIR)
    images = store.list_images()
    if not images:
        print("No images found.")
        return
    fmt = "{:<20} {:<15} {:<15} {:<30}"
    print(fmt.format("NAME", "TAG", "ID", "CREATED"))
    print("-" * 80)
    for img in images:
        digest = img.get("digest", "")
        short_id = digest[7:19] if digest.startswith("sha256:") else digest[:12]
        print(fmt.format(img["name"], img["tag"], short_id, img.get("created", "")))


def cmd_rmi(args):
    store = ImageStore(DOCKSMITH_DIR)
    store.remove_image(args.name_tag)


def cmd_run(args):
    env_overrides = {}
    for e in args.e or []:
        if "=" in e:
            k, v = e.split("=", 1)
            env_overrides[k] = v
        else:
            print(f"Error: invalid -e format '{e}', expected KEY=VALUE")
            sys.exit(1)
    run_container(DOCKSMITH_DIR, args.name_tag, args.cmd or [], env_overrides)


def main():
    parser = argparse.ArgumentParser(prog="docksmith")
    subparsers = parser.add_subparsers(dest="command")

    # build
    p_build = subparsers.add_parser("build")
    p_build.add_argument("-t", required=True, metavar="name:tag")
    p_build.add_argument("context")
    p_build.add_argument("--no-cache", action="store_true")

    # images
    subparsers.add_parser("images")

    # rmi
    p_rmi = subparsers.add_parser("rmi")
    p_rmi.add_argument("name_tag")

    # run
    p_run = subparsers.add_parser("run")
    p_run.add_argument("-e", action="append", metavar="KEY=VALUE")
    p_run.add_argument("name_tag")
    p_run.add_argument("cmd", nargs="*")

    args = parser.parse_args()

    if args.command == "build":
        cmd_build(args)
    elif args.command == "images":
        cmd_images(args)
    elif args.command == "rmi":
        cmd_rmi(args)
    elif args.command == "run":
        cmd_run(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

