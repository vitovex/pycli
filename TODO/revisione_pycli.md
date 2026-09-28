# Revisione Complessiva: `pycli` / `spy`

## Indice
1. [Sintesi Esecutiva](#1-sintesi-esecutiva)
2. [Analisi Concettuale](#2-analisi-concettuale)
3. [Analisi Tecnica](#3-analisi-tecnica)
4. [Alternative Esistenti](#4-alternative-esistenti)
5. [Posizionamento e Differenziazione](#5-posizionamento-e-differenziazione)
6. [Punti di Forza](#6-punti-di-forza)
7. [Criticità e Rischi](#7-criticità-e-rischi)
8. [Raccomandazioni](#8-raccomandazioni)

---

## 1. Sintesi Esecutiva

`pycli` è un transpiler **source-to-source** che converte una sintassi Python estesa (`.spy`) in Python puro, aggiungendo espressioni di comando shell native tramite `$(...)`. Il progetto è **concettualmente valido**, ben eseguito tecnicamente per la sua fase attuale, e si posiziona in una nicchia reale — ma deve confrontarsi con alternative già mature e con un ecosistema in rapida evoluzione.

**Verdetto: Progetto solido per uso personale/team. Per diventare un tool pubblico di riferimento, servono differenziazione più netta e investimento in DX (Developer Experience).**

---

## 2. Analisi Concettuale

### 2.1 Il Problema che Risolve

Lo scripting DevOps vive in uno spazio scomodo:

| Strumento | Problema |
|---|---|
| **Bash/PowerShell** | Sintassi arcaica, tipizzazione assente, scarso supporto OOP, debugging difficile |
| **Python puro** | `subprocess.run(...)` verboso, composizione di comandi scomoda |
| **Makefile** | Non programmabile, portabilità limitata |
| **Ansible/Terraform** | Overhead infrastrutturale eccessivo per scripting ad-hoc |

`pycli` si inserisce qui: **"Python con la fluidità di una shell"**. L'idea è genuina e copre un bisogno reale.

### 2.2 Scelte di Design

#### ✅ Punti di Design Validi

- **`$(...)` come operatore**: Il simbolo è intuitivo per chi viene da Bash/PowerShell. Non introduce nuove keyword, il che mantiene la compatibilità con i parser Python e gli IDE.
- **Transpilation vs interpretazione**: Scelta corretta. Evita il lock-in su un runtime custom e permette debugging diretto del Python generato. Il codice transpilato è leggibile.
- **`CommandResult` come oggetto ricco**: `.json`, `.lines`, `.text`, `.tee`, truthiness — è un'API ben pensata.
- **Context managers `cd()` e `env()`**: Idiomatici Python, sicuri (con restore garantito da `finally`), concettualmente superiori ai bash subshell.
- **Modular `.spy` imports**: Uso di importlib hook per compilare on-the-fly — soluzione elegante.
- **Zero dipendenze runtime**: `pyproject.toml` con `dependencies = []` — ottima scelta per portabilità.

#### ⚠️ Punti di Design Discutibili

- **Namespace `$` nel codice Python**: `$(...)` entra in conflitto concettuale con f-string interpolation `{...}`. La combinazione `$(echo "{name}")` richiede comprensione precisa di quale livello processa cosa.
- **Statement vs Expression detection euristico**: La logica in `_is_statement_context()` si basa su ispezione testuale del codice circostante, non su un AST Python. Fragile in casi edge (decoratori, lambda, comprehensions).
- **Modifica inline dei token durante la trasformazione**: In `transformer.py`, `tokens[i + 1].value` viene mutato direttamente per gestire `.tee` e `.input(...)`. Approccio funzionale ma architetturalmente poco pulito.
- **Shell injection latente**: L'interpolazione `{var}` in comandi shell non sanitizza l'input. `name = "file; rm -rf /"` + `$(cat {name})` è un problema reale.

---

## 3. Analisi Tecnica

### 3.1 Pipeline di Trasformazione

```
.spy source
  └─ Lexer         → Token stream [PYTHON_CODE | COMMAND_EXPR]
       └─ Parser   → AST (CommandExpressionNode, Pipeline, WordNode, ...)
            └─ Transformer → Python source string
                  └─ Auto-import injection
```

Il design è corretto e pulito. La separazione Lexer/Parser/Transformer è rispettata.

### 3.2 Lexer ([lexer.py](file:///c:/Git-Sources/personal/vexvex/vlang/src/pycli/lexer.py))

**Qualità: Buona.** Gestisce correttamente:
- Triple quote, escape sequences
- Profondità parentesi per `$(...)`
- Interpolazioni `{...}` nested
- Modificatori `!`, `?`, `&` post-closing

**Problema noto**: Il lexer non gestisce raw strings (`r"..."`, `rb"..."`) — un `r"$(git status)"` potrebbe essere mal interpretato se la stringa contiene `$(`.

```python
# Bug potenziale: questa stringa raw contiene $( ma non è un comando
pattern = r"$(\d+)"
```

Il check `if ch == "$" and self._peek(1) == "("` non guarda se siamo dentro una stringa Python.

> [!CAUTION]
> Il bug delle raw-string è reale: il lexer potrebbe tokenizzare falsi positivi dentro `r"..."`.

### 3.3 Parser ([parser.py](file:///c:/Git-Sources/personal/vexvex/vlang/src/pycli/parser.py))

Gestisce il command body in: `WordNode`, `StringNode`, `InterpolationNode`, `SplatNode`, `SubcommandNode`, `RedirectionNode`. È un parser **manuale a discesa ricorsiva** — appropriato per la grammatica semplice.

### 3.4 Transformer ([transformer.py](file:///c:/Git-Sources/personal/vexvex/vlang/src/pycli/transformer.py))

La funzione `_is_statement_context()` è la parte più rischiosa: usa analisi lessicale testuale per determinare se un `$(...)` è espressione o statement. I casi coperti sono buoni, ma mancano:

- Lambda: `fn = lambda: $(git status)` → `fn = lambda: run("git status", capture=False)` (errato, dovrebbe catturare)
- Ternary: `x = $(cmd) if cond else y` → statement detection errata

### 3.5 Runtime ([runtime.py](file:///c:/Git-Sources/personal/vexvex/vlang/src/pycli/runtime.py))

**Qualità: Eccellente.** Il runtime è il modulo più maturo del progetto:
- `run()`: gestione completa di capture/tee/input/env/cwd
- `run_bg()`: `subprocess.Popen` non bloccante
- `async_run()`: asyncio nativo con `create_subprocess_shell`
- `cd()` / `env()`: context managers corretti con `finally` garantito
- `DynamicObj`: dot-access su JSON con fallback `__getitem__`
- `CommandResult.__bool__`: truthiness su `exit_code == 0` — semantica chiara

**Problema**: `run()` con `capture=False` esegue il comando, cattura l'output, poi lo scrive su stdout/stderr. Non è streaming reale — per comandi lunghi l'output appare tutto alla fine. Il `.tee` usa thread separati (corretto), ma il `capture=False` standard no.

```python
# runtime.py:327-333 — non è vero streaming, è buffering+replay
if not capture:
    if proc.stdout:
        sys.stdout.write(proc.stdout)  # appare tutto alla fine
```

### 3.6 Test Coverage

8 file di test. La copertura appare buona per le feature di base. Mancano test per:
- Edge case del lexer (raw strings, f-strings contenenti `$(`)
- Statement detection su lambda/ternary
- Shell injection scenarios

---

## 4. Alternative Esistenti

### 4.1 Confronto Diretto

| Tool | Approccio | Sintassi | Maturità | Python-native |
|---|---|---|---|---|
| **`pycli`** | Transpiler | `$(git status)` | Prototype | ✅ Superset |
| **`sh`** (amoffat/sh) | Lib Python | `sh.git("status")` | Maturo (⭐7k) | ✅ Import |
| **`plumbum`** | Lib Python | `git["status"]()` | Maturo (⭐2.7k) | ✅ Import |
| **`xonsh`** | Shell + Python | `git status` (raw) | Maturo (⭐8k) | ✅ Full interop |
| **`Nushell`** | Nuova shell | Pipeline tipizzate | Crescente | ❌ Diverso runtime |
| **`doit`** | Task runner | Config Python | Maturo | ✅ |
| **`Invoke`** | Task runner | Decorator API | Maturo (⭐4k) | ✅ |
| **`subprocess-tee`** | Utility | Thin wrapper | Piccolo | ✅ |
| **`asyncify` / asyncio** | Pattern | nativo | Standard | ✅ |

### 4.2 Analisi delle Alternative Più Rilevanti

#### `xonsh` — Il Concorrente Principale

**[xonsh](https://xon.sh)** è la soluzione più simile concettualmente:
- Shell interattiva che è un **superset di Python**
- Permette `git status` direttamente (senza `$(...)`)
- Ha un REPL maturo, gestione PATH, completamento avanzato
- Attivo (⭐8k+, sviluppo continuo)

La differenza chiave: `xonsh` è una **shell** che incorpora Python. `pycli` è un **transpiler** che aggiunge shell a Python. L'angolazione è diversa: `pycli` produce `.py` standard eseguibili da CPython, `xonsh` richiede il proprio interprete.

#### `sh` — La Libreria Standard de Facto

```python
from sh import git, kubectl
branch = git("branch", "--show-current").strip()
pods = kubectl("get", "pods", "-o", "json")
```

- Zero overhead di transpilazione
- Lazy-loading dei comandi come attributi del modulo
- 7k+ stelle, production-ready
- **Svantaggio**: sintassi più verbosa, pipeline meno naturali

#### `plumbum` — Orchestrazione Avanzata

```python
from plumbum import local
git = local["git"]
chain = git["status"] | local["grep"]["modified"]
output = chain()
```

- Pipeline con `|` native
- Remote execution via SSH
- Modifica environment con `local.env`
- **Svantaggio**: API non immediatamente intuitiva

#### `Invoke` — Task Runner

Se il caso d'uso principale è scripting DevOps strutturato:
```python
from invoke import task

@task
def build(ctx):
    ctx.run("docker build -t app:latest .")
    ctx.run("docker push app:latest")
```

Molto usato come sostituto di Makefile. Simile a `pycli` per lo use case ma richiede struttura esplicita.

---

## 5. Posizionamento e Differenziazione

### Dove `pycli` È Unico

1. **Sintassi `$(...)` in Python**: Nessun altro tool usa questa notazione come transpiler. È immediatamente comprensibile per utenti bash/PowerShell.
2. **Output `.py` standard**: Il codice generato è Python puro, debuggabile, committabile, eseguibile senza `pycli` installato (se si importa il runtime).
3. **Modifiers post-fix** (`!`, `?`, `&`): Concisi e leggibili. Nessuna alternativa li ha in questa forma.
4. **Splat `{*list}`**: Espansione di liste in argomenti shell — non comune nelle alternative.
5. **`.json` dot-access**: Integrazione natuale JSON→DynamicObj — solo `pycli` lo fa inline.

### Dove Le Alternative Vincono

1. **Maturità**: `xonsh`, `sh`, `plumbum` hanno anni di battle-testing e community.
2. **Ecosistema IDE**: `xonsh` ha plugin per molti editor. `pycli` ha solo definizioni TextMate/UDL.
3. **Debugging**: Con `xonsh` o `sh` non c'è un layer di transpilazione — i traceback puntano al codice originale. Con `pycli` il traceback punta al `.py` generato, non al `.spy`.
4. **Cross-platform**: `sh` non funziona su Windows (usa `wexpect`), ma `plumbum` e `xonsh` sì. `pycli` usa `subprocess` — funziona ovunque ma ha dipendenza dalla shell di sistema.

---

## 6. Punti di Forza

### ✅ Tecnici
- Pipeline Lexer→Parser→Transformer ben separata
- Runtime `run()` completo e ben documentato
- Zero dipendenze esterne (solo stdlib)
- `DynamicObj` elegante per JSON navigation
- Context managers `cd()`/`env()` robusti
- Test suite presente e strutturata

### ✅ Ergonomici
- Sintassi `.spy` genuinamente intuitiva per DevOps
- Il codice transpilato è leggibile e comprensibile
- REPL funzionante
- Editor support (VSCode grammar + Notepad++)
- Esempi ricchi e progressivi

### ✅ Architetturali
- Transpiler → nessun custom VM
- Import hook per modularità
- Auto-inject delle dipendenze runtime

---

## 7. Criticità e Rischi

### 🔴 Critici

| # | Problema | Impatto |
|---|---|---|
| 1 | **Shell injection** — `{var}` non viene sanitizzato | Sicurezza: un input non fidato in un command template è una vulnerabilità reale |
| 2 | **Traceback in file generati** — i numeri di riga nel traceback puntano al `.py` non al `.spy` | DX: debugging molto scomodo |
| 3 | **Lexer non gestisce raw strings** — `r"$(..."` può essere falso positivo | Correttezza: bug silenzioso |

### 🟡 Importanti

| # | Problema | Impatto |
|---|---|---|
| 4 | **Statement detection euristica** — fallisce su lambda, ternary, comprehensions | Correttezza: bug edge-case |
| 5 | **`capture=False` non è streaming** — output appare tutto alla fine | UX: comandi lunghi sembrano bloccarsi |
| 6 | **Nessun type checker support** — mypy/pyright vedono il `.spy` come Python invalido | DX: zero LSP support sui file sorgenti |
| 7 | **Error messages non mappati** — gli errori di runtime mostrano call stack del `.py` transpilato | DX: confusione durante lo sviluppo |

### 🟢 Minori

| # | Problema |
|---|---|
| 8 | `parser.py` non visto in questa revisione — potenziali issue nel command parser |
| 9 | Nessun modo di vedere il `.py` generato durante l'esecuzione ordinaria |
| 10 | REPL non ha history persistence né completamento (readline non configurato) |

---

## 8. Raccomandazioni

### Priorità Alta

1. **Source maps / line mapping**: Mappare i numeri di riga `.spy` → `.py` e riscrivere i traceback. Questo è il cambio più impattante per la DX.

2. **Sanitizzazione shell injection**: Aggiungere una funzione `shell_quote()` per le interpolazioni, o almeno un warning esplicito per interpolazioni di variabili non quotate. Referenza: `shlex.quote()`.

3. **Fix raw-string nel lexer**: Aggiungere stato `in_raw_string` nel Lexer per ignorare `$(` dentro `r"..."`.

### Priorità Media

4. **Statement detection via AST Python**: Usare `ast.parse()` sul chunk precedente per determinare con certezza il contesto, invece dell'analisi testuale.

5. **Streaming reale per `capture=False`**: Usare `Popen` con `stdout=None` per passthrough diretto al terminale invece di buffer-then-replay.

6. **LSP / language server**: Creare un language server minimale (o plugin pyright) che pre-processa `.spy` → `.py` in-memory per abilitare completamento e type checking.

### Priorità Bassa

7. **REPL migliorato**: `readline` history, completamento, syntax highlighting inline.

8. **`--source-map` flag**: `spy transpile --source-map` per emettere la mappa di riga come JSON.

9. **Modalità `spy run --show-generated`**: Debug flag per stampare il `.py` prima di eseguirlo.

---

## Conclusione

`pycli` è un progetto **genuinamente utile** con un'esecuzione tecnica solida per la sua fase. Il concept è valido, la nicchia esiste, e la sintassi `$(...)` è l'idea più forte del progetto.

La sfida principale non è tecnica: è **competere con tool maturi** (soprattutto `xonsh`) che coprono lo stesso spazio. Il vantaggio di `pycli` — output `.py` standard, zero runtime custom — è reale ma di nicchia.

Per un progetto **personale/team**: ottimo. Per diventare un tool publico di riferimento: investire su source maps, sicurezza injection, e LSP support sono i passi che farebbero la differenza.
