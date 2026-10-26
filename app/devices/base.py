"""Common interface for everything the visualizer can light up."""


class OutputError(Exception):
    """A device couldn't be opened or written. The message is shown to the user."""


class Output:
    """One lightable device. Subclasses set these and implement the methods.

    id        stable id, used to remember the device's on/off switch
    name      shown in the device list
    detail    second line in the device list (backend, port, LED count)
    layout    layout.Layout of the device's LEDs, in the order send() expects
    power_budget  optional cap on the summed channel values of a frame
    keymap    key id (see keymap.py) -> LED indices under that key; empty unless it's a keyboard
              whose keys are known. Keystroke reactions and the heatmap need it.
    """

    id = ""
    name = ""
    detail = ""
    layout = None
    power_budget = None
    keymap = {}

    def open(self):
        """Prepares the device for streaming. Raises OutputError."""

    def send(self, rgb):
        """Shows one frame. rgb: (layout.n, 3) uint8. Raises OutputError."""
        raise NotImplementedError

    def release(self):
        """Hands the lighting back to the device's own effects / vendor app.
        Streaming resumes with the next send()."""

    def close(self):
        """Releases the device and frees its resources. Must not raise."""
        try:
            self.release()
        except OutputError:
            pass
