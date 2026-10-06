"""
GameStringer CLI — Engine-independent preflight and post-patch utilities.
"""

import os
import sys

# Ensure UTF-8 output encoding on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Add parent directory to sys.path for direct execution support
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gamestringer.core.logger import setup_logger, logger
from locpipe.preflight.font_check import check_game_fonts
from gamestringer.core.addressables_crc import fix_catalog_crc_command

try:
    import click

    @click.group()
    @click.option("--verbose", "-v", is_flag=True, help="Enable verbose DEBUG logging.")
    @click.option("--quiet", "-q", is_flag=True, help="Enable quiet mode (show ERRORs only).")
    @click.version_option(version="2.0.0", prog_name="gamestringer")
    def main(verbose: bool = False, quiet: bool = False):
        """GameStringer CLI — Engine-independent localization utilities."""
        setup_logger(verbose=verbose, quiet=quiet)

    @main.command(name="check-fonts")
    @click.option("--input", "-i", "input_path", required=True, type=click.Path(exists=True), help="Input game file or directory")
    @click.option("--engine", "-e", required=True, type=str, help="Engine name (unity, il2cpp, unreal, renpy, cri)")
    def check_fonts_cmd(input_path: str, engine: str):
        """Check game font assets for Hungarian character glyph support (ő/ű)."""
        res = check_game_fonts(input_path, engine)
        status = res.get("status")
        if status == "supported":
            click.secho(f"{res.get('message')}", fg="green")
            sys.exit(0)
        elif status == "warning":
            click.secho(f"{res.get('message')}", fg="yellow")
            sys.exit(0)
        else:
            click.echo(res.get("message"))
            sys.exit(0)

    @main.command(name="fix-catalog")
    @click.option("--input", "-i", "input_path", required=True, type=click.Path(exists=True), help="Input Unity game directory containing Addressables catalog.json")
    def fix_catalog_cmd(input_path: str):
        """Recalculate CRC32 hashes for patched asset bundles and update catalog.json."""
        res = fix_catalog_crc_command(input_path)
        if res.get("catalog_found"):
            click.secho(f"[SUCCESS] {res.get('message')}", fg="green")
            sys.exit(0)
        else:
            click.secho(f"[WARNING] {res.get('message')}", fg="yellow")
            sys.exit(0)

    def _proxy_to_locpipe(subcmd: str, extra_args: list[str]):
        from locpipe.cli import main as locpipe_main
        sys.exit(locpipe_main([subcmd] + list(extra_args)))

    @main.command(name="gui")
    def gui_cmd():
        """Launch the GameStringer Desktop GUI."""
        from gamestringer.desktop_gui.app import main as main_desktop_gui
        main_desktop_gui()

    @main.command(name="plan", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
    @click.pass_context
    def plan_cmd(ctx):
        """Pre-flight token & batch estimation (via locpipe)."""
        _proxy_to_locpipe("plan", ctx.args)

    @main.command(name="run", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
    @click.pass_context
    def run_cmd(ctx):
        """Execute translation pipeline (via locpipe)."""
        _proxy_to_locpipe("run", ctx.args)

    @main.command(name="audit", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
    @click.pass_context
    def audit_cmd(ctx):
        """Audit extraction noise & excluded paths (via locpipe)."""
        _proxy_to_locpipe("audit", ctx.args)

    @main.command(name="verify", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
    @click.pass_context
    def verify_cmd(ctx):
        """Verify post-run merge integrity (via locpipe)."""
        _proxy_to_locpipe("verify", ctx.args)

    @main.command(name="init", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
    @click.pass_context
    def init_cmd(ctx):
        """Scaffold a new localization project (via locpipe)."""
        _proxy_to_locpipe("init", ctx.args)

    @main.command(name="tm-invalidate", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
    @click.pass_context
    def tm_invalidate_cmd(ctx):
        """Invalidate TM entries to force re-translation (via locpipe)."""
        _proxy_to_locpipe("tm-invalidate", ctx.args)

    @main.command(name="bootstrap-resources", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
    @click.pass_context
    def bootstrap_resources_cmd(ctx):
        """AI-bootstrap glossary & style guide from TM (via locpipe)."""
        _proxy_to_locpipe("bootstrap-resources", ctx.args)

except ImportError:
    import argparse

    def main():
        setup_logger()
        parser = argparse.ArgumentParser(prog="gamestringer", description="GameStringer CLI — Localization utilities.")
        parser.add_argument("--verbose", "-v", action="store_true")
        parser.add_argument("--quiet", "-q", action="store_true")
        subparsers = parser.add_subparsers(dest="command")

        # check-fonts
        cf_p = subparsers.add_parser("check-fonts")
        cf_p.add_argument("--input", "-i", required=True)
        cf_p.add_argument("--engine", "-e", required=True)

        # fix-catalog
        fc_p = subparsers.add_parser("fix-catalog")
        fc_p.add_argument("--input", "-i", required=True)

        # gui
        subparsers.add_parser("gui")

        # locpipe bridged commands
        for cmd_name in ("plan", "run", "audit", "verify", "init", "tm-invalidate", "bootstrap-resources"):
            subparsers.add_parser(cmd_name)

        args, rest = parser.parse_known_args()

        if args.command == "check-fonts":
            rep = check_game_fonts(args.input, args.engine)
            print(f"Font check result: {rep.get('message')}")
            sys.exit(0)
        elif args.command == "fix-catalog":
            rep = fix_catalog_crc_command(args.input)
            print(f"Fix catalog result: {rep.get('message')}")
            sys.exit(0)
        elif args.command == "gui":
            from gamestringer.desktop_gui.app import main as main_desktop_gui
            main_desktop_gui()
        elif args.command in ("plan", "run", "audit", "verify", "init", "tm-invalidate", "bootstrap-resources"):
            from locpipe.cli import main as locpipe_main
            sys.exit(locpipe_main([args.command] + rest))
        else:
            parser.print_help()


if __name__ == "__main__":
    main()
