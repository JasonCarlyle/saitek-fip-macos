"""fipx -- a macOS driver for the Saitek/Logitech Flight Instrument Panel.

Talks to the FIP directly over libusb (display) and hidapi (buttons/knobs),
and drives it from X-Plane 12's built-in UDP dataref feed. No kernel
extension, no Windows DirectOutput service, no X-Plane plugin.
"""
__version__ = "1.0.0"
