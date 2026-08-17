from .version import __version__


def main():
    """Songhive application entry point."""
    from .app import main as _main

    _main()


__all__ = ["main", "__version__"]
