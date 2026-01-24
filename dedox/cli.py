#!/usr/bin/env python3
"""
DeDox CLI - Command line interface for DeDox.

Usage:
    dedox setup-paperless          # Auto-configure Paperless workflow
    dedox setup-paperless --check  # Check workflow status
    dedox setup-paperless --remove # Remove DeDox workflow
    dedox setup-paperless --force  # Force recreate workflow
"""

import argparse
import asyncio
import sys

from dedox.core.config import get_settings, reload_config


def setup_paperless_command(args):
    """Handle the setup-paperless command."""
    asyncio.run(_setup_paperless_async(args))


async def _setup_paperless_async(args):
    """Async implementation of setup-paperless command."""
    from dedox.services.paperless_setup_service import PaperlessSetupService

    # Load config
    reload_config()
    settings = get_settings()

    # Initialize Paperless token if needed
    from dedox.services.paperless_service import init_paperless
    await init_paperless()

    # Create service with optional custom webhook URL
    service = PaperlessSetupService(dedox_webhook_url=args.webhook_url)

    if args.check:
        # Check status
        print("Checking Paperless-ngx integration status...")
        status = await service.get_status()

        if status.get("paperless_connected"):
            print(f"✓ Connected to Paperless at {status.get('paperless_url')}")
        else:
            print(f"✗ Cannot connect to Paperless: {status.get('error')}")
            sys.exit(1)

        if status.get("workflow_configured"):
            print(f"✓ DeDox workflow is configured (ID: {status.get('workflow_id')})")
        else:
            print("✗ DeDox workflow is NOT configured")
            print(f"  Run 'dedox setup-paperless' to create it")
            print(f"  Webhook URL: {status.get('dedox_webhook_url')}")

    elif args.remove:
        # Remove workflow
        print("Removing DeDox workflow from Paperless...")
        result = await service.remove_dedox_workflow()

        if result.get("success"):
            print(f"✓ {result.get('message')}")
        else:
            print(f"✗ Failed: {result.get('error')}")
            sys.exit(1)

    else:
        # Setup workflow
        print("Setting up DeDox workflow in Paperless-ngx...")

        if args.force:
            print("  (Force mode: will recreate if exists)")

        result = await service.setup_dedox_workflow(force=args.force)

        if result.get("success"):
            if result.get("already_exists"):
                print(f"✓ Workflow already exists (ID: {result.get('workflow_id')})")
                print("  Use --force to recreate")
            else:
                print(f"✓ {result.get('message')}")
                print(f"  Workflow ID: {result.get('workflow_id')}")
                print(f"  Webhook URL: {result.get('webhook_url')}")
        else:
            print(f"✗ Failed: {result.get('error')}")
            sys.exit(1)


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="DeDox CLI - Document processing service",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # setup-paperless command
    setup_parser = subparsers.add_parser(
        "setup-paperless",
        help="Setup Paperless-ngx workflow integration",
        description="Automatically create the webhook workflow in Paperless-ngx "
                    "to send documents to DeDox for processing.",
    )
    setup_parser.add_argument(
        "--check",
        action="store_true",
        help="Check if workflow is configured (don't create)",
    )
    setup_parser.add_argument(
        "--remove",
        action="store_true",
        help="Remove the DeDox workflow from Paperless",
    )
    setup_parser.add_argument(
        "--force",
        action="store_true",
        help="Force recreation of workflow if it exists",
    )
    setup_parser.add_argument(
        "--webhook-url",
        type=str,
        default=None,
        help="Override the webhook URL (default: auto-detect from config)",
    )
    setup_parser.set_defaults(func=setup_paperless_command)

    # Parse and execute
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
