"""Binary Bin host-side inference stack (v1).

The ESP32-S3 owns the mechanism and the camera; this package owns the model.
The contract between them is one call: JPEG in, bin number out. Keeping it
that narrow is what lets v2 move the same decision onto the chip without
touching the firmware's state machine.
"""

__version__ = "1.0.0"
