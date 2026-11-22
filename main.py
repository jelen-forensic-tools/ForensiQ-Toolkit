import sys
import os

# Add the project to the import path
sys.path.insert(0, os.path.dirname(__file__))

# HEIC/HEIF (iPhone photos) support for Pillow – thumbnails in the GUI, PDF report
# previews and EXIF analysis. Optional: without pillow-heif these files get no preview.
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

from gui.app import ForenziQApp


def main():
    app = ForenziQApp()
    app.mainloop()


if __name__ == "__main__":
    main()
