#!/usr/bin/env python3
# Calculate the interface hashes of the functions exported to loaded code
#
# Copyright (C) 2026  Maksim Bolgov <maksim8024@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
#
# The firmware build and the host (when building a loadable module)
# both run this script on src/loader.c. The hash of each exported
# function covers its prototype and the definitions of all types
# reachable from it.
import sys, re, json, hashlib, optparse

ABI_VERSION = 1

# Pseudo symbol covering the types used by generated module glue code
CORE_SYMBOL = '_loader_core'
CORE_TYPES = ['struct loader_module_header', 'struct command_parser',
              'struct command_encoder', 'struct timer', 'struct task_wake',
              'PT_uint32', 'SF_RESCHEDULE']

TOKEN_RE = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''
                      r'|[A-Za-z_]\w*|\d[\w.]*|->|<<|>>|\.\.\.|&&|\|\|'
                      r'|\+\+|--|[-+*/%&|^!<>=]=|\S')
IDENT_RE = re.compile(r'^[A-Za-z_]\w*$')
OPEN, CLOSE = set('({['), set(')}]')
TAGS = ['struct', 'union', 'enum']
ATTRIBUTES = ['__attribute__', '__attribute', '__asm__', 'asm']
STORAGE = ['extern', 'static', 'inline', '__inline', '__inline__']

def error(msg):
    sys.stderr.write(msg + "\n")
    sys.exit(-1)

def is_ident(tok):
    return IDENT_RE.match(tok) is not None

# Tokenize the preprocessed code that is not from a system header
def read_preprocessed(filename):
    tokens = []
    in_system = False
    f = open(filename, 'r')
    for line in f:
        if line.startswith('#'):
            m = re.match(r'#\s*\d+\s+"[^"]*"(.*)$', line)
            if m is not None:
                in_system = '3' in m.group(1).split()
            continue
        if not in_system:
            tokens.extend(TOKEN_RE.findall(line))
    f.close()
    return tokens

# Split tokens into top-level declarations and function definitions
def split_statements(tokens):
    stmts = []
    cur = []
    depth = 0
    body_start = None
    for tok in tokens:
        cur.append(tok)
        if tok in OPEN:
            if tok == '{' and not depth:
                body_start = len(cur) - 1
            depth += 1
        elif tok in CLOSE:
            depth -= 1
            if (tok == '}' and not depth and body_start
                and cur[body_start - 1] == ')'):
                # End of a function definition
                stmts.append(cur)
                cur = []
                body_start = None
        elif tok == ';' and not depth:
            stmts.append(cur)
            cur = []
            body_start = None
    return stmts

def typedef_name(stmt):
    name = None
    depth = 0
    for i, tok in enumerate(stmt):
        if tok in OPEN:
            if (tok == '(' and not depth and i + 2 < len(stmt)
                and stmt[i+1] == '*' and is_ident(stmt[i+2])):
                # Function pointer typedef
                return stmt[i+2]
            depth += 1
        elif tok in CLOSE:
            depth -= 1
        elif (not depth and is_ident(tok) and tok != 'typedef'
              and tok not in ATTRIBUTES):
            name = tok
    return name

# Find the type definitions (by tag, typedef, or enumerator name)
def index_types(stmts):
    defs = {}
    for stmt in stmts:
        if stmt[-1] == '}':
            # Function definition
            continue
        if stmt[0] == 'typedef':
            name = typedef_name(stmt)
            if name is not None:
                defs.setdefault(name, stmt)
        depth = 0
        for i, tok in enumerate(stmt):
            if tok in OPEN:
                depth += 1
            elif tok in CLOSE:
                depth -= 1
            elif (not depth and tok in TAGS and i + 2 < len(stmt)
                  and is_ident(stmt[i+1]) and stmt[i+2] == '{'):
                defs.setdefault(tok + ' ' + stmt[i+1], stmt)
        if len(stmt) > 1 and stmt[0] == 'enum' and stmt[1] == '{':
            for i, tok in enumerate(stmt[2:]):
                if is_ident(tok) and stmt[i+1] in ['{', ',']:
                    defs.setdefault(tok, stmt)
    return defs

def referenced_types(tokens, defs):
    out = []
    for i, tok in enumerate(tokens):
        if tok in TAGS and i + 1 < len(tokens) and is_ident(tokens[i+1]):
            out.append(tok + ' ' + tokens[i+1])
        elif tok in defs:
            out.append(tok)
    return out

def calc_hash(tokens, defs):
    found = {}
    pending = referenced_types(tokens, defs)
    while pending:
        name = pending.pop()
        if name in found or name not in defs:
            continue
        found[name] = " ".join(defs[name])
        pending.extend(referenced_types(defs[name], defs))
    text = "\n".join([" ".join(tokens)] + sorted(set(found.values())))
    return hashlib.sha1(text.encode()).hexdigest()[:8]

# Extract the function prototypes from a gcc "-aux-info" file
def read_prototypes(filename):
    protos = {}
    f = open(filename, 'r')
    for line in f:
        m = re.match(r'/\*.*?\*/\s*(.*?)\s*(/\*.*)?$', line)
        if m is None:
            continue
        tokens = [t for t in TOKEN_RE.findall(m.group(1))
                  if t not in STORAGE]
        if '(' not in tokens:
            continue
        name = tokens[tokens.index('(') - 1]
        protos.setdefault(name, tokens)
    f.close()
    return protos

def main():
    usage = "%prog <preprocessed loader.c> <aux-info file> <output.json>"
    opts = optparse.OptionParser(usage)
    options, args = opts.parse_args()
    if len(args) != 3:
        opts.error("Incorrect arguments")
    ppfile, auxfile, outfile = args
    tokens = read_preprocessed(ppfile)
    defs = index_types(split_statements(tokens))
    protos = read_prototypes(auxfile)
    exports = re.findall(r'\b_loader_export_(\w+)\s*=', " ".join(tokens))
    symbols = {CORE_SYMBOL: calc_hash(CORE_TYPES, defs)}
    for name in exports:
        if name not in protos:
            error("Unable to find prototype of exported function '%s'"
                  % (name,))
        symbols[name] = calc_hash(protos[name], defs)
    f = open(outfile, 'w')
    f.write(json.dumps({'version': ABI_VERSION, 'symbols': symbols},
                       separators=(',', ':'), sort_keys=True))
    f.close()

if __name__ == '__main__':
    main()
