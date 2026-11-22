"""Single source of truth for the ForenziQ version.

Bump __version__ before every stable release so it matches the GitHub release tag (vX.Y.Z).

BUILD stays empty in the repository. The nightly CI build (.github/workflows/nightly.yml)
writes the commit hash into it, so a nightly .exe identifies itself as e.g. "1.1.0+6fce99e"
in the window title, report footers and the evidence manifest; the logo shows only the
short version.
"""

__version__ = "1.2.0"
BUILD = ""


def full_version() -> str:
    """Version incl. the nightly build id ("1.1.0" for stable builds and source runs)."""
    return f"{__version__}+{BUILD}" if BUILD else __version__
