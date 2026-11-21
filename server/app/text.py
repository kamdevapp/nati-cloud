"""Normalizacja tekstu do wyszukiwania: bez wielkości liter i bez znaków diakrytycznych,
żeby „spiewaj” znajdowało „Śpiewaj”, a „lodz” – „Łódź”."""

import unicodedata

# Litery, których NFKD nie rozkłada na literę bazową + znak diakrytyczny.
_EXTRA = str.maketrans({"ł": "l", "ø": "o", "đ": "d", "ß": "ss", "æ": "ae", "œ": "oe", "ı": "i"})


def normalize(*parts: str | None) -> str:
    text = " ".join(p for p in parts if p).casefold().translate(_EXTRA)
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


# Brak tagu „wykonawca” / „album” – stały znacznik zapisywany w bibliotece. Aplikacja wyświetla go
# w języku użytkownika (klucze library.unknown_artist / library.unknown_album), inne programy – po angielsku.
UNKNOWN_ARTIST = "Unknown artist"
UNKNOWN_ALBUM = "Unknown album"
# Dawne wartości (do wersji 0.20 – zapisywane po polsku); przy starcie zamieniane na znaczniki.
LEGACY_UNKNOWN_ARTIST = ("Nieznany wykonawca",)
LEGACY_UNKNOWN_ALBUM = ("Nieznany album",)
