"""CharDisplay — implements StatusDisplayPort via a 16x4 I2C character LCD.

★ New. Introduced in ADR-008's revision once the Pi target was confirmed
headless (no Chromium kiosk) — this is the device's only visual output
besides the mute LED. Uses RPLCD, a standard I2C character-LCD driver.
"""

from __future__ import annotations


class CharDisplay:
    """Implements StatusDisplayPort via a 16x4 HD44780-class I2C LCD."""

    def __init__(self, i2c_address: int = 0x27, cols: int = 16, rows: int = 4):
        self._cols = cols
        self._rows = rows
        self._lcd = None
        self._i2c_address = i2c_address

    @property
    def cols(self) -> int:
        return self._cols

    @property
    def rows(self) -> int:
        return self._rows

    def start(self) -> None:
        from RPLCD.i2c import CharLCD

        self._lcd = CharLCD("PCF8574", self._i2c_address, cols=self._cols, rows=self._rows)

    def stop(self) -> None:
        if self._lcd is not None:
            self._lcd.close(clear=True)

    def show(self, lines: tuple[str, ...]) -> None:
        if self._lcd is None:
            return
        self._lcd.clear()
        for i, line in enumerate(lines[: self._rows]):
            self._lcd.cursor_pos = (i, 0)
            self._lcd.write_string(line[: self._cols])