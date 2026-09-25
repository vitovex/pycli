# pycli: Python-Compatible DevOps DSL Specification

## Overview

This document specifies a lightweight Python-compatible scripting language that extends Python with first-class shell command execution.

The design goal is to combine:

- Python readability
- Python ecosystem compatibility
- PowerShell-like command invocation
- Bash-like command composition
- Native object handling
- Zero custom runtime VM

The language is transpiled to standard Python before execution.

---

# Design Principles

## Goals

1. Remain as close to Python as possible.
2. Minimize new syntax.
3. Allow inline shell command execution.
4. Enable seamless interoperability between shell commands and Python code.
5. Support future extensions such as:
   - pipelines
   - redirection
   - parallel execution
   - remote execution
   - structured output
6. Enable implementation as a source-to-source transpiler.

## Non-goals

1. Creating an entirely new programming language.
2. Replacing Python syntax.
3. Implementing a custom VM.
4. Replacing Python packaging or imports.

---

# File Extensions

Example:

```text
script.spy
deploy.spy
infra.spy
```

The transpiler converts:

```text
*.spy
```

into:

```text
*.py
```

---

# Execution Pip*line

```text
.spy Source
      ↓
*exer
      ↓
Parser
      ↓
AST Tr*nsform
      ↓
Python Source
     *↓
CPython
```

Example:

Input:

`*`python
vms = $(az vm list)
```

G*nerated Python:

```python
vms = r*n("az vm list")
```

---

# Core C*ncept

A shell command*can appear inside a*command expression.

Syntax:

```p*thon
$(...)
```

Examples:

```pyt*on
$(git status)

$(az vm list)

$*kubectl get pods)
```

---

# Comm*nd Expression

## Syntax

```ebnf
*ommand_expression
    ::=*"$(" pipeline ")"
```

Examples:

*``python
result = $(git status)

v*s = $(az vm list)

pods =*$(kubectl get pods -o json)
```

*--

# Statement Form

Command expr*ssions*can be used as standalone statemen*s.

Example:

```python
$(terrafor* apply)

$(git status)
```

*quivalent Python:

```python
run("*erraform apply")

run("git status"*
```

Return value is discarded.

*--

# Expression Form

Command exp*essions may be assigned.

Example:*
```python
result = $(git status)
*``

Equivalent:

```python
result * run("git status")
```

---

# Pip*line Grammar

Commands may be chai*ed.

Syntax:

```ebnf
pipeline
    ::= command ("|" command)*
```

Example:

```python
$(kubectl get pods | grep api)
```

Equivalent:

```text
kubectl get pods | grep api
```

AST:

```text
Pipeline
 ├─ kubectl get pods
 └─ grep api
```

---

# Command Grammar

```ebnf
command
    ::= command_part+
```

---

# Command Parts

```ebnf
command_part
    ::= word
     | string
     | interpolation
     | subcommand
     | redirection
```

---

# Words

Words represent executable names, arguments and options.

Examples:

```python
git

status

--output

json

/var/log
```

Grammar:

```ebnf
word
    ::= TOKEN
```

---

# String Literals

Standard shell-like quoted strings.

Examples:

```python
$(echo "hello world")

$(echo 'hello world')
```

Grammar:

```ebnf
string
    ::= SINGLE_QUOTED
     | DOUBLE_QUOTED
```

---

# Python Interpolation

## Motivation

Variables and expressions should be usable inside shell commands.

Example:

```python
subscription = "production"

$(az account set --subscription {subscription})
```

Generated Python:

```python
run(
    f"az account set --subscription {subscription}"
)
```

---

## Grammar

```ebnf
interpolation
    ::= "{" python_expression "}"
```

---

## Examples

Variables:

```python
$(echo {name})
```

Properties:

```python
$(echo {user.name})
```

Expressions:

```python
$(echo {len(items)})
```

Function calls:

```python
$(az account set --subscription {get_subscription()})
```

---

# List Expansion

Expands iterable values into individual arguments.

Example:

```python
files = ["a.txt", "b.txt"]

$(rm {*files})
```

Generated command:

```bash
rm a.txt b.txt
```

Grammar:

```ebnf
splat
    ::= "{*" python_expression "}"
```

---

# Subcommands

Commands may contain nested commands.

Example:

```python
$(echo $(git branch --show-current))
```

Generated shell:

```bash
echo $(git branch --show-current)
```

---

## Grammar

```ebnf
subcommand
    ::= "$(" pipeline ")"
```

---

# Redirection

Output redirection follows conventional shell semantics.

Examples:

```python
$(git status > status.txt)

$(grep error logfile.txt > errors.txt)

$(cat input.txt | sort > sorted.txt)
```

Grammar:

```ebnf
redirection
    ::= ">" file
     | ">>" file
     | "<" file
```

---

# Command Result Object

By default every command returns a CommandResult object.

Example:

```python
result = $(git status)
```

Runtime type:

```python
CommandResult
```

Properties:

```python
result.stdout
result.stderr
result.exit_code
result.command
result.duration
```

---

# Truthiness

Command results evaluate according to exit code.

Example:

```python
if $(git diff --quiet):
    print("clean")
```

Equivalent:

```python
if result.exit_code == 0:
```

Truth table:

| Exit Code | Boolean Value |
|------------|---------------|
| 0 | True |
| Non-zero | False |

---

# Structured Output

## JSON Auto-Detection

Many DevOps CLIs produce JSON.

Examples:

```python
$(az vm list)

$(kubectl get pods -o json)

$(terraform output -json)
```

The runtime should optionally attempt JSON parsing.

---

# Parsed Result Access

Example:

```python
vms = $(az vm list)

for vm in vms.json:
    print(vm.name)
```

Alternative:

```python
vms = $(az vm list).json
```

Runtime:

```python
json.loads(stdout)
```

---

# Dynamic Object Mapping

JSON objects should become navigable structures.

Example JSON:

```json
{
  "name": "vm01",
  "size": "Standard_D2s_v5"
}
```

Python access:

```python
vm.name

vm.size
```

instead of:

```python
vm["name"]

vm["size"]
```

---

# Error Handling

Default behavior:

```python
$(git status)
```

does not raise.

Instead:

```python
result.exit_code
```

contains status information.

---

# Strict Mode

Explicit strict execution.

Example:

```python
$(git status)!
```

Semantics:

```python
run(..., check=True)
```

Raises exception on non-zero exit code.

---

# Future Extension: Parallel Execution

Not mandatory for v1.

Syntax proposal:

```python
$&(
    terraform apply
)
```

or

```python
parallel(
    $(command1),
    $(command2)
)
```

---

# Future Extension: Remote Execution

Not mandatory for v1.

Example:

```python
$(remote("vm01") {
    apt update
    apt upgrade -y
})
```

---

# Future Extension: Typed Commands

Examples:

```python
json_vms = $json(az vm list)

text = $text(git status)

code = $code(myscript.sh)
```

Generated Python:

```python
json_vms = run_json(...)

text = run_text(...)

code = run_code(...)
```

---

# Complete Grammar

```ebnf
command_expression
    ::= "$(" pipeline ")"

pipeline
    ::= command ("|" command)*

command
    ::= command_part+

command_part
    ::= word
     | string
     | interpolation
     | splat
     | subcommand
     | redirection

interpolation
    ::= "{" python_expression "}"

splat
    ::= "{*" python_expression "}"

subcomman*
    ::= "$(" pipeline ")"

redire*tion
    ::= ">" file
     | ">>" *ile
     | "<" file

word
    ::= *OKEN

string
    ::= SINGLE_QUOTED*     | DOUBLE_QUOTED
```

---

# E*ample

Source DSL:

```python
subs*ription = "prod"

$(az login)

vms*= $(az vm list --subscription {sub*cription})

for vm in vms.json:
  * print(vm.name)

branch = $(git br*nch --show-current)

$(echo Curren**branch: {branch.stdout})

files =*["temp1*txt", "temp2.txt"]

$(rm {*files})*
if $(git diff --quiet):
    print*"Repository clean")
```

Generated*Python:

```python
subscription = *prod"

run("az login")

vms = run(*    f"az vm list --subscription {s*bscription}"
).json

for vm in vms*
    print(vm.name)

branch = run(*    "git branch --show-current"
)
*run(
    f"echo Current branch: {b*anch.stdout}"
)

files = ["temp1.t*t", "temp2.txt"]

run_expanded(
  * "rm",
    *files
)

if run("*it diff --quiet"):
    print("Repo*itory clean")
```

---

# Implemen*ation Recommendation

Implement in*the following order:

1. Lexer ext*nsion for `$(...)`
2. Parser for c*mmand expressions
3. Python source*generation
4. Runtime library (`ru*()`)
5. Interpolation support
6. C*mmandResult type
7. JSON auto pars*ng
8. Pipelines
9.*Redirections
10. Subcommands
11. S*rict mode
12. Advanced features

T*e first*production-ready version should be*implemented as a transpiler target*ng standard CPython without modifi*ations to the Python interpreter.
*```*