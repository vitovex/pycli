# pycli: Python-Compatible DevOps DSL Specification
## Complete Language Specification (V1)

---

# Overview

This document specifies a lightweight Python-compatible scripting language that extends Python with first-class shell command execution.

The language is designed primarily for:

- DevOps
- Cloud automation
- Infrastructure as Code
- SRE tooling
- CI/CD scripting

The language is **not a new runtime**.

Instead, it is a:

```text
Source-to-Source Transpiler
```

that transforms a custom dialect into standard Python.

Execution flow:

```text
.spy Source
      ↓
Lexer
      ↓
Parser
      ↓
AST Transform
      ↓
Python Source
      ↓
CPython
```

Example:

Input:

```python
vms = $(az vm list)
```

Generated Python:

```python
vms = run("az vm list")
```

---

# Design Goals

## Goals

- Stay very close to Python.
- Avoid introducing new keywords.
- Allow inline shell commands.
- Preserve Bash and PowerShell syntax.
- Support structured command outputs.
- Support Python interpolation.
- Remain transpileable to ordinary Python.

## Non Goals

- Custom VM.
- Custom bytecode.
- Python replacement.
- Shell replacement.
- Runtime interpreter.

---

# File Extensions

Example:

```text
deploy.spy
infra.spy
pipeline.spy
```

Transpilation:

```text
*.spy
    ↓
*.py
```

---

# Core Concept

The language introduces one new expression:

```python
$(...)
```

called a:

```text
CommandExpression
```

Examples:

```python
$(git status)

$(az vm list)

$(kubectl get pods)
```

---

# Command Expression

## Syntax

```ebnf
command_expression
    ::= "$("
           command_body
        ")"
```

Examples:

```python
result = $(git status)

vms = $(az vm list)

pods = $(kubectl get pods -o json)
```

---

# Statement Form

Command expressions may appear as standalone statements.

Example:

```python
$(git status)

$(terraform apply)
```

Generated Python:

```python
run("git status", capture=False)

run("terraform apply", capture=False)
```

Output is streamed to the console (sys.stdout/sys.stderr) and the return value is discarded.

---

# Expression Form

Command expressions may be assigned.

Example:

```python
branch = $(git branch --show-current)
```

Generated:

```python
branch = run(
    "git branch --show-current"
)
```

---

# Parsing Model

The parser recognizes only the outermost DSL command expression.

Example:

```python
$(echo $(hostname))
```

The outer expression:

```python
$( ...)
```

belongs to the DSL.

The inner expression:

```bash
$(hostname)
```

belongs to Bash.

Generated:

```python
run(
    "echo $(hostname)"
)
```

---

# Fundamental Rule

The transpiler must only interpret:

```python
{python_expression}
```

All other command syntax is preserved.

This includes:

```bash
$(...)
$