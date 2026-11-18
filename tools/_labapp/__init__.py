"""labapp (working name): what mosaic and prose have in common, and nothing about figures or
manuscripts. A Python backend with a web page as its interface needs the same things in both:
each system's folders and ways of opening files, a log and a problem report, the local server,
kept copies, the Mac's menus, updates.

Each app carries its own copy (tools/vendor.py puts one in <app>/_labapp), so an installed app
needs nothing from here. Inside the package, imports are relative, so the copy works under any name.
"""
__version__ = "0.1.0"
