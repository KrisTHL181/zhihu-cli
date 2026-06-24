"""Profile management commands — save and switch between multiple logins."""

import json

import click

from zhihu_cli.content.handlers.cache_manager import cache_manager
from zhihu_cli.content.handlers.requests import reload_session
from zhihu_cli.output import (
    echo,
    error,
    f_bold,
    f_meta,
    info,
    print_json,
    success,
)


def register_profile(main_group):
    """Register the ``profile`` command group on *main_group*."""

    @main_group.group()
    def profile() -> None:
        """Manage account profiles — save and switch between multiple logins."""

    @profile.command("list")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def profile_list(output_json: bool) -> None:
        """List all saved profiles."""
        active = cache_manager.get_active_profile()
        profiles = [p for p in cache_manager.list_profiles() if not p.startswith("_")]
        if not profiles:
            if output_json:
                print_json([])
            else:
                info("No profiles found. Use 'zhihu auth paste --profile <name>' to create one.")
            return
        if output_json:
            result = []
            for name in profiles:
                path = cache_manager._resolve_profile_path(name)
                try:
                    data = json.loads(path.read_text())
                    has_cookie = "cookie" in {k.lower() for k in data}
                except (json.JSONDecodeError, OSError):
                    has_cookie = False
                result.append({"name": name, "active": name == active, "has_cookie": has_cookie})
            print_json(result)
            return
        for name in profiles:
            marker = " *" if name == active else ""
            path = cache_manager._resolve_profile_path(name)
            try:
                data = json.loads(path.read_text())
                cookie = "cookie" in {k.lower() for k in data}
            except (json.JSONDecodeError, OSError):
                cookie = False
            status = "cookie" if cookie else "no cookie"
            echo(f"  {f_bold(name)}{marker}  ({f_meta(status)})")

    @profile.command("switch")
    @click.argument("name")
    def profile_switch(name: str) -> None:
        """Switch to a different profile."""
        try:
            cache_manager.switch_profile(name)
            reload_session()
            success(f"Switched to profile '{name}'.")
        except ValueError:
            error(f"Profile '{name}' does not exist. Use 'zhihu profile list' to see saved profiles.")
            raise SystemExit(1)

    @profile.command("delete")
    @click.argument("name")
    @click.option("--force", is_flag=True, help="Skip confirmation")
    def profile_delete(name: str, force: bool) -> None:
        """Delete a saved profile."""
        profiles = cache_manager.list_profiles()
        if name not in profiles:
            error(f"Profile '{name}' does not exist.")
            raise SystemExit(1)
        if name.startswith("_"):
            error(f"Cannot delete internal profile '{name}'.")
            raise SystemExit(1)

        if not force:
            click.confirm(f"Delete profile '{name}'?", abort=True)

        cache_manager.delete_profile(name)
        success(f"Deleted profile '{name}'.")

    @profile.command("current")
    @click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON")
    def profile_current(output_json: bool) -> None:
        """Show the currently active profile."""
        active = cache_manager.get_active_profile()
        if output_json:
            print_json({"active_profile": active})
            return
        if active:
            echo(active)
        else:
            error("No active profile set.")

    _LOGOUT_PROFILE = "_logout_"

    @profile.command("logout")
    def profile_logout() -> None:
        """Switch to an unauthenticated session (hidden profile).

        Switches the active profile to a hidden profile with no stored
        credentials. Use 'zhihu profile switch <name>' to log back in.
        """
        active = cache_manager.get_active_profile()
        if active == _LOGOUT_PROFILE:
            info("Already logged out.")
            return

        # Ensure the hidden logout profile exists with empty headers
        if _LOGOUT_PROFILE not in cache_manager.list_profiles():
            cache_manager.save_headers({}, profile_name=_LOGOUT_PROFILE)

        cache_manager.switch_profile(_LOGOUT_PROFILE)
        reload_session()
        echo("Logged out. Use 'zhihu profile switch <name>' to log back in.")
