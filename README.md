# SAIbuilder — Static Analysis Index for Pine Script & Beyond

A language-aware pre-processor that indexes source files once per conversation,
giving every downstream analysis and edit turn **O(1) lookup** instead of O(n) scanning.

---

## What It Does

SAIbuilder reads a source file and produces a **structured JSON index** containing:

- Full symbol table (variables, functions, UDTs, enums) with scope, type, qualifier, and all usage line numbers
- Pre-computed safe regex for every detected rename candidate
- Pattern catalogue: ring-buffers, duplicate `ta.*()` calls, unguarded nullables, raw barstate usages, dead code
- Budget counters: variable count per scope, `request.*()` call count, tuple element count
- Section map: line ranges for each logical section (INPUTS, CALCULATIONS, RENDERING, etc.)
- Flat list of flagged issues keyed by pine-guard rule ID (CE, RE, B, W, ARCH, LIB)

---

## Speed Gains

| Phase | Without SAI | With SAI |
|---|---|---|
| File location + read | 1–2 turns (3–6 calls) | 0 — loaded in bootstrap |
| Pattern reconnaissance | 1–2 calls per change | 0 — index has exact line + regex |
| Regex debugging | 1–2 calls per failure | 0 — pre-validated at index time |
| Applying a change | 1 call | 1 call |
| **10 changes total** | **30–50 turns** | **~4 turns** |

---

## Quickstart

### Bootstrap (1 tool call per conversation)

```python
import subprocess, sys

# Download SAIbuilder once per conversation
subprocess.run(["git", "clone", "https://github.com/cybermediaboy/SAIbuilder", "/tmp/sai"], check=True)
sys.path.insert(0, "/tmp/sai")

import sai
src, index = sai.bootstrap("MyScript.pine", lang="pine6")

# src  = full file content as string (in memory, ready to transform)
# index = SAIIndex object with .symbols, .patterns, .budget, .issues, .sections
print(index.summary())
```

### Apply a Change (1 call per change)

```python
from sai.core.edit_engine import apply_change

src = apply_change(src, index, "rename_cryptic_vars")
src = apply_change(src, index, "cache_duplicate_ta_calls")
src = apply_change(src, index, "add_nz_guards")
src = apply_change(src, index, "extract_ring_buffers")
src = apply_change(src, index, "unify_barstate")
```

### Push Result (1 call)

```python
with open("MyScript.pine", "w") as f:
    f.write(src)
# Then git push via your connector
```

---

## File Structure

```
SAIbuilder/
  builder.py              # CLI entry: python builder.py file.pine --lang pine6
  sai/
    __init__.py           # bootstrap() convenience function
    core/
      __init__.py
      index_schema.py     # SAIIndex dataclass + all sub-schemas
      tokenizer_base.py   # Language-agnostic line scanner + regex engine
      edit_engine.py      # apply_change() dispatcher
      verifier.py         # Post-edit consistency checks
    languages/
      __init__.py
      pine6.py            # Pine Script v6 — 83 rules encoded
  tests/
    test_pine6.py         # Unit tests
  specs/
    pine6-rules.md        # Language rules specification document
```

---

## Language Modules

The builder uses a plugin interface — swap the language module to analyse any file:

```
python builder.py file.pine    --lang pine6
python builder.py trading.py   --lang python
python builder.py Token.sol    --lang solidity
```

Each language module defines:
- Variable declaration patterns and scope classification rules
- Type qualifier inference rules
- Dangerous pattern signatures (unguarded nullables, duplicate calls, dead code)
- Rename safety constraints
- Budget limits and tracking rules

---

## Rule Coverage (pine6 module)

Encodes all 83 pine-guard rules across 7 tiers:

| Tier | Count | Examples |
|---|---|---|
| Compile Errors (CE) | 39 | CE10095 duplicate decl, CE10272 forward ref, CE10175 param reassign |
| Runtime Errors (RE) | 4 | RE10045 array OOB, ARRAY_LOOP_OOB empty array |
| Bugs (B) | 12 | B2 unguarded array access, B3 NA aggregation, B8 lazy-eval side effect |
| Warnings (W) | 2 | W001 request budget, W002 redundant security calls |
| Architecture (ARCH) | 8 | ARCH004 raw var array, ARCH005 tuple→UDT, ARCH006 triad pattern |
| Library (LIB) | 8 | LIB001 varip no default, LIB002 series→simple param |
| Other | 10 | Barstate normalisation, section docblocks, integer division |

---

## Specs

See [`specs/pine6-rules.md`](specs/pine6-rules.md) for the full language rules specification
that drove the pine6 module implementation.

---

## License

MIT
