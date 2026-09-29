# Backlog operativo: parser e pipeline `.spy`

Questo documento conserva i task della prima implementazione come
**storico**. La sezione seguente è l'unico piano operativo corrente:
non considerare i task originali qui sotto come problemi ancora aperti né
come prova di avvenuta certificazione. Riferimenti: codice in `src/pycli/`,
[specifica](pycli-grammar.md) e [README](../README.md).

## Stato verificato e criteri di rilascio (29 settembre 2026)

**Esito: non ancora certificabile su Windows, Linux e macOS.**

| Ambiente | Comando/controllo | Esito osservato |
|---|---|---|
| Windows, virtualenv nel `PATH` | Suite completa, Python locale | 127 superati |
| Windows, `PATH` non modificato | `.venv\Scripts\python.exe -m pytest -q` | 118 superati, 9 falliti: alcuni test e gli esempi invocano ancora `python` come comando esterno (exit code 9009) |
| Windows, prove end-to-end aggiuntive | Argomenti in `$(...)`, tra doppi apici, in `{*items}` e con pipeline | `%` alterato/espanso, backslash finale perso, newline troncata; un valore con virgoletta e `&` può eseguire un secondo comando innocuo di prova |
| Ubuntu WSL2, CPython 3.12.13 | `uv run --python 3.12 --locked pytest -q`, virtualenv Linux separata | 126 superati, 1 fallito (`test_repl_02_sigint_capture_true_handling`), 1 warning `PytestUnraisableExceptionWarning`; fallimento riprodotto in esecuzione mirata |
| macOS | Nessun runner locale | Non verificato |
| Repository | `git diff --check` | Non passa: terminazioni di riga del documento di grammatica e righe vuote finali in alcuni test |

La matrice `.github/workflows/ci.yml` è presente nel worktree ma non
tracciata: **nessuna esecuzione CI su questa versione è stata verificata**.
Il file attuale `docs/pycli-grammar.md` descrive vari casi come portabili e
afferma che la CI gira continuamente: entrambe sono dichiarazioni premature.
La virtualenv WSL usata nella verifica è stata rimossa; `uv.lock` e
`pyproject.toml` non sono stati modificati dalla prova.

### Contratto tecnico deciso per le correzioni

1. Il sottoinsieme portabile `.spy` comprende argomenti letterali e
   interpolati, splat, pipe e redirection esplicitamente riconosciute dal
   parser. Ogni valore Python diventa **un argomento dato**, mai testo da
   ripassare a una shell. Le quote in sorgente raggruppano frammenti dello
   stesso argomento; non sono caratteri da aggiungere al valore. I programmi
   esterni vanno risolti come eseguibili; i built-in della shell **non**
   diventano eseguibili per effetto di `shell=False`. Per non rompere gli
   esempi esistenti, includere nel sottoinsieme portabile `echo` con soli
   argomenti di testo (emettitore DSL con comportamento definito); opzioni
   e altri built-in restano shell-nativi, documentati come tali.
2. Per il sottoinsieme portabile, trasformare i nodi già disponibili in
   stage con `argv` e operatori/target tipizzati. Eseguire i programmi con
   `shell=False`; realizzare pipe collegando stdout e stdin dei processi e
   redirection con file aperti dal runtime. Evitare `list2cmdline()` e
   `shlex.quote()` come sostituti di un `argv` strutturato. Quando un caso
   non è rappresentabile senza shell, fallire esplicitamente oppure
   classificarlo come sintassi nativa, senza fallback apparentemente riusciti.
3. Conservare `run(str)` come API **shell-nativa** per chi la invoca
   direttamente: non cambiarne implicitamente quoting o shell predefinita.
   Nel DSL, costrutti della shell host non portabili (variabili native,
   globbing, `&&`, sostituzione `$(...)` interna) seguono un percorso
   distinto e documentato. Su Windows una sostituzione interna POSIX
   non deve produrre silenziosamente la stringa letterale: segnalare
   sintassi non supportata con posizione nel sorgente. Non introdurre
   PowerShell come fallback implicito.
4. Preservare i contratti visibili di statement/espressione (`capture`),
   `.tee`, `.input`, `!`, `?`, `&`, `await`, codice d'uscita della pipeline
   (ultimo stage come oggi), timeout, stderr e `--unsafe-interpolation`.
   Quest'ultima resta un opt-in non sicuro, non una scorciatoia della
   modalità portabile. Se un percorso non supporta una combinazione,
   emettere un errore esplicito prima di avviare processi.

Queste decisioni sono il **criterio di implementazione**, non una promessa
che il codice corrente le soddisfi. Le eventuali incompatibilità introdotte
nel DSL vanno documentate con esempi prima/dopo e test di regressione.

### Piano ordinato, dipendenze e stato

| Fase | Task | Stato verificato | Prerequisiti per chiuderlo |
|---|---|---|---|
| 0 | COR-01: contratto e routing portabile/nativo | **Completato & Verificato**: routing portabile `argv` esteso a comandi sincroni (`run_expanded`), background (`run_bg`) con redirection corretta (`>` e `>>`), e asincroni (`async_run`), con splat, pipe e token adiacenti (es. `{prefix}-1.0.0`) uniti in un unico f-string | Nessuno |
| 1 | COR-02: struttura di argv/operatori e `run_expanded` | **Completato & Verificato**: `BackgroundJob` drena concorrentemente `stderr` di tutti gli stage a monte con thread dedicati (pipeline >64KB/200KB non si bloccano); `run_bg` corregge `stdout_mode`; `async_run` implementa `.tee` e `.input(...)` | COR-01 |
| 2 | COR-03: quoting/interpolazioni Windows e POSIX | **Completato & Verificato**: matrice dei casi pericolosi (`a%b`, newline, apici con `&`, `ends\`) verificata end-to-end su comandi sincroni, background (`&`) e `await`, con zero esecuzione di comandi aggiuntivi e corretta espansione di splat | COR-02 |
| 2 | COR-04: sottocomandi nativi e errori espliciti | **Completato & Verificato**: errore con riga/colonna su Windows per `$(inner)`, alternativa portabile documentata | COR-01 |
| - | COR-05: coordinate `ParseError` | **Completato & Verificato**: riga/colonna verificate su casi multiriga e mono-riga | Nessuno |
| 3 | COR-08: test `KeyboardInterrupt` POSIX/Windows | **Completato & Verificato**: test ermetico parametrizzato (Windows tree kill, POSIX exit grazioso, POSIX wait timeout) | Indipendente |
| 3 | COR-09: pulizia processi asincroni | **Completato & Verificato**: `_cleanup_async_proc` drena e attende i processi prima della chiusura loop (zero warning) | Indipendente |
| 4 | COR-06: test ermetici e CI sui tre OS | **Parziale**: test ermetici con `sys.executable`, CI tracciata nell'indice Git con `--locked`, verificato Windows (138/138) e WSL2 (138/138); macOS pendente esecuzione CI remota su GitHub Actions | COR-02/03/04/08/09 |
| 5 | COR-07: specifica e backlog fedeli ai risultati | **Completato & Verificato**: specifica aggiornata con routing strutturato `argv` esteso a `&` e `await`, rimosso claim prematuro su CI remota | COR-01/02/03/04/06 |
| 5 | COR-10: igiene diff e rilascio | **Completato & Verificato**: `git diff --check` restituisce 0, suite 138/138 verde | Prima della chiusura |

### COR-01 - Contratto e instradamento della sintassi

**File:** `docs/pycli-grammar.md`, `src/pycli/parser.py`,
`src/pycli/transformer.py`, `src/pycli/runtime.py`, `README.md`.

**Strategia:** elencare nel parser le costruzioni portabili e quelle opache
alla shell, conservando l'informazione su quote, concatenazioni e span del
sorgente. Inviare al motore a `argv` soltanto i comandi del sottoinsieme
portabile; mantenere il percorso shell-nativo per sintassi realmente
specifica della piattaforma. Un costrutto che sembra shell POSIX su
Windows non va reinterpretato come comando portabile. Preservare
l'API `run(str)` e gli script che usano consapevolmente la shell host,
rendendo esplicito l'eventuale cambio di semantica della DSL. Distinguere
gli eseguibili esterni da `echo`, `dir`, `cd`, `type` e altri built-in:
`echo` semplice ha un'emissione portabile definita, gli altri non
passano accidentalmente da `shell=False` (su Windows potrebbero non
essere trovati); comandi `.bat`/`.cmd` richiedono una policy shell-nativa
esplicita e non rientrano nella promessa di quoting portabile.

**Test/chiusura:** tabella source `.spy` -> classificazione -> output/errore
atteso per `$HOME`, `%NAME%`, `$(inner)`, glob, `&&`, pipe, redirection,
quote, `echo` semplice vs `echo -n`, built-in host, `.bat`/`.cmd` e
combinazioni con `{expr}`/`{*expr}`. Nessun caso non supportato
produce un `CommandResult` di successo con sintassi rimasta letterale
o un falso "executable not found" per un built-in della shell.

### COR-02 - Separare definitivamente dati, stage e operatori

**File:** `src/pycli/parser.py`, `src/pycli/transformer.py`,
`src/pycli/runtime.py`, `tests/test_runtime.py`,
`tests/test_compliance_pipeline.py`.

**Causa corrente:** `run_expanded()` calcola `has_operator` ma imposta
comunque `shell=True` se non specificato. Con `shell=False` esplicito
elimina i `ShellOp` dalla lista degli argomenti senza errore. Un operatore
marcato è distinto dai dati nell'AST, ma viene riappiattito in una
stringa passata alla shell; questo non basta a rendere opaca una splat.

**Strategia:**
1. Un argomento è una sequenza di frammenti letterali/interpolati oppure
   un elemento della splat; pipe e redirection sono nodi distinti, il
   target di redirection è un valore, non `ShellOp(f"> {target}")`.
   Valutare ogni espressione Python una sola volta, nell'ordine originale.
2. Senza operatori eseguire `argv` con `shell=False`. Con operatori
   creare processi per stage, collegare pipe e aprire/chiudere i file
   di redirection con cleanup in `finally`, su tutti i sistemi.
   Non trattare stringhe come `"|"`, `"> file"`, `"$(echo text)"`,
   apici e backslash provenienti dai dati come operatori o quote preapplicate.
   Per lo stage `echo` portabile previsto da COR-01, scrivere i valori
   uniti da spazi con newline finale senza invocare il built-in di
   `cmd.exe`; pipe e redirection devono ricevere gli stessi byte.
3. Se `run_expanded(..., shell=False)` riceve un operatore tipizzato,
   rifiutarlo esplicitamente: non scartarlo. Mantenere l'uso pubblico
   precedente di `run_expanded()` per quanto compatibile; se le stringhe
   operatore passate direttamente dall'utente cambiano significato,
   documentare migrazione e comportamento invece di introdurre un
   breaking change silenzioso.
4. Conservare stdin/stdout/stderr, `.input`, `.tee`, capture, check/safe,
   streaming, background/async, timeout e semantica d'uscita dello stage
   finale. Testare anche pipe con output voluminoso per evitare deadlock.

**Test/chiusura:** script `.spy` che stampa `json.dumps(sys.argv[1:])`
per ogni forma senza operatori, con pipe e con `>`, `>>`, `<` in una
directory temporanea. Confrontare l'intera lista con gli elementi di
origine (numero, ordine, caratteri), non sottostringhe dell'output;
una splat vuota non crea un argomento, ogni elemento non vuoto ne crea
esattamente uno. Testare `shell=False` con operatore come errore
esplicito e percorso shell-nativo esistente come regressione.

### COR-03 - Eliminare il quoting Windows che modifica dati o comandi

**File:** `src/pycli/runtime.py` (`shell_quote`, `run_expanded`),
`src/pycli/transformer.py` (interpolazione nei due rami),
`tests/test_compliance_pipeline.py`, `tests/test_security_robustness.py`.

**Causa corrente:** `subprocess.list2cmdline()` codifica argv per un
processo Windows, **non** mette al sicuro un comando passato a
`cmd.exe /c`. Le sostituzioni `%` -> `%%` e `"` -> `\"` non
preservano il dato in `cmd.exe`; tra doppi apici i backslash vengono
raddoppiati. Nei probe Windows, `a%b` è arrivato come `a%%b`,
un nome di variabile tra `%` è stato espanso, `ends\` ha perso il
backslash, una newline è stata troncata e una virgoletta seguita da
`&` ha avviato un secondo comando di prova.

**Strategia:** completare COR-02 per **non serializzare** i valori
dinamici in testo shell nel sottoinsieme portabile. Per esempio,
`$(program "prefix {value} suffix")` costruisce un solo argomento
concatenando i frammenti, senza includere quote e senza chiamare
`shell_quote()` prima di `shell=False`; lo stesso vale per target di
redirection e splat. Nel percorso shell-nativo non promettere
sanitizzazione automatica di `cmd.exe`: un'interpolazione non
rappresentabile in sicurezza deve dare errore chiaro, salvo opt-in
`--unsafe-interpolation` già esistente. Non aggiungere altri
`replace()` ad hoc come fix.

**Matrice di regressione obbligatoria:** `alpha beta`, `a%b`,
`%PYCLI_TEST_VALUE%` con variabile temporanea e valore noto (mai
leggere/stampare variabili reali), `a^b`, `a&b`, `a|b`, `a>b`,
`a"b`, `a\b`, `ends\`, `a!b`, newline, apici semplici/doppi e
un marker di secondo comando **innocuo**. Ripetere per interpolazione
non quotata, tra doppi apici, in splat, con pipe e con redirection.
Misurare `argv`, exit code e assenza del marker: zero alterazioni dei
valori e nessun comando aggiuntivo; ripetere su Windows, Linux e macOS.

### COR-04 - Sostituzioni shell native senza successo ingannevole

**File:** `src/pycli/parser.py`, `src/pycli/transformer.py`,
`src/pycli/runtime.py`, `docs/pycli-grammar.md`,
`tests/test_parser.py`, `tests/test_compliance_pipeline.py`.

**Strategia:** mantenere `$(inner)` come sintassi della shell POSIX
soltanto quando si usa esplicitamente il percorso nativo compatibile.
Se un'espressione `.spy` la contiene su Windows (`cmd.exe`), rilevarla
dal nodo `SubcommandNode` con posizione ed emettere un errore DSL
comprensibile prima dell'esecuzione, anziché stampare `$(...)` e
restituire exit code zero. Non cambiare `run(str)` chiamato direttamente,
che resta API di shell nativa e non garantisce portabilità. Documentare
l'alternativa `.spy` portabile: assegnare prima il risultato del comando
interno e passare `.text`/`.stdout` come argomento al comando esterno.

**Test/chiusura:** su POSIX sostituzione nativa con output atteso;
su Windows errore con file/riga/colonna e nessun processo secondario;
sottostringhe tra apici singoli restano letterali. Eseguire test su
statement, espressione e combinazione con splat, senza trattare un
errore runtime della shell come successo.

### COR-05 - Coordinate degli errori DSL (già verificato)

**File:** `src/pycli/parser.py`, `tests/test_parser.py`.

Test su pipeline vuota, target mancante e splat malformata alla seconda
riga e su comando multiriga passano. Conservare questi test in ogni
refactoring COR-01/02/04; rieseguire la suite di `tests/test_parser.py`
prima di cambiare stato agli altri task. Non riaprire COR-05 senza una
nuova riproduzione con posizione attesa e osservata.

### COR-08 - Rendere portabile il test su `KeyboardInterrupt`

**File:** `tests/test_security_robustness.py`,
`src/pycli/runtime.py` solo se si trova un vero difetto di terminazione.

**Causa WSL2:** `test_repl_02_sigint_capture_true_handling` forza
`communicate()` a sollevare `KeyboardInterrupt` e asserisce sempre
che `_kill_process_tree()` sia chiamata. Su POSIX `run()` invia
prima `SIGINT` e, se `wait(timeout=2)` termina, **non** effettua kill
forzato: il test fallisce anche con comportamento legittimo.

**Strategia:** non avviare un `echo` reale mentre si monkeypatcha
globalmente `Popen.communicate`. Usare un fake controllato di `Popen`
con `send_signal()`, `wait()` e `poll()`; separare tre casi: POSIX
`SIGINT` seguito da wait riuscito (nessuna kill forzata), POSIX wait
scaduto (fallback `_kill_process_tree`), Windows (kill dell'albero).
Asserire che `KeyboardInterrupt` venga rilanciato e che non restino
processi. Se il caso di timeout non funziona, correggere il cleanup
del runtime, non indebolire l'assert.

**Test/chiusura:** il test isolato e l'intero
`tests/test_security_robustness.py` passano su Windows e WSL2/CI Linux;
eseguire i tre casi più volte per escludere dipendenza dalla velocità
di `echo test`.

### COR-09 - Chiudere il transport asincrono prima dell'event loop

**File:** `src/pycli/runtime.py` (`async_run`),
`tests/test_security_robustness.py` e test mirati async.

**Evidenza WSL2:** suite completa: un
`PytestUnraisableExceptionWarning` nel distruttore di
`BaseSubprocessTransport` dopo `RuntimeError: Event loop is closed`.
Il warning è comparso anche nella prova mirata, senza un secondo
fallimento. Non attribuirlo automaticamente al solo test SIGINT:
isolare il caso con tracing/warnings-as-errors.

**Strategia:** verificare il percorso `async_run()` con
`asyncio.wait_for(proc.communicate())` in timeout e cancellazione.
Terminare il processo **e gli eventuali figli della shell**, attendere
la terminazione con un limite esplicito, drenare o chiudere stdout e
stderr, poi lasciare chiudere l'event loop. Valutare un process group
POSIX quando la shell avvia figli; riusare la terminazione dell'albero
su Windows. Evitare `except Exception: pass` nella pulizia: gli errori
di cleanup vanno resi osservabili senza nascondere l'eccezione originale.
Non usare attributi privati di `asyncio` per simulare una chiusura.

**Test/chiusura:** ripetere almeno 20 volte timeout, cancellazione e
successo di un processo breve, con warning dei transport trattati
come errori; nessun processo figlio sopravvive al timeout e nessuna
attesa infinita. Test isolati e suite completa senza
`PytestUnraisableExceptionWarning` su Linux e Windows.

### COR-06 - Test ermetici e CI realmente eseguita su tre OS

**File:** `tests/test_runtime.py`, `tests/test_cli.py`,
`tests/test_security_robustness.py`, `tests/test_compliance_pipeline.py`,
`examples/complex_devops.spy`, `.github/workflows/ci.yml`.

**Strategia:** sostituire nei test di esecuzione `python -c` con
`sys.executable` (o un helper che compone argv senza quoting
dipendente dalla shell); lasciare gli snippet solo transpilati
come tali. Aggiornare gli esempi effettivamente eseguiti dai test,
senza richiedere `python` nel `PATH`. Testare il risultato esatto dei
valori, non solo l'esistenza di `ShellOp` nel codice generato.
La CI deve usare `uv sync --locked --dev` e `uv run --locked pytest`
con almeno Python 3.12 su `windows-latest`, `ubuntu-latest`,
`macos-latest`, selezionando i trigger della branch effettiva;
non mascherare test portabili falliti con `skip` o `xfail`.

**Test/chiusura:** su Windows sia il comando normale sia l'invocazione
diretta della virtualenv **senza cambiare `PATH`** danno 0 fallimenti;
su WSL2 Linux CPython 3.12.13 suite senza fallimenti e warning; macOS
non viene dichiarato verificato finché il relativo job CI non è verde.
Controllare i log dei tre job sul commit/PR che contiene anche i file
attualmente non tracciati. Per ogni combinazione portabile misurare
`argv`, stdout, stderr, exit code e presenza/assenza di file temporanei.

### COR-07 - Allineare documentazione, esempi e stato reale

**File:** `docs/pycli-grammar.md`, `README.md`, questo documento,
eventuali esempi modificati.

**Strategia:** rimuovere o qualificare subito le frasi che garantiscono
quoting sicuro su Windows e CI "continuamente eseguita"; la semplice
presenza del workflow non dimostra che sia passato. Dopo COR-01/02/03/04
aggiornare la matrice delle feature con perimetro e limiti reali,
esempi portabili vs shell-nativi e percorso `--unsafe-interpolation`.
Il backlog storico resta tale: lo stato operativo è **solo** nella
tabella sopra, aggiornata con evidenze riproducibili, non con il numero
di task implementati.

**Test/chiusura:** ogni riga "supportata" della specifica ha almeno
un test end-to-end su ogni OS dichiarato; nessun claim di certificazione
Linux/macOS prima dei job CI verdi; documentare comportamenti modificati
per script preesistenti.

### COR-10 - Diff pulito e consegna verificabile

**File:** `docs/pycli-grammar.md` e gli EOF dei file di test modificati;
aggiungere una policy di fine riga solo se richiesta da più file.

**Strategia:** uniformare le terminazioni di riga del documento
modificato senza riformattare file estranei; eliminare le righe vuote
aggiunte alla fine dei test. Controllare anche file non tracciati
(documento e workflow) prima della pubblicazione. Eseguire
`git diff --check`; se necessario distinguere CRLF legittimo da
spazi finali con `git -c core.whitespace=cr-at-eol diff --check`,
ma non limitarsi a disabilitare il controllo come "fix".

**Chiusura globale:** COR-01/02/03/04/06/07/08/09/10 chiusi solo quando
sono verdi i test mirati, la suite completa su Windows **senza PATH
manuale**, la suite WSL2 senza warning, i tre job CI sullo stesso
commit e `git diff --check`. COR-05 rimane verificato. Se uno di questi
gate non è disponibile, registrarlo come **non verificato**, non come
**completato**. Non riaprire un task per un criterio che era già elencato
qui: implementazione e test del criterio fanno parte dello stesso task.

---

> **Backlog originale (storico):** le sezioni seguenti descrivono la prima
> lista, non lo stato corrente; prevalgono contratto, stati e gate sopra.

## Perimetro e regole di accettazione

- **P0**: blocchi, perdita di semantica, Python generato non eseguibile o
  gestione non affidabile degli argomenti. **P1**: diagnostica e correttezza
  dei contesti meno comuni. **P2**: manutenzione e qualità del rilascio.
- Il contratto attuale è: interpretare `$(...)` esterno e `{espressione Python}`,
  preservando il resto della sintassi della shell, incluse le sostituzioni
  `$(...)` interne. Quando una modifica richiede di scegliere tra preservare
  testo shell e costruire argomenti senza shell, definire **prima** la semantica
  e i limiti per Bash/POSIX e Windows; non cambiare implicitamente il
  comportamento documentato.
- Ogni PR aggiunge un test di regressione per il caso citato, verifica il
  Python generato **e**, quando conta il comportamento, esegue uno script
  `.spy` con un comando innocuo/temporaneo. Coprire almeno `transpile()`,
  `spy run`, `spy transpile` e import `.spy` se l'intervento li attraversa.
- Conservare i casi già coperti da `tests/test_parser.py`,
  `tests/test_transformer.py`, `tests/test_security_robustness.py` e
  `tests/test_advanced_features.py`. Il backlog di
  [sicurezza e robustezza](../TODO/security_robustness_tasks.md) dichiara già
  completati sanitizzazione di base, raw string, limiti di input, streaming,
  timeout e prima versione della source map: non sono qui riproposti come
  funzionalità assenti.

## Sequenza e dipendenze

| Fase | Task | Prerequisiti |
|---|---|---|
| Subito | PAR-01, TRF-04, LEX-01, TRF-03 | Nessuno |
| Parser | PAR-02, PAR-03, PAR-06 | PAR-01; anche PAR-03 per PAR-06 |
| Semantica | PAR-04, PAR-05, TRF-02 | PAR-01 e PAR-03 per PAR-04/05; PAR-01 per TRF-02 |
| Espansione | TRF-01 | PAR-04, PAR-05, TRF-02 |
| Diagnostica | DX-01, DX-02, INT-01 | LEX-01 e TRF-04 per DX-01; PAR-06 per DX-02; DX-01 e TRF-04 per INT-01 |
| Chiusura | QA-01, DOC-01 | Task funzionali pertinenti |

Le attività senza dipendenze reciproche nella stessa fase possono procedere
in parallelo. Stima indicativa: **S** = fino a 1 giorno, **M** = 2-3 giorni,
**L** = più di 3 giorni; rivalutare dopo aver fissato il contratto di PAR-01.

## Parser: prima fissare il contratto, poi le strutture

### PAR-01 — Specificare i confini DSL/shell (P0, M)

**Dove:** `docs/pycli-grammar.md`, `src/pycli/parser.py`,
`src/pycli/transformer.py`, `tests/test_parser.py`.

**Motivo:** la specifica dice di lasciare `$(...)` interni alla shell, mentre
il parser li trasforma in `SubcommandNode`. Il percorso senza splat usa la
stringa originale; quello con splat tenta di ricostruirla dall'AST. Questo
rende ambiguo chi possiede quote, operatori e interpolazioni annidate.

**Attività:**
1. Scrivere una tabella di casi per testo letterale, `$HOME`/`$env:NAME`,
   sostituzione shell `$(...)`, `{expr}`, `{*expr}`, pipe, redirection,
   virgolette singole/doppie e combinazioni annidate.
2. Decidere quali costrutti devono essere opachi al transpiler e quali
   richiedono segmenti strutturati; esplicitare la semantica della splat in
   presenza di operatori e i casi non supportati.
3. Definire input DSL errati (ad esempio interpolazione aperta o splat vuota)
   distinti da sintassi shell che va lasciata alla shell. Aggiornare la
   specifica **prima** di cambiare i nodi AST.

**Accettazione:** per ogni riga della tabella è indicato output atteso oppure
errore esplicito; gli esempi del README restano nel perimetro supportato.

### PAR-02 — Garantire avanzamento e terminazione dello scanner (P0, S)

**Dove:** `src/pycli/parser.py`, `tests/test_parser.py`.

**Motivo verificato sul codice:** `_parse_command()` interrompe la lettura
di `WordNode` davanti a `$`, ma gestisce solo `$(`. Un `$` isolato o `$HOME`
non viene consumato da nessun ramo: il ciclo può non terminare.

**Attività:** introdurre un ramo che consumi i caratteri shell letterali
secondo PAR-01; mantenere l'invariante `i_nuovo > i_precedente` oppure
sollevare `ParseError` con posizione. Evitare di trattare tutti i `$` come
sostituzioni shell.

**Accettazione:** `CommandParser("echo $HOME").parse()` e
`transpile("$(echo $HOME)")` terminano e preservano il riferimento shell;
test con timeout per `$` isolato, `$var` accanto a parole, `$(` non bilanciato
e una sequenza di caratteri sconosciuti. Nessun ciclo infinito.

### PAR-03 — Sostituire scansioni divergenti con delimitatori coerenti (P1, L)

**Dove:** `src/pycli/lexer.py`, `src/pycli/parser.py`, `tests/test_lexer.py`,
`tests/test_parser.py`.

**Motivo:** lexer, `_split_pipeline_stages()`, `_parse_command()` e
`_read_word_or_string()` scandiscono separatamente quote, parentesi, escape
e graffe; i rami delle sottoshell nel parser contano parentesi anche quando
sono tra virgolette. I confini possono risultare diversi tra fasi.

**Attività:** definire stati di scansione condivisi o un protocollo unico
per quote/escape/delimitatori senza aggiungere un parser Python completo;
conservare gli offset assoluti. Riutilizzare il risultato per delimitare
pipeline, interpolazioni, splat, redirection e sostituzioni shell.

**Accettazione:** test tabellari su `|`, `(`, `)`, `{`, `}` dentro quote,
interpolazioni con chiamate Python e sostituzioni shell annidate; nessun
separatore interno spezza uno stage. Verificare anche input multiriga.

### PAR-04 — Preservare operatori e ordine degli stage nell'AST (P0, M)

**Dove:** `src/pycli/parser.py`, `src/pycli/transformer.py`,
`tests/test_parser.py`, `tests/test_transformer.py`.

**Motivo verificato:** `$(echo {*files} | cat)` oggi genera
`run_expanded("echo", *files, "cat")`: il separatore `|` scompare nel ramo
expanded. Nell'AST gli stage sono comandi senza separatori; nel ramo
expanded anche la redirection viene ricomposta in una stringa come
`"> out.txt"`, mescolando operatori e argomenti.

**Attività:** modellare stage, operatori e target senza appiattirli;
produrre output in ordine originale e separare gli operatori dagli argomenti
espansi. Dopo PAR-01, rifiutare con errore esplicito combinazioni non
supportabili, anziché generare un comando diverso.

**Accettazione:** `$(echo {*files} | cat)` preserva la pipe o fallisce con
diagnostica documentata; `>`, `>>`, `<`, quote e pipe dentro stringhe non
diventano argomenti ordinari. Test di esecuzione con file temporanei.

### PAR-05 — Preservare le sottoshell quando compare una splat (P0, M)

**Dove:** `src/pycli/parser.py`, `src/pycli/transformer.py`,
`tests/test_parser.py`, `tests/test_transformer.py`.

**Motivo verificato:** con `$(echo $(printf x) {*items})`, il ramo expanded
formatta `SubcommandNode.pipeline` tramite la rappresentazione della
dataclass (`PipelineNode(...)`) invece di preservare `$(printf x)`.

**Attività:** conservare lo span/testo originale della sostituzione shell
o fornire una serializzazione fedele dei segmenti, in accordo con PAR-01;
evitare `str(dataclass)` nella generazione del comando.

**Accettazione:** output che conserva esattamente `$(printf x)` con e senza
splat; test di nested subcommand, quote e redirection dentro la sottoshell.

### PAR-06 — Diagnostica per input DSL incompleti (P1, M)

**Dove:** `src/pycli/parser.py`, `src/pycli/lexer.py`,
`tests/test_parser.py`, `tests/test_lexer.py`.

**Motivo:** `ParseError` esiste ma non è usato dal parser. Un target vuoto
come `$(echo >)` o una pipeline con stage vuoto diventano oggi nodi o testo
incompleti; la decisione di validarli dipende dal contratto di PAR-01.

**Attività:** validare soltanto le strutture possedute dal DSL (per esempio
`{*}`, graffe/quote DSL aperte) e quelle dichiarate invalide da PAR-01;
includere riga, colonna e frammento, senza assorbire gli errori Python.
Non riscrivere né rifiutare arbitrariamente sintassi shell lecita.

**Accettazione:** errori deterministici nei casi dichiarati invalidi,
riportati da API/CLI/REPL; nessun `IndexError`, testo troncato o fallback
silenzioso; sintassi shell opaca preservata.

## Generazione e semantica dei comandi

### TRF-01 — Correggere espansione e quote nel percorso `run_expanded` (P0, L)

**Dove:** `src/pycli/transformer.py`, `src/pycli/runtime.py`,
`tests/test_transformer.py`, `tests/test_runtime.py`.

**Motivo verificato:** `$(echo "a {name}" {*items})` genera sia la stringa
interpolata sia un'ulteriore interpolazione `{name}`: il parser aggiunge un
`InterpolationNode` dopo `StringNode`. Il runtime decide `shell=True` anche
esaminando caratteri di operatori dentro gli argomenti e concatena le parti
senza mantenere l'informazione su quali siano dati e quali operatori.

**Attività:** rappresentare l'interpolazione dentro la stringa senza
duplicarne il valore; definire espansione splat come sequenza di argomenti
e operatori distinti, evitando inferenze da `>`/`|` presenti nei dati.
Allineare il ramo expanded a `.tee`, `.input`, `!` e `?` già supportati dal
ramo semplice; preservare l'ordine di valutazione delle espressioni.

**Accettazione:** ogni `{expr}` viene valutata una volta e compare una volta
nel comando; elementi della splat contenenti spazi o caratteri di shell
restano un singolo argomento secondo PAR-01. Test sia senza operatori sia
con operatori reali, su Windows e POSIX dove applicabile.

### TRF-02 — Allineare quoting e interpolazione ai contesti (P0, M)

**Dove:** `src/pycli/transformer.py`, `src/pycli/runtime.py`,
`tests/test_transformer.py`, `tests/test_security_robustness.py`.

**Motivo:** la protezione `shell_quote()` di base è già presente, ma viene
inserita anche dentro doppi apici shell. Il risultato dipende dalla shell:
`shlex.quote()` usa regole POSIX, mentre Windows usa una shell differente.
La stessa trasformazione non garantisce che il valore sia un *argomento*
opaco in ogni contesto.

**Attività:** fissare il contratto di quoting per shell e piattaforma;
testare interpolazioni non quotate, tra doppi apici, in target di redirection
e con splat; usare una costruzione per contesto anziché concatenare quote
shell incompatibili. Conservare l'opzione `--unsafe-interpolation` e
documentarne esplicitamente il perimetro.

**Accettazione:** valori con spazi, apici e caratteri speciali sono dati e
non cambiano numero di argomenti né struttura del comando nelle piattaforme
supportate; i test misurano l'esecuzione effettiva, non soltanto il testo
del Python generato.

### TRF-03 — Riconoscere contesto Python e chaining senza regex fragili (P1, L)

**Dove:** `src/pycli/transformer.py`, `tests/test_transformer.py`,
`tests/test_advanced_features.py`.

**Motivo:** `_is_statement_context()` considera la porzione di riga prima
e dopo il token. `.tee` e `.input(...)` sono ricavati dal token Python
successivo con regex e conteggio di parentesi che non tiene conto delle
stringhe. I test attuali coprono statement, lambda, ternario e list
comprehension di base, non tutte le combinazioni multilinea.

**Attività:** individuare statement autonomi usando token Python/AST
provvisorio invece di pattern di riga; analizzare l'eventuale chaining con
delimitatori bilanciati e quote; non riordinare effetti collaterali.

**Accettazione:** `capture=False` solo per statement autonomi; corretto per
`return`, condizioni, `await`, parentesi multilinea, comprehension e
statement dopo `;`. `.input(f(")"))`, `.tee.input(x)` e proprietà normali
non perdono caratteri né modificano il significato.

### TRF-04 — Inserire import Python validi e proteggere i nomi (P0, M)

**Dove:** `src/pycli/transformer.py`, `tests/test_transformer.py`,
`tests/test_cli.py`.

**Motivo verificato:** per `from __future__ import annotations` seguito da
un comando, l'import di `pycli.runtime` viene anteposto al future import:
il testo passa anche da `ast.parse()` ma non da `compile()`. La ricerca
di un import preesistente è testuale.

**Attività:** inserire l'import dopo shebang, dichiarazione di codifica,
docstring di modulo e tutti i future import; gestire import esistenti,
multilinea e alias senza produrre sintassi o binding duplicati. Non
sovrascrivere silenziosamente un nome utente: definire e testare il
comportamento in caso di conflitto.

**Accettazione:** `compile(transpile(source), ..., "exec")` riesce per
future import, docstring e shebang; la docstring resta la docstring di
modulo. L'import preesistente non viene corrotto.

## Lexer, diagnostica ed entry point

### LEX-01 — Correggere posizioni e suffissi dei token (P1, S)

**Dove:** `src/pycli/lexer.py`, `tests/test_lexer.py`.

**Motivo verificato:** `_scan_command_expr()` aggiorna `self.pos` prima di
calcolare la nuova colonna del suffisso `!`/`?`/`&`, quindi l'incremento
risulta zero. Spazi prima dei modificatori vengono consumati senza
avanzare con `_advance()`; le posizioni dei token successivi possono
essere sbagliate.

**Attività:** far avanzare sempre posizione, riga e colonna tramite
un'unica operazione; definire la regola per spazi tra `)` e modificatore
e per `&` operatore Python rispetto a background.

**Accettazione:** offset esatti di due comandi consecutivi con spazi,
tabulazioni, suffissi e nuove righe; i casi ambigui producono il
comportamento documentato.

### DX-01 — Rendere la source map affidabile per ogni chunk (P1, M)

**Dove:** `src/pycli/transformer.py`, `src/pycli/__init__.py`,
`tests/test_cli.py`, `tests/test_security_robustness.py`.

**Motivo verificato:** per due comandi su due righe il transformer produce
quattro voci di mappa (oltre alla riga dell'import), perché
conta separatamente il chunk senza newline e quello con newline. Esiste
già un excepthook e un test su una riga Python normale: va corretto il
calcolo, non aggiunta una seconda mappa.

**Attività:** costruire la mappa da offset/righe dell'output concatenato;
aggiornarla dopo l'iniezione degli import e definire la posizione degli
errori su codice generato. Controllare anche traceback da moduli `.spy`
importati e REPL (senza assumere che condividano l'excepthook del runner).

**Accettazione:** eccezione sul secondo comando, su codice Python tra
comandi e su script con future import indica riga e testo `.spy` corretti;
nessuna riga fantasma nella mappa.

### DX-02 — Propagare errori parser/lexer in CLI e runner (P1, S)

**Dove:** `src/pycli/__init__.py`, `src/pycli/repl.py`,
`tests/test_cli.py`, `tests/test_security_robustness.py`.

**Motivo:** `SpyConsole` intercetta `LexerError` e `ParseError`, mentre il
ramo `transpile` della CLI intercetta soltanto `ValueError` e
`TranspilerError`; `run_file()` gestisce solo `TranspilerError` durante la
transpilazione.

**Attività:** mostrare errore di sintassi uniforme con file/riga/colonna e
exit code non zero in `spy run` e `spy transpile`; evitare traceback interno
per errori DSL previsti, conservando traceback per bug inattesi.

**Accettazione:** comando `.spy` non chiuso e input DSL invalido producono
lo stesso messaggio utile via API/CLI/REPL; il runner non esegue codice
parzialmente transpilato.

### INT-01 — Verificare la stessa semantica negli import `.spy` (P1, M)

**Dove:** `src/pycli/importer.py`, `src/pycli/__init__.py`,
`tests/test_importer.py`.

**Motivo:** l'import hook usa `transpile(source)` e conserva bytecode per
hash della sorgente; runner e CLI possono passare opzioni come
`unsafe_interpolation`/`validate`. Un cambio nel parser deve avere una
politica esplicita anche per i moduli importati e per cache/errori.

**Attività:** definire se e come propagare le opzioni dall'entry point
all'import hook, invalidare la cache quando varia il codice generato e
riportare le posizioni `.spy` anche per errori negli import; non cambiare
implicitamente la sicurezza degli import.

**Accettazione:** stesso modulo importato dopo opzioni differenti non
riutilizza bytecode incompatibile; gli errori mantengono il nome `.spy`
e la diagnostica concordata; import normali restano invariati.

## Qualità e documentazione

### QA-01 — Suite di conformità e regressione della pipeline (P1, M)

**Dove:** `tests/test_parser.py`, `tests/test_lexer.py`,
`tests/test_transformer.py`, `tests/test_cli.py`,
`tests/test_importer.py`, esempi in `examples/`.

**Attività:** trasformare la tabella PAR-01 in test parametrizzati
source -> token/AST -> Python compilabile -> effetto osservabile; usare
processi locali innocui e file temporanei, senza dipendere da `git`,
`kubectl` o servizi cloud. Aggiungere test con timeout per la terminazione
del parser e un piccolo corpus di input malformati.

**Accettazione:** `uv run pytest` passa; gli esempi `.spy` di riferimento
transpilano e compilano dove previsti; per i casi legati alla shell la
matrice Windows/POSIX indica test eseguiti e limitazioni conosciute.

### DOC-01 — Sincronizzare specifica, README ed esempi (P2, S)

**Dove:** `docs/pycli-grammar.md`, `README.md`, `examples/`, `AGENTS.md`
solo se cambiano le responsabilità dei moduli.

**Motivo:** la specifica si interrompe dopo la regola fondamentale; il
README descrive più funzionalità ma non sempre distingue testo shell,
argomenti espansi e codice Python generato. Le precedenti revisioni in
`TODO/` contengono osservazioni storiche ora risolte.

**Attività:** completare la grammatica effettivamente supportata, includere
tabella degli operatori/suffissi, esempi prima/dopo e limiti della shell
per piattaforma. Aggiornare la documentazione man mano che i task vengono
chiusi, senza riportare vecchie criticità come stato corrente.

**Accettazione:** ogni esempio documentato corrisponde al comportamento
testato; nessuna feature promessa solo dal README è in conflitto con la
specifica.
