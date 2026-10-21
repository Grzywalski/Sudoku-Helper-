#sprint_03
# Wprowadzenie
Sudoku AI to prosta aplikacja desktopowa napisana w Pythonie (pygame), która generuje, rozwiązuje i sprawdza sudoku, a dodatkowo pozwala zapytać model LLM o podpowiedź. Planszę rozwiązuje klasyczny solver, więc model nie musi sam "zgadywać" rozwiązania (co, jak pokazały wcześniejsze testy, bywa zawodne). Dostaje aktualny stan planszy, opcjonalnie gotowe rozwiązanie (tylko jako wiedzę dla siebie) i pełni rolę asystenta, który podpowiada kolejny ruch.

Kod źródłowy oraz gotowy plik `.exe`: W Release

# Funkcje
- **Generuj** - losuje planszę o jednym rozwiązaniu, trzy poziomy trudności: Łatwy (40 podanych cyfr), Średni (32), Trudny (26)
- **Rozwiąż** - uzupełnia planszę solverem (cyfry wstawione automatycznie są zielone)
- **Sprawdź** - wykrywa konflikty (czerwone cyfry) i ocenia, czy plansza jest poprawnie rozwiązana
- **Reset / Wyczyść** - usuwa wpisane cyfry (zostają cyfry początkowe) albo czyści całą planszę, by wpisać własne sudoku
- **Podpowiedź (AI)** - wysyła planszę i pytanie gracza do modelu, a odpowiedź pokazuje w panelu obok

Sterowanie: klik w pole + cyfry `1-9` (`0` / `Backspace` / `Delete` kasuje), strzałki przesuwają zaznaczenie, `Enter` w polu pytania wysyła zapytanie do AI.

# Jak to działa

### Solver i generator
- **Solver** - backtracking z heurystyką MRV (zawsze wybiera pole z najmniejszą liczbą kandydatów) na bitmaskach, rozwiązuje typowe sudoku w milisekundach
- **Generator** - losuje pełną planszę, a następnie usuwa cyfry po jednej, za każdym razem sprawdzając, czy rozwiązanie nadal jest jedyne

### Podpowiedzi AI
Zapytanie jest wysyłane w osobnym wątku, więc okno nie zawiesza się podczas oczekiwania na odpowiedź. Do modelu trafia stały prompt systemowy (po polsku, krótko, bez markdown, pola w formacie `(wiersz, kolumna)`), aktualna plansza oraz polecenie gracza. Jeśli gracz popełnił błąd, model dostaje o tym informację, a rozwiązanie liczone jest wtedy tylko z cyfr początkowych. Wysyłanie rozwiązania do modelu można wyłączyć stałą `SEND_SOLUTION_TO_AI` w `main.py`.

# Konfiguracja AI (`conf.txt`)
Z jakim modelem i pod jakim adresem łączy się program, określa plik `conf.txt` leżący **obok `main.py` / `.exe`**. Plik jest wczytywany przy każdym zapytaniu, więc zmiany działają bez restartu programu. Jeśli go nie ma, program utworzy domyślny.

```
provider=openai
url=http://localhost:1234/v1
model=nazwa-modelu
```

| `provider` | Do czego |
| :--- | :--- |
| `openai` | Każdy serwer zgodny z API OpenAI: OpenAI, OpenRouter, Groq, LM Studio, vLLM, llama.cpp, Ollama (`/v1`) itd. |
| `ollama` | Natywne API Ollamy (`/api/chat`) |
| `anthropic` | Claude (Anthropic API) |
| `gemini` | Google Gemini |

Opcjonalne klucze: `api_key` (alternatywnie zmienna środowiskowa `LLM_API_KEY`), `max_tokens`, `max_tokens_param` (tylko `openai`), `thinking_level` (tylko `gemini`) oraz `header=Nazwa: wartość` (dodatkowe nagłówki HTTP). Pełna lista z przykładami jest w pliku `conf.txt` w repozytorium.

# Uruchomienie

Gotowy plik znajdziesz w Releases na githubie. Skopiuj `conf.txt` do tego samego folderu co plik .exe.

---
**Uwagi**
- Poza `pygame` program używa wyłącznie biblioteki standardowej Pythona.
- Klucz API w `conf.txt` jest zapisany jawnym tekstem, więc nie udostępniaj tego pliku razem z `.exe`.
- Przy modelach w chmurze plansza (i opcjonalnie rozwiązanie) jest wysyłana do zewnętrznego dostawcy API.

