import os
import re
import sys
import json
import time
import random
import threading
import urllib.request
import urllib.error

import pygame

#  KONFIGURACJA AI
API_KEY = os.environ.get("LLM_API_KEY", "")
MAX_TOKENS = 3000

# Dostawca AI, adres, model, klucz API itd. są wczytywane z pliku conf.txt leżącego obok programu.
CONF_NAME = "conf.txt"
PROVIDERS = ("ollama", "openai", "anthropic", "gemini")
DEFAULT_URLS = {
    "ollama": "http://localhost:11434/api/chat",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
}
DEFAULT_MODELS = {"ollama": "qwen3.5", "gemini": "gemini-3.5-flash"}   # dla pozostałych model=... jest wymagany
KEY_REQUIRED = ("anthropic", "gemini")
DEFAULT_CONF_TEXT = """# Konfiguracja połączenia z AI (format: klucz=wartość, linie z # są ignorowane)
#
# provider:
#   ollama     natywne API Ollamy (/api/chat)
#   openai     KAŻDY serwer zgodny z API OpenAI (/v1/chat/completions): OpenAI, OpenRouter, Groq, Together,
#              Mistral, DeepSeek, LM Studio, vLLM, llama.cpp, Ollama (/v1) i wiele innych
#   anthropic  Claude (Anthropic API)
#   gemini     Google Gemini
#
# Klucze opcjonalne:
#   api_key=...            klucz API (alternatywa: zmienna środowiskowa LLM_API_KEY)
#   max_tokens=3000        limit długości odpowiedzi
#   max_tokens_param=...   tylko openai: nazwa pola limitu, domyślnie max_tokens
#                          (modele rozumujące OpenAI wymagają: max_completion_tokens)
#   thinking_level=low     tylko gemini: ogranicza "myślenie" modelu
#   header=Nazwa: wartość  dodatkowy nagłówek HTTP (można powtarzać w wielu liniach)

provider=ollama
url=http://localhost:11434/api/chat
model=qwen3.5

# ---------- Przykłady: odkomentuj JEDEN zestaw, a trzy powyższe linie zakomentuj ----------

# LM Studio / vLLM / llama.cpp / Ollama przez /v1 (lokalnie, bez klucza):
# provider=openai
# url=http://localhost:1234/v1
# model=nazwa-modelu

# OpenAI:
# provider=openai
# url=https://api.openai.com/v1
# model=nazwa-modelu
# api_key=sk-...

# OpenRouter (dowolny model, np. model=anthropic/claude-sonnet-4.5):
# provider=openai
# url=https://openrouter.ai/api/v1
# model=nazwa-modelu
# api_key=sk-or-...

# Claude (Anthropic):
# provider=anthropic
# url=https://api.anthropic.com
# model=nazwa-modelu
# api_key=sk-ant-...

# Gemini:
# provider=gemini
# url=https://generativelanguage.googleapis.com/v1beta
# model=gemini-3.5-flash
# api_key=TWOJ_KLUCZ_Z_GOOGLE_AI_STUDIO
# thinking_level=low
"""

# Czy wysyłać do AI gotowe rozwiązanie (tylko jako wiedzę dla AI, nie dla gracza).
SEND_SOLUTION_TO_AI = False

# Stały prompt techniczny 
BASE_PROMPT = (
    "Jesteś asystentem w aplikacji do Sudoku. "
    "Plansza ma 9 wierszy i 9 kolumn, numerowanych od 1. Wiersze liczymy od góry, kolumny od lewej. "
    "Znak '.' oznacza puste pole. Dostajesz aktualny stan planszy, a czasem także jej rozwiązanie "
    "(to wiedza tylko dla Ciebie, gracz go nie widzi). "
    "Zasady odpowiedzi: pisz po polsku, krótko, zwykłym tekstem bez markdown. "
    "Wykonuj polecenie gracza. Jeśli prosi o wskazówkę, nie zdradzaj całego rozwiązania "
    "ani więcej niż trzeba, chyba że gracz wprost o to prosi. "
    "Podawaj pola w formacie (kolumna,wiersz)."
)


def build_system_prompt():
    return BASE_PROMPT

def app_dir():
    """Folder, w którym leży program: obok .exe (po kompilacji) albo obok main.py."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def load_config():
    """Wczytuje ustawienia z conf.txt. Gdy pliku nie ma, tworzy go z wartościami domyślnymi."""
    path = os.path.join(app_dir(), CONF_NAME)
    raw, headers = {}, {}

    if not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(DEFAULT_CONF_TEXT)
        except OSError:
            pass  # np. brak uprawnień do zapisu - zostają wartości domyślne
    else:
        try:
            with open(path, "r", encoding="utf-8-sig") as f:   # utf-8-sig: Notatnik dodaje BOM
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key, value = key.strip().lower(), value.strip()
                    if key == "header":                        # header=Nazwa: wartość (można powtarzać)
                        if ":" in value:
                            name, hval = value.split(":", 1)
                            headers[name.strip()] = hval.strip()
                    else:
                        raw[key] = value.strip('"').strip("'")
        except (OSError, UnicodeDecodeError) as e:
            raise RuntimeError(f"Nie można odczytać {path}: {e}")

    url = raw.get("url", "")
    provider = raw.get("provider", "").lower()
    if not provider:   # brak wpisu: rozpoznaj po adresie
        if "googleapis.com" in url:
            provider = "gemini"
        elif "anthropic.com" in url:
            provider = "anthropic"
        elif "/chat/completions" in url or re.search(r"/v1(/|$)", url):
            provider = "openai"
        else:
            provider = "ollama"
    if provider not in PROVIDERS:
        raise RuntimeError(f"Nieznany provider '{provider}' w {CONF_NAME} (dozwolone: {', '.join(PROVIDERS)}).")

    model = raw.get("model") or DEFAULT_MODELS.get(provider)
    if not model:
        raise RuntimeError(f"Ustaw model=... w {CONF_NAME} (provider: {provider}).")
    try:
        max_tokens = int(raw["max_tokens"]) if raw.get("max_tokens") else MAX_TOKENS
    except ValueError:
        raise RuntimeError(f"max_tokens w {CONF_NAME} musi być liczbą całkowitą.")

    return {
        "provider": provider,
        "url": url or DEFAULT_URLS[provider],
        "model": model,
        "api_key": raw.get("api_key") or API_KEY,   # conf.txt ma pierwszeństwo przed zmienną LLM_API_KEY
        "max_tokens": max_tokens,
        "max_tokens_param": raw.get("max_tokens_param") or "max_tokens",
        "thinking_level": raw.get("thinking_level", "").lower(),
        "headers": headers,
    }


def _post_json(url, payload, headers=None):
    h = {"content-type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=h)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Brak połączenia: {e.reason}")
    except ValueError:
        raise RuntimeError(f"Nieprawidłowy adres URL w {CONF_NAME}: {url}")


def _hdrs(conf, auth=None):
    """Nagłówki uwierzytelniania + dodatkowe nagłówki z conf.txt (header=...)."""
    h = dict(auth or {})
    h.update(conf["headers"])
    return h


def _need_key(conf):
    if not conf["api_key"]:
        raise RuntimeError(f"Brak klucza API: wpisz api_key=... w {CONF_NAME} (lub ustaw zmienną LLM_API_KEY).")


def _strip_think(text):
    """Usuwa bloki <think>...</think>, które część modeli (np. Qwen, DeepSeek-R1) dokleja do odpowiedzi."""
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL | re.IGNORECASE).strip()


# ---------- Ollama (natywne API /api/chat) ----------
def _ask_ollama(conf, system, user_msg):
    auth = {"Authorization": f"Bearer {conf['api_key']}"} if conf["api_key"] else {}
    data = _post_json(conf["url"], {
        "model": conf["model"],         # np. "qwen3.5"
        "stream": False,                # bez tego Ollama zwraca strumień
        "think": False,                 # wyłącza myślenie (działa tylko na /api/chat)
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        "options": {"num_predict": conf["max_tokens"], "num_ctx": 8192},
    }, _hdrs(conf, auth))
    text = (data.get("message", {}).get("content") or "").strip()
    if not text:
        raise RuntimeError(f"Pusta odpowiedź (done_reason={data.get('done_reason')})")
    return text


# ---------- OpenAI i serwery zgodne z OpenAI (/v1/chat/completions) ----------
def _ask_openai(conf, system, user_msg):
    url = conf["url"].rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    auth = {"Authorization": f"Bearer {conf['api_key']}"} if conf["api_key"] else {}
    data = _post_json(url, {
        "model": conf["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        conf["max_tokens_param"]: conf["max_tokens"],
    }, _hdrs(conf, auth))

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"Brak odpowiedzi: {str(data)[:300]}")
    choice = choices[0]
    content = (choice.get("message") or {}).get("content") or ""
    if isinstance(content, list):       # część serwerów zwraca listę fragmentów
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    text = _strip_think(content)
    if not text:
        raise RuntimeError(f"Pusta odpowiedź (finish_reason={choice.get('finish_reason')})")
    return text


# ---------- Anthropic (Claude) ----------
def _ask_anthropic(conf, system, user_msg):
    _need_key(conf)
    url = conf["url"].rstrip("/")
    if not url.endswith("/messages"):
        url += "/messages" if url.endswith("/v1") else "/v1/messages"
    data = _post_json(url, {
        "model": conf["model"],
        "max_tokens": conf["max_tokens"],
        "system": system,
        "messages": [{"role": "user", "content": user_msg}],
    }, _hdrs(conf, {"x-api-key": conf["api_key"], "anthropic-version": "2023-06-01"}))

    text = "".join(b.get("text", "") for b in (data.get("content") or []) if b.get("type") == "text").strip()
    if not text:
        raise RuntimeError(f"Pusta odpowiedź (stop_reason={data.get('stop_reason')})")
    return text


# ---------- Google Gemini ----------
def _ask_gemini(conf, system, user_msg):
    _need_key(conf)
    url = conf["url"].rstrip("/")
    if ":generateContent" not in url:   # pełny adres z :generateContent jest używany bez zmian
        url = f"{url}/models/{conf['model']}:generateContent"
    gen_cfg = {"maxOutputTokens": conf["max_tokens"]}
    if conf["thinking_level"]:          # np. "low" - ogranicza "myślenie", które zjada limit tokenów
        gen_cfg["thinkingConfig"] = {"thinkingLevel": conf["thinking_level"]}
    data = _post_json(url, {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user_msg}]}],
        "generationConfig": gen_cfg,
    }, _hdrs(conf, {"x-goog-api-key": conf["api_key"]}))

    cands = data.get("candidates") or []
    if not cands:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "nieznana")
        raise RuntimeError(f"Brak odpowiedzi od Gemini (blockReason={reason})")
    cand = cands[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    if not text:
        raise RuntimeError(f"Pusta odpowiedź (finishReason={cand.get('finishReason')})")
    return text


ASK_FUNCS = {"ollama": _ask_ollama, "openai": _ask_openai, "anthropic": _ask_anthropic, "gemini": _ask_gemini}


def ask_ai(system, user_msg):
    conf = load_config()   # wczytywane przy każdym zapytaniu - zmiany w conf.txt działają od razu
    return ASK_FUNCS[conf["provider"]](conf, system, user_msg)


ROWS = [[(r, c) for c in range(9)] for r in range(9)]
COLS = [[(r, c) for r in range(9)] for c in range(9)]
BOXES = [[(br * 3 + i, bc * 3 + j) for i in range(3) for j in range(3)]
         for br in range(3) for bc in range(3)]
UNITS = ROWS + COLS + BOXES


def find_solutions(grid, limit=1, randomize=False):
    """Backtracking z heurystyką MRV (wybiera pole z najmniejszą liczbą kandydatów).
    Zwraca (liczba_znalezionych_rozwiązań (max limit), pierwsze_rozwiązanie lub None)."""
    g = [row[:] for row in grid]
    rows, cols, boxes = [0] * 9, [0] * 9, [0] * 9
    for r in range(9):
        for c in range(9):
            v = g[r][c]
            if v:
                bit, b = 1 << v, (r // 3) * 3 + c // 3
                if rows[r] & bit or cols[c] & bit or boxes[b] & bit:
                    return 0, None  #
                rows[r] |= bit
                cols[c] |= bit
                boxes[b] |= bit

    found = []
    count = 0

    def rec():
        nonlocal count
        best, best_mask, best_n = None, 0, 10
        for r in range(9):
            for c in range(9):
                if g[r][c] == 0:
                    used = rows[r] | cols[c] | boxes[(r // 3) * 3 + c // 3]
                    mask = ~used & 0x3FE
                    n = bin(mask).count("1")
                    if n < best_n:
                        best, best_mask, best_n = (r, c), mask, n
                        if n == 0:
                            return
        if best is None:
            count += 1
            if not found:
                found.append([row[:] for row in g])
            return
        r, c = best
        b = (r // 3) * 3 + c // 3
        digits = [d for d in range(1, 10) if best_mask >> d & 1]
        if randomize:
            random.shuffle(digits)
        for d in digits:
            bit = 1 << d
            g[r][c] = d
            rows[r] |= bit
            cols[c] |= bit
            boxes[b] |= bit
            rec()
            g[r][c] = 0
            rows[r] ^= bit
            cols[c] ^= bit
            boxes[b] ^= bit
            if count >= limit:
                return

    rec()
    return count, (found[0] if found else None)


def generate_puzzle(givens):
    """Zwraca (plansza_z_lukami) o JEDNYM rozwiązaniu i około `givens` podanych cyfrach."""
    _, full = find_solutions([[0] * 9 for _ in range(9)], 1, randomize=True)
    puzzle = [row[:] for row in full]
    cells = [(r, c) for r in range(9) for c in range(9)]
    random.shuffle(cells)
    filled = 81
    for r, c in cells:
        if filled <= givens:
            break
        backup = puzzle[r][c]
        puzzle[r][c] = 0
        if find_solutions(puzzle, 2)[0] != 1:
            puzzle[r][c] = backup  
        else:
            filled -= 1
    return puzzle


class Board:
    def __init__(self):
        self.clear()

    def clear(self):
        self.grid = [[0] * 9 for _ in range(9)]
        self.fixed = [[False] * 9 for _ in range(9)]   # cyfry "dane" (nie można ich zmienić)
        self.auto = [[False] * 9 for _ in range(9)]    # cyfry wstawione przez solver

    def reset(self):
        """Usuwa wszystko oprócz cyfr początkowych."""
        for r in range(9):
            for c in range(9):
                if not self.fixed[r][c]:
                    self.grid[r][c] = 0
                self.auto[r][c] = False

    def set_cell(self, r, c, v):
        if self.fixed[r][c]:
            return False
        self.grid[r][c] = v
        self.auto[r][c] = False
        return True

    def conflicts(self):
        bad = set()
        for unit in UNITS:
            seen = {}
            for (r, c) in unit:
                v = self.grid[r][c]
                if v:
                    seen.setdefault(v, []).append((r, c))
            for cells in seen.values():
                if len(cells) > 1:
                    bad.update(cells)
        return bad

    def is_full(self):
        return all(v != 0 for row in self.grid for v in row)

    def check_sudoku(self):
        full = list(range(1, 10))
        return all(sorted(self.grid[r][c] for (r, c) in unit) == full for unit in UNITS)

    def solve(self):
        count, sol = find_solutions(self.grid, 1)
        if sol is None:
            return False
        for r in range(9):
            for c in range(9):
                if self.grid[r][c] == 0:
                    self.grid[r][c] = sol[r][c]
                    self.auto[r][c] = True
        return True

    def generate(self, givens):
        puzzle = generate_puzzle(givens)
        self.clear()
        for r in range(9):
            for c in range(9):
                self.grid[r][c] = puzzle[r][c]
                self.fixed[r][c] = puzzle[r][c] != 0

    def as_text(self):
        return "\n".join(" ".join(str(v) if v else "." for v in row) for row in self.grid)


#  INTERFEJS
CELL = 80
BOARD_X, BOARD_Y = 20, 20
BOARD_SIZE = CELL * 9
PANEL_X, PANEL_W = 760, 260
WIDTH, HEIGHT = 1040, 800

C_BG = (255, 255, 255)
C_PANEL = (240, 243, 247)
C_PRIMARY = (20, 140, 130)
C_PRIMARY_HOVER = (30, 168, 156)
C_SECONDARY = (88, 100, 120)
C_SECONDARY_HOVER = (110, 123, 145)
C_DISABLED = (170, 176, 186)
C_TEXT = (40, 46, 58)
C_MUTED = (125, 132, 145)


def wrap_text(text, font, max_w):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for w in para.split(" "):
            test = w if not cur else cur + " " + w
            if font.size(test)[0] <= max_w:
                cur = test
                continue
            if cur:
                lines.append(cur)
            while font.size(w)[0] > max_w and len(w) > 1:
                k = len(w)
                while k > 1 and font.size(w[:k])[0] > max_w:
                    k -= 1
                lines.append(w[:k])
                w = w[k:]
            cur = w
        lines.append(cur)
    return lines


class Button:
    def __init__(self, rect, text, callback, kind="primary", active=None, disabled=None):
        self.rect = pygame.Rect(rect)
        self.text = text
        self.callback = callback
        self.kind = kind
        self.active = active      # funkcja -> bool (dla przełączników)
        self.disabled = disabled  # funkcja -> bool

    def is_disabled(self):
        return bool(self.disabled and self.disabled())

    def draw(self, screen, font, mouse):
        hover = self.rect.collidepoint(mouse)
        if self.is_disabled():
            color = C_DISABLED
        elif self.kind == "primary":
            color = C_PRIMARY_HOVER if hover else C_PRIMARY
        else:
            on = self.active() if self.active else False
            if on:
                color = C_PRIMARY
            else:
                color = C_SECONDARY_HOVER if hover else C_SECONDARY
        pygame.draw.rect(screen, color, self.rect, border_radius=8)
        label = font.render(self.text, True, (255, 255, 255))
        screen.blit(label, label.get_rect(center=self.rect.center))

    def click(self, pos):
        if self.rect.collidepoint(pos) and not self.is_disabled():
            self.callback()
            return True
        return False


class App:
    LEVELS = [("Łatwy", 40), ("Średni", 32), ("Trudny", 26)]

    def __init__(self):
        pygame.init()
        pygame.display.set_caption("Sudoku")
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        self.clock = pygame.time.Clock()
        self.f_cell = pygame.font.Font(None, 58)
        self.f_cell_bold = pygame.font.Font(None, 58)
        self.f_cell_bold.set_bold(True)
        self.f_btn = pygame.font.Font(None, 28)
        self.f_text = pygame.font.Font(None, 24)
        self.f_small = pygame.font.Font(None, 22)

        self.board = Board()
        self.selected = (0, 0)
        self.level = 1
        self.focus = "board"   # "board" albo "input"
        self.input_text = ""
        self.hint_text = ""
        self.hint_loading = False
        self.hint_scroll = 0
        self.status = ("Wygeneruj planszę albo wpisz własną i kliknij „Rozwiąż”.", "info")
        self.running = True

        self.input_rect = pygame.Rect(PANEL_X, 322, PANEL_W, 92)
        self.output_rect = pygame.Rect(PANEL_X, 446, PANEL_W, 294)

        x, w = PANEL_X, PANEL_W
        half = (w - 10) // 2
        third = (w - 2 * 6) // 3
        self.buttons = [
            Button((x, 20, half, 46), "Generuj", self.on_generate),
            Button((x + half + 10, 20, half, 46), "Rozwiąż", self.on_solve),
            Button((x, 76, half, 46), "Reset", self.on_reset, kind="secondary"),
            Button((x + half + 10, 76, half, 46), "Wyczyść", self.on_clear, kind="secondary"),
        ]
        for i, (name, _) in enumerate(self.LEVELS):
            self.buttons.append(Button(
                (x + i * (third + 6), 132, third, 46), name,
                (lambda i=i: self.set_level(i)), kind="secondary",
                active=(lambda i=i: self.level == i)))
        self.buttons += [
            Button((x, 188, w, 46), "Sprawdź", self.on_check, kind="secondary"),
            Button((x, 244, w, 46), "Podpowiedź (AI)", self.on_hint,
                   disabled=lambda: self.hint_loading),
        ]
        self.hint_btn = self.buttons[-1]

    # ---------- akcje przycisków ----------
    def say(self, text, kind="info"):
        self.status = (text, kind)

    def set_level(self, i):
        self.level = i
        self.say(f"Poziom: {self.LEVELS[i][0]}. Kliknij „Generuj”, aby wylosować planszę.")

    def on_generate(self):
        t = time.time()
        self.board.generate(self.LEVELS[self.level][1])
        self.hint_text = ""
        self.say(f"Wygenerowano planszę ({self.LEVELS[self.level][0]}) w {time.time() - t:.2f} s.", "ok")

    def on_solve(self):
        t = time.time()
        if self.board.solve():
            self.say(f"Rozwiązano w {(time.time() - t) * 1000:.1f} ms.", "ok")
        else:
            self.say("Ta plansza nie ma rozwiązania (sprawdź konflikty).", "err")

    def on_reset(self):
        self.board.reset()
        self.say("Usunięto wpisane i rozwiązane cyfry, zostały tylko cyfry początkowe.")

    def on_clear(self):
        self.board.clear()
        self.hint_text = ""
        self.say("Plansza wyczyszczona. Możesz wpisać własne Sudoku.")

    def on_check(self):
        if self.board.conflicts():
            self.say("Są konflikty (czerwone cyfry).", "err")
        elif not self.board.is_full():
            self.say("Na razie bez błędów, ale plansza nie jest pełna.")
        elif self.board.check_sudoku():
            self.say("Brawo! Sudoku rozwiązane poprawnie.", "ok")
        else:
            self.say("Plansza jest pełna, ale niepoprawna.", "err")

    def on_hint(self):
        question = self.input_text.strip() or "Daj mi wskazówkę, jaki ruch wykonać dalej."
        grid = self.board.grid
        note = ""
        _, sol = find_solutions(grid, 1)
        if sol is None:
            note = "UWAGA: aktualna plansza zawiera błędy gracza lub jest nierozwiązywalna. "
            fixed_grid = [[grid[r][c] if self.board.fixed[r][c] else 0 for c in range(9)] for r in range(9)]
            _, sol = find_solutions(fixed_grid, 1)
        msg = f"{note}Aktualna plansza:\n{self.board.as_text()}\n\n"
        if SEND_SOLUTION_TO_AI and sol:
            msg += "Rozwiązanie (tylko dla Ciebie):\n" + "\n".join(
                " ".join(str(v) for v in row) for row in sol) + "\n\n"
        msg += f"Polecenie gracza: {question}"

        self.hint_loading = True
        self.hint_text = ""
        self.hint_scroll = 0
        threading.Thread(target=self._hint_worker, args=(msg,), daemon=True).start()

    def _hint_worker(self, msg):
        try:
            text = ask_ai(build_system_prompt(), msg).strip()
            self.hint_text = text or "(AI zwróciło pustą odpowiedź)"
        except Exception as e:
            self.hint_text = f"Błąd: {e}"
        finally:
            self.hint_loading = False

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False

            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                pos = event.pos
                if any(b.click(pos) for b in self.buttons):
                    continue
                board_rect = pygame.Rect(BOARD_X, BOARD_Y, BOARD_SIZE, BOARD_SIZE)
                if board_rect.collidepoint(pos):
                    self.selected = ((pos[1] - BOARD_Y) // CELL, (pos[0] - BOARD_X) // CELL)
                    self.focus = "board"
                elif self.input_rect.collidepoint(pos):
                    self.focus = "input"
                else:
                    self.focus = "board"

            elif event.type == pygame.MOUSEWHEEL:
                if self.output_rect.collidepoint(pygame.mouse.get_pos()):
                    self.hint_scroll -= event.y * 30
                    self.hint_scroll = max(0, self.hint_scroll)

            elif event.type == pygame.TEXTINPUT and self.focus == "input":
                if len(self.input_text) < 400:
                    self.input_text += event.text

            elif event.type == pygame.KEYDOWN:
                if self.focus == "input":
                    if event.key == pygame.K_BACKSPACE:
                        self.input_text = self.input_text[:-1]
                    elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        if not self.hint_loading:
                            self.on_hint()
                    elif event.key == pygame.K_ESCAPE:
                        self.focus = "board"
                else:
                    self.handle_board_key(event)

    def handle_board_key(self, event):
        r, c = self.selected
        moves = {pygame.K_UP: (-1, 0), pygame.K_DOWN: (1, 0), pygame.K_LEFT: (0, -1), pygame.K_RIGHT: (0, 1)}
        if event.key in moves:
            dr, dc = moves[event.key]
            self.selected = ((r + dr) % 9, (c + dc) % 9)
            return
        digit = None
        if pygame.K_1 <= event.key <= pygame.K_9:
            digit = event.key - pygame.K_0
        elif pygame.K_KP1 <= event.key <= pygame.K_KP9:
            digit = event.key - pygame.K_KP1 + 1
        elif event.key in (pygame.K_0, pygame.K_KP0, pygame.K_BACKSPACE, pygame.K_DELETE):
            digit = 0
        if digit is not None:
            self.board.set_cell(r, c, digit)

    # ---------- rysowanie ----------
    def draw_board(self):
        b = self.board
        sr, sc = self.selected
        sel_val = b.grid[sr][sc]
        bad = b.conflicts()

        for r in range(9):
            for c in range(9):
                rect = pygame.Rect(BOARD_X + c * CELL, BOARD_Y + r * CELL, CELL, CELL)
                color = (255, 255, 255)
                if (r, c) in bad:
                    color = (255, 214, 214)
                if (r, c) == (sr, sc):
                    color = (255, 236, 130)
                elif r == sr or c == sc or (r // 3 == sr // 3 and c // 3 == sc // 3):
                    if color == (255, 255, 255):
                        color = (234, 241, 252)
                elif sel_val and b.grid[r][c] == sel_val and color == (255, 255, 255):
                    color = (205, 225, 250)
                pygame.draw.rect(self.screen, color, rect)

        for i in range(10):
            thick = i % 3 == 0
            col, w = ((30, 30, 30), 3) if thick else ((160, 160, 160), 1)
            p = i * CELL
            pygame.draw.line(self.screen, col, (BOARD_X + p, BOARD_Y), (BOARD_X + p, BOARD_Y + BOARD_SIZE), w)
            pygame.draw.line(self.screen, col, (BOARD_X, BOARD_Y + p), (BOARD_X + BOARD_SIZE, BOARD_Y + p), w)

        for r in range(9):
            for c in range(9):
                v = b.grid[r][c]
                if not v:
                    continue
                if (r, c) in bad:
                    color, font = (210, 30, 30), self.f_cell
                elif b.fixed[r][c]:
                    color, font = (20, 20, 20), self.f_cell_bold
                elif b.auto[r][c]:
                    color, font = (30, 150, 80), self.f_cell
                else:
                    color, font = (30, 90, 200), self.f_cell
                img = font.render(str(v), True, color)
                center = (BOARD_X + c * CELL + CELL // 2, BOARD_Y + r * CELL + CELL // 2)
                self.screen.blit(img, img.get_rect(center=center))

    def draw_panel(self):
        panel = pygame.Rect(PANEL_X - 15, 0, WIDTH - PANEL_X + 15, HEIGHT)
        pygame.draw.rect(self.screen, C_PANEL, panel)
        mouse = pygame.mouse.get_pos()
        for btn in self.buttons:
            btn.draw(self.screen, self.f_btn, mouse)

        # pole tekstowe
        self.screen.blit(self.f_small.render("Twoje pytanie do AI:", True, C_MUTED), (PANEL_X, 302))
        border = C_PRIMARY if self.focus == "input" else (190, 196, 206)
        pygame.draw.rect(self.screen, (255, 255, 255), self.input_rect, border_radius=6)
        pygame.draw.rect(self.screen, border, self.input_rect, 2, border_radius=6)

        lh = self.f_text.get_linesize()
        max_lines = (self.input_rect.h - 12) // lh
        if self.input_text or self.focus == "input":
            caret = "|" if self.focus == "input" and (pygame.time.get_ticks() // 500) % 2 == 0 else " "
            lines = wrap_text(self.input_text + caret, self.f_text, self.input_rect.w - 16)
            color = C_TEXT
        else:
            lines = wrap_text("np. „Gdzie jest najłatwiejszy ruch?”", self.f_text, self.input_rect.w - 16)
            color = C_MUTED
        for i, line in enumerate(lines[-max_lines:]):
            self.screen.blit(self.f_text.render(line, True, color),
                             (self.input_rect.x + 8, self.input_rect.y + 6 + i * lh))

        # odpowiedź AI
        title = "Podpowiedź:" if not self.hint_loading else "Podpowiedź: AI myśli" + "." * (1 + (pygame.time.get_ticks() // 400) % 3)
        self.screen.blit(self.f_small.render(title, True, C_MUTED), (PANEL_X, 426))
        pygame.draw.rect(self.screen, (255, 255, 255), self.output_rect, border_radius=6)
        pygame.draw.rect(self.screen, (190, 196, 206), self.output_rect, 1, border_radius=6)
        lines = wrap_text(self.hint_text, self.f_text, self.output_rect.w - 20)
        total_h = len(lines) * lh
        max_scroll = max(0, total_h - (self.output_rect.h - 16))
        self.hint_scroll = min(self.hint_scroll, max_scroll)
        self.screen.set_clip(self.output_rect.inflate(-4, -4))
        for i, line in enumerate(lines):
            self.screen.blit(self.f_text.render(line, True, C_TEXT),
                             (self.output_rect.x + 10, self.output_rect.y + 8 + i * lh - self.hint_scroll))
        self.screen.set_clip(None)

    def draw_status(self):
        text, kind = self.status
        color = {"ok": (30, 140, 70), "err": (200, 40, 40)}.get(kind, C_TEXT)
        img = self.f_btn.render(text, True, color)
        self.screen.blit(img, (BOARD_X, BOARD_Y + BOARD_SIZE + 18))

    def draw(self):
        self.screen.fill(C_BG)
        self.draw_board()
        self.draw_panel()
        self.draw_status()
        pygame.display.flip()

    def run(self):
        pygame.key.start_text_input()
        while self.running:
            self.handle_events()
            self.draw()
            self.clock.tick(60)
        pygame.quit()


if __name__ == "__main__":
    App().run()