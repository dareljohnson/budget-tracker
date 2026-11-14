"""Errors raised while reading, scanning or parsing a receipt."""


class ReceiptError(Exception):
    """Base class for receipt capture failures."""


class ReceiptReadError(ReceiptError):
    """The receipt file could not be read or recognised."""


class ReceiptOcrUnavailable(ReceiptReadError):
    """The local OCR stack is not installed or could not be loaded."""


class ReceiptScanError(ReceiptError):
    """The scanner could not be reached or returned no image."""
