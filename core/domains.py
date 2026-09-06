"""Normalizacja adresów do klucza limitu (eTLD+1)."""

from __future__ import annotations

from urllib.parse import urlsplit

import tldextract

# suffix_list_urls=() wyłącza pobieranie listy z sieci przy pierwszym użyciu.
# Demon jako usługa systemowa nie ma prawa nagle iść do internetu.
_extract = tldextract.TLDExtract(suffix_list_urls=())

_ALLOWED_SCHEMES = {"http", "https"}


def normalize_domain(url: str) -> str | None:
    """'https://www.youtube.com/watch?v=x' -> 'youtube.com'

    Zwraca None dla adresów, których nie da się lub nie należy śledzić
    (chrome://, file://, about:blank, puste).
    """
    if not url or not url.strip():
        return None

    url = url.strip()
    parts = urlsplit(url)

    if parts.scheme and parts.scheme.lower() not in _ALLOWED_SCHEMES:
        return None
    if not parts.scheme:
        parts = urlsplit(f"http://{url}")

    host = parts.hostname
    if not host:
        return None

    host = host.lower().rstrip(".")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass

    ext = _extract(host)
    if not ext.suffix:
        return host  # localhost, adresy IP, hosty intranetowe

    return ext.registered_domain