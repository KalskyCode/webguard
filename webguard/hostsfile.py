"""Wydzielony blok w pliku hosts. Reszta pliku zostaje nietknięta.

Blok wygląda tak:

    # >>> WebGuard >>> managed block, do not edit
    0.0.0.0 instagram.com
    0.0.0.0 www.instagram.com
    0.0.0.0 m.instagram.com
    # <<< WebGuard <<<

hosts nie zna wildcardów, więc dla każdej domeny eTLD+1 wpisujemy stały zestaw
prefiksów subdomen. Egzekwowanie per-subdomena spoza tej listy zostaje na
warstwie declarativeNetRequest (dopasowanie po sufiksie domeny).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

BEGIN = "# >>> WebGuard >>> managed block, do not edit"
END = "# <<< WebGuard <<<"

SUBDOMAIN_PREFIXES = ("", "www.", "m.")
BLACKHOLE = "0.0.0.0"


def default_hosts_path() -> Path:
    """Ścieżka do hosts. WEBGUARD_HOSTS_FILE nadpisuje ją do testów i dev-u."""
    override = os.environ.get("WEBGUARD_HOSTS_FILE")
    if override:
        return Path(override)
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return Path(root) / "System32" / "drivers" / "etc" / "hosts"


def render_block(domains: list[str]) -> list[str]:
    if not domains:
        return []
    lines = [BEGIN]
    for domain in sorted(set(domains)):
        for prefix in SUBDOMAIN_PREFIXES:
            lines.append(f"{BLACKHOLE} {prefix}{domain}")
    lines.append(END)
    return lines


def strip_block(lines: list[str]) -> list[str]:
    """Zwraca wiersze bez naszego bloku (i bez uszkodzonego bloku bez END)."""
    out: list[str] = []
    inside = False
    for line in lines:
        stripped = line.strip()
        if stripped == BEGIN:
            inside = True
            continue
        if inside:
            if stripped == END:
                inside = False
            continue
        out.append(line)
    return out


def apply(domains: list[str], path: Path | None = None) -> bool:
    """Ustawia blok WebGuard na dokładnie `domains`. True, gdy plik się zmienił.

    Styl końca wiersza (LF/CRLF) pliku jest zachowany. Zapis jest atomowy:
    plik tymczasowy w tym samym katalogu i os.replace.
    """
    path = path or default_hosts_path()
    # read_bytes, nie read_text: read_text tłumaczy \r\n -> \n, co zabija
    # wykrywanie stylu końca wiersza i porównanie "czy się zmieniło".
    raw_bytes = path.read_bytes() if path.exists() else b""
    raw = raw_bytes.decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"

    kept = strip_block(raw.splitlines())  # splitlines radzi sobie z \r\n, \n, \r
    while kept and kept[-1].strip() == "":
        kept.pop()

    block = render_block(domains)
    if block:
        new_lines = kept + [""] + block if kept else block
    else:
        new_lines = kept

    new_text = newline.join(new_lines)
    if new_text:
        new_text += newline

    if new_text.encode("utf-8") == raw_bytes:
        return False

    _atomic_write(path, new_text)
    return True


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".webguard-hosts-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
