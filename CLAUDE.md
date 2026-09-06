# WebGuard

Aplikacja do samokontroli: mierzy czas spędzony na wskazanych stronach WWW i blokuje je
po przekroczeniu dziennego limitu (domyślnie 20 minut). Projekt osobisty, Windows, jeden
użytkownik. Autor: Max, junior frontend dev (React/Next/TS, trochę Pythona).

## Założenie architektoniczne #1: tarcie, nie nieomylność

Użytkownik jest administratorem swojej maszyny, więc blokada **nie może być** absolutnie
nieomijalna. Zawsze zostaje tryb awaryjny, konto admina, live USB.

Celem projektu jest sprawić, żeby obejście zajmowało 15-20 minut świadomej, upierdliwej
pracy zamiast jednego kliknięcia. Impuls "wejdę na 2 minuty" nie przeżywa 15 minut
grzebania w usługach Windows.

**Nie proponuj rozwiązań, które udają nieomijalność.** Oceniaj pomysły przez pytanie
"ile minut świadomego wysiłku to kosztuje", nie "czy da się to obejść".

## Założenie architektoniczne #2: kontrakt Ulissesa

Nie ma hasła odblokowującego. Nie ma trybu awaryjnego dla użytkownika. Zamiast tego:

- **zaostrzenie** reguły (krótszy limit, dodanie strony, anulowanie usunięcia) działa NATYCHMIAST
- **poluzowanie** (dłuższy limit, usunięcie reguły) wchodzi w życie po **24 godzinach**

To jest serce projektu. Kolumny `pending_limit_seconds`, `pending_effective_at`
i `remove_effective_at` w tabeli `rules` istnieją wyłącznie po to. Jeśli jakakolwiek
zmiana pozwoliłaby ominąć to opóźnienie, jest błędem, nawet jeśli poprawia UX.

## Stack

| Warstwa | Wybór | Uzasadnienie |
|---|---|---|
| Demon | Python 3.11+ | Autor go zna; liczy się poprawność logiki, nie wydajność |
| Baza | SQLite + `sqlite3` ze stdlib | Bez ORM-a, kilka zapytań, narzut bez zysku |
| Usługa Windows | WinSW | `pywin32` do usług jest upierdliwy i słabo się debuguje |
| Rozszerzenie | Manifest V3, czysty JS | Service worker to ~200 linii, bundler by przeszkadzał |
| GUI | Tauri v2 + React + TS | Mocna strona autora; ~10 MB zamiast ~150 MB Electrona |
| Pakowanie | PyInstaller onefile | Na końcu, przy wdrożeniu |

Rust byłby technicznie lepszy dla usługi systemowej, ale odrzucony świadomie: przy
doświadczeniu autora kosztowałby dwa tygodnie walki z borrow checkerem zamiast
działającego programu. Nie proponuj przepisania na Rust.

Furtka: jeśli toolchain Rusta/MSVC zablokuje pracę na dłużej niż godzinę, przesiadamy
się na Electron. Kod React jest wtedy identyczny.

## Architektura

Trzy procesy, jasno rozdzielone:

**1. Rozszerzenie przeglądarki (MV3)** — pomiar czasu
Jedyny komponent wiedzący, która karta jest aktywna. Nasłuchuje `tabs.onActivated`,
`windows.onFocusChanged`, `idle.onStateChanged`. Nie liczy czasu, gdy okno jest w tle
albo użytkownik jest bezczynny. Raportuje przyrostowo co ~5 s.

**2. Demon (usługa Windows, LocalSystem)** — źródło prawdy
Cały stan i wszystkie decyzje. Rozszerzenie i GUI to cienkie klienty, które o nic nie
proszą, tylko dostają decyzje. Baza z ACL tylko dla SYSTEM.

**3. GUI (Tauri + React + TS)** — konfiguracja
Lista stron, limity, statystyki. Nie ma dostępu do bazy. Każdą zmianę waliduje demon,
łącznie z odmową natychmiastowego poluzowania.

### Protokół

Demon wystawia HTTP na `127.0.0.1`, podzielone na dwa kanały o różnym zaufaniu:

- **ingest**, bez uwierzytelnienia: `POST /usage`, `GET /status`. Najgorsze, co złośliwa
  strona może zrobić, to nasłać fałszywe sekundy i sama sobie coś zablokować.
- **admin**, z tokenem z `meta.admin_token`: wszystko, co dotyka reguł i limitów.
  Dodatkowo demon odrzuca żądania z niepustym nagłówkiem `Origin`.

Native Messaging został rozważony i **odrzucony**: host jest uruchamiany przez Chrome na
koncie użytkownika, nie usługi, więc i tak potrzebowałby drugiego kanału do demona.

### Warstwy egzekwowania

Blokada nie może zależeć od rozszerzenia, bo rozszerzenie wyłącza się jednym kliknięciem.

| Warstwa | Rola |
|---|---|
| `declarativeNetRequest` w rozszerzeniu | Precyzja (per URL i ścieżka), ładna strona blokady |
| Plik `hosts` | Działa we wszystkich przeglądarkach i w incognito |
| Chrome Enterprise Policy (HKLM) | Wymusza instalację rozszerzenia, blokuje usunięcie |
| Watchdog + recovery usługi | Wstaje po zabiciu procesu |

Trzeba też polityką wyłączyć DNS-over-HTTPS, bo inaczej Chrome potrafi ominąć `hosts`.

### Incognito

Decyzja: incognito zostaje włączone, blokada leci przez `hosts`.

Znany kompromis: zgoda rozszerzenia na działanie w incognito to przełącznik, który
użytkownik musi kliknąć ręcznie, i nie ma polityki Chrome, która by to wymusiła
(**do zweryfikowania przy implementacji, zmienia się między wersjami**). Bez tego czas
w incognito nie jest liczony wcale.

Mitygacja: demon traktuje **brak sygnału od rozszerzenia jako podejrzany**, nie jako zero,
i wtedy prewencyjnie blokuje monitorowane domeny w `hosts`.

Wykrywanie okien incognito od strony systemu (nazwa procesu, tytuł okna) zostało
sprawdzone i nie działa: Chrome trzyma je w tym samym procesie i nie oznacza w tytule.

## Stan projektu

### Zrobione — krok 1: rdzeń

```
webguard/
├── core/
│   ├── __init__.py
│   ├── clock.py       # granica doby
│   ├── domains.py     # normalizacja URL -> eTLD+1
│   └── storage.py     # SQLite, jedyne miejsce dotykające bazy
├── tests/
│   ├── test_clock.py
│   └── test_storage.py
├── pytest.ini
├── pyrefly.toml
└── requirements.txt
```

Testy przechodzą. Cztery decyzje w modelu danych, które trzeba respektować:

1. **Doba resetuje się o 4:00, nie o północy.** Scrollowanie o 00:30 to ta sama sesja co
   o 23:30. Godzina siedzi w `meta.day_start_hour`. `current_day()` realizuje to przez
   przesunięcie osi czasu, bez rozgałęzień — nie przepisuj tego na `if`-y.
2. **Klucz to domena eTLD+1**, nie pełny URL. `www.youtube.com`, `m.youtube.com`
   i `youtube.com/watch?v=x` dzielą jeden limit. Obie strony porównania są znormalizowane,
   więc dopasowanie to zwykłe `==` — bez regexów i wzorców.
3. **Czas doliczany przyrostowo** (`add_usage(delta)`), nie przez start/stop. Awaria
   kosztuje maks. 5 sekund zamiast całej sesji.
4. **Funkcje przyjmują opcjonalny `now: datetime | None`.** Bez wstrzykiwania zegara nie da
   się przetestować logiki 24 h. Nie wołaj `datetime.now()` w środku funkcji domenowych.

Dwa miejsca celowo napisane nieoczywiście:
- `request_remove_rule` wywołane ponownie **nie przesuwa** terminu (inaczej klikanie
  w GUI odwlekałoby usunięcie w nieskończoność)
- `apply_pending` kasuje reguły **przed** aktualizacją limitów, w jednej transakcji
  `BEGIN IMMEDIATE`, żeby demon i GUI nie weszły sobie w drogę

`tldextract` jest skonfigurowany z `suffix_list_urls=()`. Demon jako usługa systemowa nie
ma prawa nagle iść do internetu. Nie usuwaj tego.

### Do zrobienia

2. **Rozszerzenie i pomiar czasu** — na razie logujące do konsoli ← NASTĘPNY KROK
3. **Egzekwowanie** — `hosts` + `declarativeNetRequest`
4. **Demon jako usługa** — WinSW, ACL, watchdog
5. **GUI** — Tauri + React

GUI jest ostatnie mimo że to najmocniejsza strona autora. Powód: zaczynając od GUI
skończy się po tygodniu z piękną listą stron, która niczego nie blokuje, i projekt umrze.
Rdzeń ma działać, choćby obsługiwany komendami w terminalu.

### Otwarte pytanie

Czy `apply_pending` wołać w każdym cyklu demona (~5 s), czy raz na minutę? Do rozstrzygnięcia
przy kroku 4 — ma znaczenie dla obciążenia bazy.

## Konwencje i środowisko

- Komponenty React: **funkcje strzałkowe** z `export default` na dole pliku.
  Nigdy `export default function` inline.
- Kod, nazwy i komentarze techniczne po angielsku. Rozmowa z autorem po polsku.
- Testy: `python -m pytest -v` z katalogu głównego (`pythonpath = .` w `pytest.ini`).
- Edytor używa Pyrefly, który nie wykrywa automatycznie `.venv` — stąd `pyrefly.toml`
  z `python-interpreter`. Klucz bywał nazywany na trzy sposoby w różnych wersjach.
  Podkreślenia importów w edytorze bywają fałszywe; weryfikuj uruchomieniem w terminalu.

## Jak pracować z autorem

Max uczy się przez budowanie i **prosi o podejście krok po kroku**. Nie generuj całych
gotowych rozwiązań naraz.

W praktyce: tłumacz decyzję przed kodem, dawaj jeden moduł na raz, wskazuj miejsca gdzie
łatwo się potknąć i zostawiaj je do samodzielnego napisania, jeśli są pouczające.
Gotowy kod dawaj tam, gdzie zła decyzja kosztuje później dużo (schemat bazy, protokół),
a nie tam, gdzie chodzi o wprawę.

Mów wprost, gdy pomysł jest zły. Autor pytał wcześniej o Rust vs Python i dostał
uzasadnioną rekomendację wbrew "lepszej" opcji — takiej szczerości oczekuje.
