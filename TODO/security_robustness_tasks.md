# Task List: Sicurezza e Robustezza — `pycli`

> Organizzato per area tematica, dal rischio più alto al più basso.
> Ogni task include: file coinvolti, descrizione del problema, approccio di fix, e criteri di completamento.

---

## Stato Verifica — 2026-09-28

> **21 ✅ implementati | 0 ⚠️ parziali | 0 ❌ non implementati**
>
> Tutti i 21 task di sicurezza e robustezza sono implementati e verificati con suite di test completa (92/92 test passati).
> I rilievi della revisione su TASK-SEC-02 (shell_quote su target di redirezione dinamici) e TASK-REPL-02 (gestione esplicita SIGINT in capture=True) sono stati risolti.

---

## Legenda Priorità

| Simbolo | Priorità | Motivazione |
|---|---|---|
| 🔴 | **Critico** | Vulnerabilità di sicurezza o bug di correttezza certo |
| 🟡 | **Alto** | Bug edge-case o degrado silenzioso in produzione |
| 🟢 | **Medio** | Robustezza, DX, comportamento inatteso non critico |

---

## Sezione 1 — Shell Injection (Sicurezza)

---

### TASK-SEC-01 🔴 — Sanitizzazione interpolazioni `{var}` in comandi shell

**File**: [`runtime.py`](file:///c:/Git-Sources/personal/vexvex/vlang/src/pycli/runtime.py), [`transformer.py`](file:///c:/Git-Sources/personal/vexvex/vlang/src/pycli/transformer.py)

**Problema**:
Ogni variabile Python interpolata in un comando via `{var}` viene inserita nella stringa shell senza nessuna sanitizzazione. Un valore contenente metacaratteri shell (`;`, `&&`, `|`, `$()`, backtick) può alterare il comando eseguito.

```python
# Scenario di attacco:
user_input = "innocuo; rm -rf /tmp/important"
$(cat {user_input})
# → run(f"cat {user_input}") → shell esegue: cat innocuo; rm -rf /tmp/important
```

**Fix da implementare**:
1. Nel `Transformer`, quando si produce un f-string con interpolazioni, wrappare ogni `{expr}` in una chiamata a `shell_quote(expr)` nel codice Python generato — **solo per interpolazioni non-splat e non dentro redirection target**.
2. Aggiungere in `runtime.py` una funzione helper `shell_quote(val: Any) -> str` che chiama `shlex.quote(str(val))`.
3. Aggiungere un flag `--unsafe-interpolation` al CLI per disabilitare la sanitizzazione nei casi in cui l'utente sa cosa fa (compatibilità retroattiva).

```python
# runtime.py — nuovo helper
import shlex
def shell_quote(val: Any) -> str:
    """Quote a value for safe shell interpolation."""
    return shlex.quote(str(val))
```

```python
# Codice generato attuale (UNSAFE):
run(f"cat {filename}")

# Codice generato con fix (SAFE):
run(f"cat {shell_quote(filename)}")
```

**Criteri di completamento**:
- [x] `shell_quote()` aggiunta a `runtime.py` e esportata
- [x] Il Transformer inietta `shell_quote(...)` attorno a tutte le `InterpolationNode` scalari
- [x] Test: `$(cat {name})` con `name = "a; echo pwned"` non esegue `echo pwned`
- [x] Flag `--unsafe-interpolation` documentato nel README

---

### TASK-SEC-02 🔴 — Nessuna sanitizzazione nei target di redirection

**File**: [`transformer.py`](file:///c:/Git-Sources/personal/vexvex/vlang/src/pycli/transformer.py), [`parser.py`](file:///c:/Git-Sources/personal/vexvex/vlang/src/pycli/parser.py)

**Problema**:
Il `target` di un `RedirectionNode` viene inserito verbatim nella stringa di comando. Un path con spazi o metacaratteri causa comportamento errato o exploit.

```python
outfile = "file con spazi.txt"
$(git status > {outfile})
# → run("git status > file con spazi.txt") → shell interpreta come 3 token
```

**Fix da implementare**:
1. Nel Transformer, per i `RedirectionNode` con target dinamico (che contiene `{...}`), wrappare il target con `shell_quote()`.
2. Nel `_read_word_or_string` del parser, preservare le quote originali attorno al target di redirection.

**Criteri di completamento**:
- [x] `$(cmd > {path_with_spaces})` produce `run(f"cmd > {shell_quote(path_with_spaces)}")`
- [x] Test con path contenente spazi, virgolette, e semicoloni

> ✅ **Risolto (revisione 2026-09-28)**: il target dinamico di redirection in `_transform_expanded_command()` ([`transformer.py:349-368`](file:///c:/Git-Sources/personal/vexvex/vlang/src/pycli/transformer.py#L349-L368)) viene ora wrappato in `shell_quote()` (o bypassato se `unsafe_interpolation=True`).

---

### TASK-SEC-03 🟡 — Validazione espressioni di interpolazione nel Parser

**File**: [`parser.py`](file:///c:/Git-Sources/personal/vexvex/vlang/src/pycli/parser.py)

**Problema**:
`is_valid_interpolation_expr()` usa `ast.parse()` per validare le espressioni, ma cattura `except Exception` in modo troppo ampio. Un'espressione malformata fallisce silenziosamente e viene trattata come testo letterale, senza warning all'utente.

```python
# parser.py:12-17
def is_valid_interpolation_expr(expr: str) -> bool:
    try:
        p = ast.parse(expr.strip(), mode="eval")
        return not isinstance(p.body, ast.Dict)
    except Exception:   # ← troppo ampio, oscura errori reali
        return False
```

**Fix da implementare**:
1. Separare `SyntaxError` (espressione invalida, ok ignorare) da altri `Exception` (bug imprevisto, loggare).
2. Aggiungere un modo per emettere un `warning` quando un `{...}` viene silenziosamente trattato come testo letterale.

```python
import warnings

def is_valid_interpolation_expr(expr: str) -> bool:
    try:
        p = ast.parse(expr.strip(), mode="eval")
        return not isinstance(p.body, ast.Dict)
    except SyntaxError:
        return False
    except Exception as e:
        warnings.warn(
            f"Unexpected error validating interpolation expr {expr!r}: {e}",
            RuntimeWarning, stacklevel=3
        )
        return False
```

**Criteri di completamento**:
- [x] Solo `SyntaxError` è silente
- [x] Altri errori emettono `RuntimeWarning`
- [x] Test: espressione syntatticamente invalida → `False` silente; altri errori → warning

---

## Sezione 2 — Lexer (Robustezza)

---

### TASK-LEX-01 🔴 — Raw strings Python trattate come codice normale

**File**: [`lexer.py`](file:///c:/Git-Sources/personal/vexvex/vlang/src/pycli/lexer.py)

**Problema**:
Il Lexer non distingue raw string (`r"..."`, `rb"..."`, `r'...'`) da stringhe normali. Il carattere `$` dentro una raw string può triggerare un falso positivo di `CommandExpression`.

```python
# .spy source — raw string contenente pattern simile a $()
import re
pattern = r"$(\d+)"         # ← Il lexer lo legge come $(  \d+ ) → COMMAND_EXPR!
result = re.match(pattern, "42")
```

**Fix da implementare**:
Aggiungere al loop principale del `Lexer.tokenize()` il rilevamento dei prefissi di stringa (`r`, `b`, `f`, `rb`, `br`, `fr`, `rf`) prima del carattere di apertura quotato.

```python
# lexer.py — aggiunta in tokenize(), PRIMA del check su '"' e "'"
# Rilevare prefisso stringa (r, b, f, rb, br, fr, rf, u — case-insensitive)
STRING_PREFIXES = frozenset(["r", "b", "f", "u", "rb", "br", "fr", "rf"])

def _peek_string_prefix(self) -> str:
    """Return any string prefix at current position (e.g. 'r', 'rb'), or ''."""
    for length in (2, 1):
        candidate = self.source[self.pos : self.pos + length].lower()
        next_ch = self.source[self.pos + length : self.pos + length + 1]
        if candidate in STRING_PREFIXES and next_ch in ('"', "'"):
            return candidate
    return ""
```

Quando il prefisso è rilevato:
- Se contiene `r`: trattare il contenuto come raw (nessun escape, nessun `$(` riconoscibile dentro).
- Altrimenti: comportamento attuale.

**Criteri di completamento**:
- [x] `r"$(...)"` non genera token `COMMAND_EXPR`
- [x] `rb"..."`, `fr"..."`, `r'...'` coperti
- [x] Test: script con raw string contenente `$(pattern)` eseguito correttamente

---

### TASK-LEX-02 🟡 — F-string Python con `$(...)` annidato

**File**: [`lexer.py`](file:///c:/Git-Sources/personal/vexvex/vlang/src/pycli/lexer.py)

**Problema**:
Una f-string Python (`f"..."`) che contiene internamente `$(...)` non è una command expression, ma il Lexer potrebbe tokenizzarla erroneamente se la `$` appare dentro `f"..."`.

```python
# Esempio — f-string Python con testo letterale "$("
msg = f"The pattern is $({variable})"   # NON è un comando pycli
```

Attualmente il Lexer entra nel branch stringa e consuma il contenuto come `PYTHON_CODE`, quindi per ora è safe — ma solo perché `$(` viene visto dopo aver già aperto la stringa. Il caso rimane fragile se future modifiche alterano l'ordine dei check.

**Fix da implementare**:
Aggiungere una fase di test esplicito: il check `if ch == "$" and self._peek(1) == "("` deve verificare di **non** essere dentro una stringa Python. Aggiungere flag `in_string: bool` al loop principale.

**Criteri di completamento**:
- [x] Test: f-string Python contenente `$(` non genera `COMMAND_EXPR`
- [x] Test di regressione: il tokenizer continua a riconoscere comandi fuori dalle stringhe

---

### TASK-LEX-03 🟡 — Nessun limite sulla dimensione dell'input

**File**: [`lexer.py`](file:///c:/Git-Sources/personal/vexvex/vlang/src/pycli/lexer.py), [`__init__.py`](file:///c:/Git-Sources/personal/vexvex/vlang/src/pycli/__init__.py)

**Problema**:
Non esiste alcun limite sulla dimensione del file `.spy` in input. Un file molto grande (o generato programmaticamente) può causare consumo di memoria eccessivo durante la tokenizzazione (tutti i token vengono accumulati in lista).

**Fix da implementare**:
1. In `transpile_file()` / `run_file()`, aggiungere un check sulla dimensione del file prima di leggerlo interamente in memoria.
2. Aggiungere una costante configurabile `MAX_SOURCE_SIZE_BYTES = 10 * 1024 * 1024` (10MB default).

```python
# __init__.py — in transpile_file()
MAX_SOURCE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

def transpile_file(input_path: Path, output_path: Path | None = None) -> str:
    size = input_path.stat().st_size
    if size > MAX_SOURCE_SIZE_BYTES:
        raise ValueError(
            f"File {input_path} exceeds maximum allowed size "
            f"({size} bytes > {MAX_SOURCE_SIZE_BYTES} bytes)"
        )
    source = input_path.read_text(encoding="utf-8")
    ...
```

**Criteri di completamento**:
- [x] File > 10MB → errore chiaro prima della lettura
- [x] Costante configurabile via env var `PYCLI_MAX_SOURCE_SIZE`

---

## Sezione 3 — Transformer (Robustezza / Correttezza)

---

### TASK-TRF-01 🟡 — Statement detection fallisce su lambda, ternary, e comprehension

**File**: [`transformer.py`](file:///c:/Git-Sources/personal/vexvex\vlang\src\pycli\transformer.py)

**Problema**:
`_is_statement_context()` usa analisi testuale del codice circostante per determinare se un `$(...)` è statement o expression. Fallisce su costrutti Python validi:

```python
# Lambda: dovrebbe essere expression, ma viene rilevato come statement
fn = lambda: $(git status)

# Ternary: dovrebbe essere expression
x = $(cmd) if condition else fallback

# List comprehension: dovrebbe essere expression
results = [$(process {item}) for item in items]
```

**Fix da implementare**:
1. Usare `ast.parse()` sul blocco di codice Python precedente al token per determinare il contesto in modo semanticamente corretto.
2. Come fallback (se il parse fallisce perché il codice è incompleto), mantenere l'euristica testuale attuale.
3. Aggiungere i casi lambda/ternary/comprehension alla lista di pattern che forzano `is_statement = False`.

```python
# In _is_statement_context(): aggiungere check per pattern expression
EXPRESSION_PATTERNS = [
    r"\blambda\b[^:]*:\s*$",         # lambda ...: $()
    r"\bif\b.*\belse\b\s*$",          # ... if cond else $()
    r"\[.*for\b.*\bin\b.*\]\s*$",     # comprehension
]
for pat in EXPRESSION_PATTERNS:
    if re.search(pat, stripped_before):
        return False
```

**Criteri di completamento**:
- [x] `fn = lambda: $(cmd)` → expression (capture=True)
- [x] `x = $(cmd) if cond else y` → expression
- [x] `[$(cmd {i}) for i in items]` → expression
- [x] Test per ciascun caso aggiunto alla test suite

---

### TASK-TRF-02 🟡 — Mutazione diretta dei token durante la trasformazione

**File**: [`transformer.py`](file:///c:/Git-Sources/personal/vexvex\vlang\src\pycli\transformer.py)

**Problema**:
La gestione di `.tee` e `.input(...)` muta direttamente `tokens[i+1].value` in-place durante l'iterazione. Questo rende difficile il debugging, impedisce trasformazioni multiple sullo stesso token stream, e crea side effects nascosti.

```python
# transformer.py:60 — mutazione in-place
tokens[i + 1].value = val[m_tee.end():]  # ← modifica il token originale
```

**Fix da implementare**:
1. Creare una copia locale del valore del token prima di modificarlo.
2. Introdurre un passo di **pre-processing** separato che identifica i chaining (`.tee`, `.input()`) e li annota come attributi del token `COMMAND_EXPR` precedente, senza mutare il token `PYTHON_CODE`.

```python
# Approccio: annotation pass separato
@dataclass
class CommandToken:
    ...
    is_tee: bool = False
    input_expr: str | None = None
```

**Criteri di completamento**:
- [x] Nessuna mutazione in-place di `token.value` nella fase di transform
- [x] Il token stream originale è immutabile dopo la tokenizzazione
- [x] Test: `.tee` e `.input()` rilevati e annotati correttamente senza effetti collaterali

---

### TASK-TRF-03 🟢 — Nessuna validazione del codice Python generato

**File**: [`transformer.py`](file:///c:/Git-Sources/personal/vexvex\vlang\src\pycli\transformer.py)

**Problema**:
Il codice Python prodotto dal Transformer non viene mai validato sintatticamente prima di essere restituito. Un bug nel Transformer potrebbe produrre Python non valido che fallisce solo a runtime con un `SyntaxError` confuso.

**Fix da implementare**:
Aggiungere una fase di validazione opzionale (abilitabile con `--validate` o in modalità debug) che esegue `ast.parse()` sul codice generato.

```python
# transformer.py — in Transformer.transform()
def transform(self, source: str, validate: bool = False) -> str:
    ...
    result_code = "".join(output_chunks)
    if validate:
        try:
            ast.parse(result_code)
        except SyntaxError as e:
            raise TranspilerError(
                f"Transpiler produced invalid Python: {e}"
            ) from e
    return result_code
```

**Criteri di completamento**:
- [x] Flag `validate=True` disponibile in `transpile()`
- [x] Flag `--validate` esposto nel CLI (`spy run --validate script.spy`)
- [x] In modalità `--validate`, errore chiaro con riferimento alla riga del `.py` generato

---

## Sezione 4 — Runtime (Robustezza / Sicurezza)

---

### TASK-RUN-01 🔴 — `capture=False` non è streaming reale

**File**: [`runtime.py`](file:///c:/Git-Sources/personal/vexvex\vlang\src\pycli\runtime.py)

**Problema**:
Quando `capture=False`, `run()` usa `subprocess.run(..., capture_output=True)` che accumula tutto l'output in memoria, poi lo scrive su stdout/stderr alla fine. Per comandi a lunga esecuzione (`docker build`, `npm install`, `cargo build`) l'output appare tutto alla fine, dando l'impressione di un blocco.

```python
# runtime.py:316-333 — non è vero streaming
proc = subprocess.run(command, ..., capture_output=True, ...)  # ← buffering
if not capture:
    sys.stdout.write(proc.stdout)  # ← output appare tutto insieme alla fine
```

**Fix da implementare**:
Per `capture=False` **e** `tee=False`, usare `subprocess.Popen` con `stdout=None` e `stderr=None` (passthrough diretto al terminale del processo padre), eliminando il buffer intermedio.

```python
# runtime.py — nuovo branch per passthrough diretto
if not capture and not tee and input_text is None:
    proc = subprocess.Popen(
        command,
        shell=shell,
        stdout=None,   # ← ereditato dal processo padre → streaming nativo
        stderr=None,
        cwd=cwd_str,
        env=env_dict,
    )
    proc.wait()
    duration = time.perf_counter() - start
    return CommandResult(
        command=cmd_str, stdout="", stderr="",
        exit_code=proc.returncode or 0, duration=duration
    )
```

**Criteri di completamento**:
- [x] `$(docker build .)` mostra output in tempo reale riga per riga
- [x] Il comportamento è corretto anche con `check=True` e `suppress_errors=True`
- [x] Test: comando che produce output lentamente (es. `ping`) appare incrementalmente

---

### TASK-RUN-02 🟡 — Nessun timeout su `run()` e `async_run()`

**File**: [`runtime.py`](file:///c:/Git-Sources/personal/vexvex\vlang\src\pycli\runtime.py)

**Problema**:
`run()` e `async_run()` non hanno nessun meccanismo di timeout. Un comando che si blocca indefinitamente (es. processo che attende stdin, servizio non raggiungibile) blocca l'intero script senza possibilità di recupero.

**Fix da implementare**:
1. Aggiungere parametro `timeout: float | None = None` a `run()`, `run_expanded()`, `run_bg()`, `async_run()`.
2. Per `run()`: passare `timeout` a `subprocess.run()` o gestirlo manualmente in `Popen`.
3. Per `async_run()`: usare `asyncio.wait_for()`.
4. Quando scade il timeout, sollevare `CommandTimeoutError(CommandResult)`.

```python
# runtime.py — nuova eccezione
class CommandTimeoutError(CommandError):
    """Raised when a command exceeds its configured timeout."""

# run() signature aggiornata
def run(command, *, timeout: float | None = None, ...) -> CommandResult:
    ...
    try:
        proc = subprocess.run(..., timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise CommandTimeoutError(
            CommandResult(command=cmd_str, ..., exit_code=-1, ...)
        ) from e
```

**Criteri di completamento**:
- [x] `run("sleep 60", timeout=2)` → `CommandTimeoutError` dopo 2 secondi
- [x] `timeout` aggiunto a `run`, `run_expanded`, `async_run`
- [x] Sintassi `$(cmd) [timeout=5]` o nota nel README su come usarlo programmaticamente
- [x] Processo figlio terminato correttamente su timeout (no zombie)

---

### TASK-RUN-03 🟡 — Encoding hardcoded `utf-8` senza gestione fallback

**File**: [`runtime.py`](file:///c:/Git-Sources/personal/vexvex\vlang\src\pycli\runtime.py)

**Problema**:
`subprocess.run(..., text=True)` usa l'encoding del sistema (`locale.getpreferredencoding()`). Ma `async_run()` usa `.decode("utf-8", errors="replace")` hardcoded. Su sistemi Windows con locale non-UTF-8, questo può causare output corrotto o eccezioni.

```python
# runtime.py:479-480 — encoding hardcoded
stdout = stdout_b.decode("utf-8", errors="replace")
stderr = stderr_b.decode("utf-8", errors="replace")
```

**Fix da implementare**:
1. Aggiungere parametro `encoding: str = "utf-8"` a `run()`, `run_expanded()`, `async_run()`.
2. In `async_run()`, usare l'encoding configurato invece di hardcoded `"utf-8"`.
3. Il default rimane `"utf-8"` ma è sovrascrivibile.

**Criteri di completamento**:
- [x] `encoding` è parametro di tutte le funzioni run
- [x] Su Windows con `cmd.exe`, output in CP850/CP1252 viene decodificato correttamente se specificato
- [x] Test con output non-ASCII

---

### TASK-RUN-04 🟡 — `BackgroundJob.wait()` non gestisce processi già terminati prima di `communicate()`

**File**: [`runtime.py`](file:///c:/Git-Sources/personal/vexvex\vlang\src\pycli\runtime.py)

**Problema**:
Se `BackgroundJob.wait()` viene chiamato dopo che il processo è già terminato, `proc.communicate()` potrebbe ricevere `stdout=None` / `stderr=None` se i pipe sono stati chiusi.

```python
# runtime.py:204
stdout, stderr = self.proc.communicate(timeout=timeout)
# stdout può essere None se il processo è già finito e i pipe sono stati letti
```

Attualmente c'è `stdout or ""` ma il problema più sottile è che `communicate()` su un processo già terminato con pipe chiusi può alzare `BrokenPipeError` in alcuni sistemi.

**Fix da implementare**:
Aggiungere un check esplicito via `proc.poll()` prima di chiamare `communicate()` e gestire il caso già-terminato separatamente.

```python
def wait(self, timeout: float | None = None) -> CommandResult:
    if self._result is not None:
        return self._result
    try:
        stdout, stderr = self.proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        self.proc.kill()
        stdout, stderr = self.proc.communicate()
        raise CommandTimeoutError(...)
    except BrokenPipeError:
        stdout, stderr = "", ""
    ...
```

**Criteri di completamento**:
- [x] `job.wait()` chiamato dopo terminazione del processo → nessuna eccezione
- [x] Timeout su `BackgroundJob.wait(timeout=...)` → processo terminato e `CommandTimeoutError`
- [x] Test: `job.wait()` chiamato due volte → stesso risultato (idempotente) ✓ già ok

---

### TASK-RUN-05 🟢 — Nessun limite su `stdout`/`stderr` catturati

**File**: [`runtime.py`](file:///c:/Git-Sources/personal/vexvex\vlang\src\pycli\runtime.py)

**Problema**:
`run()` cattura l'intero stdout/stderr in memoria. Un comando che produce output molto grande (es. `cat /dev/urandom | head -c 1GB`) può esaurire la RAM prima che il processo termini.

**Fix da implementare**:
Aggiungere parametro `max_output_bytes: int | None = None`. Quando impostato, troncare stdout/stderr oltre quella soglia e settare un flag `CommandResult.truncated = True`.

**Criteri di completamento**:
- [x] `run("cmd", max_output_bytes=10_000_000)` → tronca a 10MB
- [x] `result.truncated` è `True` quando l'output è stato troncato
- [x] La troncatura non impedisce la corretta terminazione del processo

---

## Sezione 5 — Importer / `exec()` (Sicurezza)

---

### TASK-IMP-01 🔴 — `exec()` con namespace globale non isolato

**File**: [`__init__.py`](file:///c:/Git-Sources/personal/vexvex\vlang\src\pycli\__init__.py), [`importer.py`](file:///c:/Git-Sources/personal/vexvex\vlang\src\pycli\importer.py)

**Problema**:
`run_file()` esegue il codice transpilato con `exec(compiled, global_namespace)` dove `global_namespace` contiene solo `cd`, `env`, e `wait_all`. Questo significa che il codice eseguito ha accesso completo a tutto il runtime Python (via `__builtins__`) incluso `__import__`, `open`, `os`, `sys`, ecc. Non è una sandbox, ma il namespace lascia aperta la porta a confusioni.

Più critico: `importer.py` usa `exec(compiled, module.__dict__)` dove `module.__dict__` contiene già `__builtins__` completo.

**Non è una sandbox** — ma bisogna essere espliciti e aggiungere protezioni basali:

**Fix da implementare**:
1. Documentare esplicitamente che `pycli` non è una sandbox e che i file `.spy` vengono eseguiti con i privilegi dell'utente corrente.
2. Aggiungere al CLI un warning visibile quando si esegue uno script ricevuto da fonti non fidate: `spy run --warn-external script.spy`.
3. Verificare che il path del file `.spy` sia assoluto e non contenga path traversal (`../../../etc/...`).

```python
# __init__.py — validazione path
def run_file(script_path: Path, ...) -> int:
    resolved = script_path.resolve()
    # Blocca path traversal simbolici fuori dalla CWD in modalità sicura
    if not resolved.exists():
        sys.stderr.write(f"Error: script not found: {script_path}\n")
        return 1
    ...
```

**Criteri di completamento**:
- [x] Warning documentato nel README su esecuzione di script non fidati
- [x] Path traversal validato: script deve esistere come file reale
- [x] `importer.py` verifica che il path del modulo sia dentro una delle directory in `sys.path`

---

### TASK-IMP-02 🟡 — Import hook inserito in posizione 0 di `sys.meta_path` — potenziale hijacking

**File**: [`importer.py`](file:///c:/Git-Sources/personal/vexvex\vlang\src\pycli\importer.py)

**Problema**:
`SpyFinder` viene inserito in `sys.meta_path[0]`, prima di tutti gli altri finder. Questo significa che un modulo `.spy` può **ombreggiare** un modulo Python standard se ha lo stesso nome.

```python
# Un file chiamato "json.spy" nel CWD può oscurare il modulo stdlib json!
```

**Fix da implementare**:
1. `SpyFinder.find_spec()` deve verificare che il nome del modulo non corrisponda a un modulo stdlib o a un modulo già presente in `sys.modules`.
2. Aggiungere una blacklist di nomi riservati: tutti i moduli della stdlib (`sys.stdlib_module_names` in Python 3.10+).

```python
import sys

STDLIB_NAMES = sys.stdlib_module_names  # Python 3.10+

class SpyFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        top_level = fullname.split(".")[0]
        if top_level in STDLIB_NAMES:
            return None  # Non oscurare mai la stdlib
        if fullname in sys.modules:
            return None  # Già caricato, non interferire
        ...
```

**Criteri di completamento**:
- [x] `import json` con `json.spy` presente → carica il modulo stdlib, non il `.spy`
- [x] `import os` non viene mai hijacked da un `os.spy`
- [x] Test di non-regressione: `import devops_utils` con `devops_utils.spy` → carica correttamente il `.spy`

---

### TASK-IMP-03 🟢 — Nessun caching del codice transpilato

**File**: [`importer.py`](file:///c:/Git-Sources/personal/vexvex\vlang\src\pycli\importer.py)

**Problema**:
Ogni volta che un modulo `.spy` viene importato (o re-importato dopo `importlib.reload()`), viene letto da disco e ritranspilato. In progetti con molti moduli `.spy` o con `importlib.reload()` frequente, questo è un overhead inutile.

**Fix da implementare**:
1. Aggiungere caching del bytecode compilato (analoga alla `__pycache__`): salvare il `.pyc` nella directory `__pycache__` con hash del sorgente come chiave di validazione.
2. Usare `importlib.util.source_hash()` o `hashlib.md5(source.encode())` per invalidare la cache quando il file cambia.

```python
# importer.py — con cache
import hashlib, importlib.util

def _get_cached_code(spy_path: Path, py_source: str):
    cache_dir = spy_path.parent / "__pycache__"
    src_hash = hashlib.md5(py_source.encode()).hexdigest()[:8]
    cache_file = cache_dir / f"{spy_path.stem}.{src_hash}.pyc"
    if cache_file.exists():
        return marshal.loads(cache_file.read_bytes()[8:])  # skip magic+hash header
    code = compile(py_source, str(spy_path), "exec")
    cache_dir.mkdir(exist_ok=True)
    cache_file.write_bytes(b'\x00' * 8 + marshal.dumps(code))
    return code
```

**Criteri di completamento**:
- [x] Secondo import dello stesso `.spy` usa il bytecode cached
- [x] Cache invalidata quando il file `.spy` cambia (hash o mtime)
- [x] Cache ignorata silenziosamente se non scrivibile (permessi, read-only FS)

---

## Sezione 6 — REPL (Sicurezza / Robustezza)

---

### TASK-REPL-01 🟡 — Eccezioni di transpilazione oscurate nel REPL

**File**: [`repl.py`](file:///c:/Git-Sources/personal/vexvex\vlang\src\pycli\repl.py)

**Problema**:
In `SpyConsole.runsource()`, qualsiasi eccezione durante `transpile(source)` viene catturata genericamente e delegata a `self.showsyntaxerror(filename)`, che mostra un errore di sintassi Python (non del `.spy`). L'utente vede un messaggio fuorviante.

```python
# repl.py:50-53
try:
    transpiled = transpile(source)
except Exception:          # ← troppo ampio
    self.showsyntaxerror(filename)  # ← messaggio fuorviante
    return False
```

**Fix da implementare**:
1. Separare `LexerError` e `ParseError` (errori `.spy` attesi) da altri `Exception` (bug nel transpiler).
2. Per errori `.spy`: mostrare messaggio con riga e colonna del sorgente originale.
3. Per bug interni: mostrare traceback completo per facilitare il debug.

```python
from pycli.lexer import LexerError
from pycli.parser import ParseError

def runsource(self, source, filename="<input>", symbol="single") -> bool:
    try:
        transpiled = transpile(source)
    except (LexerError, ParseError) as e:
        sys.stderr.write(f"  spy syntax error: {e}\n")
        return False
    except Exception as e:
        sys.stderr.write(f"  [pycli internal error] {type(e).__name__}: {e}\n")
        return False
    return super().runsource(transpiled, filename=filename, symbol=symbol)
```

**Criteri di completamento**:
- [x] `$(git status` (parentesi non chiusa) → `LexerError` con riga/colonna
- [x] Bug interno nel transpiler → traceback completo su stderr
- [x] Nessun crash del REPL in entrambi i casi

---

### TASK-REPL-02 🟢 — Nessuna protezione da loop infiniti nel REPL

**File**: [`repl.py`](file:///c:/Git-Sources/personal/vexvex\vlang\src\pycli\repl.py)

**Problema**:
Il REPL non ha nessun meccanismo di interruzione per comandi che si bloccano indefinitamente (es. `$(sleep 9999)` o loop Python infiniti). `KeyboardInterrupt` (Ctrl+C) dovrebbe interrompere il comando corrente senza chiudere il REPL.

**Fix da implementare**:
Verificare che `code.InteractiveConsole.interact()` gestisca già `KeyboardInterrupt` correttamente (lo fa per i loop Python puri). Per i comandi shell, assicurarsi che il segnale SIGINT venga propagato al processo figlio.

```python
# runtime.py — in run() con capture=False, propagare SIGINT al figlio
import signal

def run(...):
    ...
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.send_signal(signal.SIGINT)
        proc.wait()
        raise
```

**Criteri di completamento**:
- [x] Ctrl+C durante `$(sleep 999)` nel REPL → interrompe il comando, ritorna al prompt
- [x] Il REPL non si chiude su Ctrl+C (comportamento standard Python)
- [x] Test su Linux e Windows

> ✅ **Risolto (revisione 2026-09-28)**: SIGINT / `KeyboardInterrupt` è ora gestito esplicitamente in tutti i branch di esecuzione ([`runtime.py`](file:///c:/Git-Sources/personal/vexvex/vlang/src/pycli/runtime.py)): `capture=False`, `tee=True`, `capture=True`, e `BackgroundJob.wait()`, con terminazione pulita dell'albero dei processi.

---

## Sezione 7 — Errori e Observability

---

### TASK-OBS-01 🟡 — Traceback punta al `.py` generato, non al `.spy` sorgente

**File**: [`__init__.py`](file:///c:/Git-Sources/personal/vexvex\vlang\src\pycli\__init__.py), [`transformer.py`](file:///c:/Git-Sources/personal/vexvex\vlang\src\pycli\transformer.py)

**Problema**:
Quando uno script `.spy` lancia un'eccezione a runtime, il traceback mostra i numeri di riga del file `.py` transpilato (in memoria), non del file `.spy` sorgente. L'utente deve decifrare quale riga `.spy` corrisponde alla riga `.py`.

**Fix da implementare**:
1. Il Transformer deve produrre una **source map**: un dizionario `{linea_py: linea_spy}`.
2. In `run_file()`, installare un `sys.excepthook` personalizzato che riscrive i traceback usando la source map.
3. In alternativa semplificata: aggiungere commenti `# spy:line=N` nel codice generato e riscriverli nei traceback.

```python
# Approccio semplificato: commenti di riga
# Codice generato con annotazioni:
run("git status", capture=False)  # spy:5
branch = run("git branch --show-current")  # spy:7
```

```python
# __init__.py — excepthook personalizzato
import traceback, re

SPY_LINE_RE = re.compile(r"# spy:(\d+)")

def _spy_excepthook(exc_type, exc_value, exc_tb):
    lines = traceback.format_tb(exc_tb)
    # Rimpiazza numeri di riga .py con quelli .spy dove possibile
    ...
    sys.__excepthook__(exc_type, exc_value, exc_tb)
```

**Criteri di completamento**:
- [x] Errore a riga 10 del `.spy` → traceback mostra riga 10 del `.spy`
- [x] File `.spy` e numero di riga originale visibili nel traceback
- [x] Funziona sia per errori Python che per `CommandError`

---

### TASK-OBS-02 🟢 — `CommandError` non include il comando esatto con argomenti espansi

**File**: [`runtime.py`](file:///c:/Git-Sources/personal/vexvex\vlang\src\pycli\runtime.py)

**Problema**:
`CommandError.__str__()` include `result.command` (la stringa passata alla shell), ma quando si usa `run_expanded()`, `result.command` è costruito con `" ".join(arg_strings)` — le liste vengono appiattite ma il comando originale `.spy` (es. `$(rm {*files})`) non è visibile.

**Fix da implementare**:
Aggiungere un campo opzionale `original_expression: str | None` a `CommandResult` che il Transformer popola con la stringa `.spy` originale tramite un meccanismo di annotazione a runtime (es. thread-local o context var).

**Criteri di completamento**:
- [x] `CommandError` mostra sia il comando eseguito che l'espressione `.spy` originale
- [x] `result.original_expression` disponibile per ispezione

---

## Riepilogo Task per Priorità

> Stato aggiornato al 2026-09-28. Legenda: ✅ implementato | ⚠️ parziale | ❌ non implementato

| ID | Titolo | Priorità | Area | Stato |
|---|---|---|---|---|
| TASK-SEC-01 | Sanitizzazione interpolazioni shell | 🔴 | Sicurezza | ✅ |
| TASK-SEC-02 | Sanitizzazione target redirection | 🔴 | Sicurezza | ✅ |
| TASK-LEX-01 | Raw strings Python | 🔴 | Lexer | ✅ |
| TASK-RUN-01 | Streaming reale `capture=False` | 🔴 | Runtime | ✅ |
| TASK-IMP-01 | exec() namespace + path traversal | 🔴 | Importer | ✅ |
| TASK-SEC-03 | Validazione interpolazioni parser | 🟡 | Sicurezza | ✅ |
| TASK-LEX-02 | F-string con `$(...)` annidato | 🟡 | Lexer | ✅ |
| TASK-LEX-03 | Limite dimensione input | 🟡 | Lexer | ✅ |
| TASK-TRF-01 | Statement detection lambda/ternary | 🟡 | Transformer | ✅ |
| TASK-TRF-02 | Mutazione token in-place | 🟡 | Transformer | ✅ |
| TASK-RUN-02 | Timeout su `run()` e `async_run()` | 🟡 | Runtime | ✅ |
| TASK-RUN-03 | Encoding hardcoded utf-8 | 🟡 | Runtime | ✅ |
| TASK-RUN-04 | `BackgroundJob.wait()` su proc terminato | 🟡 | Runtime | ✅ |
| TASK-IMP-02 | Import hook hijacking stdlib | 🟡 | Importer | ✅ |
| TASK-REPL-01 | Eccezioni transpiler nel REPL | 🟡 | REPL | ✅ |
| TASK-OBS-01 | Traceback su file sorgente .spy | 🟡 | Observability | ✅ |
| TASK-TRF-03 | Validazione Python generato | 🟢 | Transformer | ✅ |
| TASK-RUN-05 | Limite stdout/stderr catturati | 🟢 | Runtime | ✅ |
| TASK-IMP-03 | Caching bytecode transpilato | 🟢 | Importer | ✅ |
| TASK-REPL-02 | Protezione loop infiniti REPL | 🟢 | REPL | ✅ |
| TASK-OBS-02 | CommandError espressione originale | 🟢 | Observability | ✅ |
