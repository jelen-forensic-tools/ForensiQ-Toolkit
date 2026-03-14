import sys
import os

# Přidej projekt do cesty
sys.path.insert(0, os.path.dirname(__file__))

from gui.app import ForenziQApp


def main():
    app = ForenziQApp()
    app.mainloop()


if __name__ == "__main__":
    main()
