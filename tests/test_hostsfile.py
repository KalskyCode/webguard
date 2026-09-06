from webguard.hostsfile import BEGIN, END, apply, render_block, strip_block

BASE = (
    "127.0.0.1 localhost\n"
    "::1 localhost\n"
    "10.0.0.5 intranet.local\n"
)


def read(path):
    return path.read_text(encoding="utf-8")


def test_apply_do_pustego_miejsca_tworzy_blok(tmp_path):
    hosts = tmp_path / "hosts"
    hosts.write_text(BASE, encoding="utf-8")

    changed = apply(["instagram.com"], path=hosts)

    assert changed is True
    text = read(hosts)
    assert "127.0.0.1 localhost" in text  # reszta nietknięta
    assert "0.0.0.0 instagram.com" in text
    assert "0.0.0.0 www.instagram.com" in text
    assert "0.0.0.0 m.instagram.com" in text
    assert text.count(BEGIN) == 1 and text.count(END) == 1


def test_apply_idempotentne(tmp_path):
    hosts = tmp_path / "hosts"
    hosts.write_text(BASE, encoding="utf-8")

    assert apply(["instagram.com", "x.com"], path=hosts) is True
    before = read(hosts)
    assert apply(["x.com", "instagram.com"], path=hosts) is False  # kolejność bez znaczenia
    assert read(hosts) == before


def test_apply_pustej_listy_usuwa_blok(tmp_path):
    hosts = tmp_path / "hosts"
    hosts.write_text(BASE, encoding="utf-8")

    apply(["instagram.com"], path=hosts)
    assert apply([], path=hosts) is True

    text = read(hosts)
    assert BEGIN not in text and END not in text
    assert text == BASE  # dokładnie stan wyjściowy


def test_apply_podmienia_zawartosc_bloku(tmp_path):
    hosts = tmp_path / "hosts"
    hosts.write_text(BASE, encoding="utf-8")

    apply(["instagram.com"], path=hosts)
    apply(["tiktok.com"], path=hosts)

    text = read(hosts)
    assert "instagram.com" not in text
    assert "0.0.0.0 tiktok.com" in text
    assert text.count(BEGIN) == 1


def test_zachowuje_crlf(tmp_path):
    hosts = tmp_path / "hosts"
    hosts.write_text(BASE.replace("\n", "\r\n"), encoding="utf-8")

    apply(["instagram.com"], path=hosts)

    data = hosts.read_bytes()
    assert b"\r\n" in data
    assert b"\n" not in data.replace(b"\r\n", b"")  # brak gołych LF


def test_apply_gdy_plik_nie_istnieje(tmp_path):
    hosts = tmp_path / "etc" / "hosts"  # katalog też nie istnieje

    assert apply(["instagram.com"], path=hosts) is True
    assert "0.0.0.0 instagram.com" in read(hosts)


def test_strip_uszkodzonego_bloku_bez_end_usuwa_do_konca(tmp_path):
    # blok bez znacznika END traktujemy jako nasz aż do końca pliku
    lines = [BEGIN, "0.0.0.0 stare.com", "127.0.0.1 localhost"]
    assert strip_block(lines) == []


def test_render_bloku_sortuje_i_deduplikuje():
    block = render_block(["x.com", "a.com", "x.com"])
    domains_in_order = [ln.split()[1] for ln in block if ln.startswith("0.0.0.0")]
    assert domains_in_order == ["a.com", "www.a.com", "m.a.com",
                                "x.com", "www.x.com", "m.x.com"]
