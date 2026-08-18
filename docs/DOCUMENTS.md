# Documents

Cerebro reads the documents you work in — Word, Excel, PowerPoint, PDF, CSV —
so you can ask about them, search them, and have it make edits for you.

## What it can do

| Format | Read | Edit |
|---|---|---|
| Word `.docx` | ✅ text, headings, tables | ✅ |
| Excel `.xlsx` | ✅ every sheet, values and headers | ✅ |
| PowerPoint `.pptx` | ✅ slide text | — |
| PDF | ✅ text (not scans) | — |
| CSV / TSV | ✅ | — |
| Text / Markdown | ✅ | — |

Old binary formats (`.doc`, `.xls`, `.ppt`) are not supported — save them as the
modern format and Cerebro reads them.

## How documents reach Cerebro

**The desktop watcher** notices what you have open:

```
python cerebro.py watch
```

It looks for the `~$name.docx` lock files Word and Excel create while a document
is open, and reads the active window title. Nothing is installed into Office.

**The browser extension** catches documents you open from SharePoint, OneDrive or
Office online. Cerebro finds the locally synced copy, so it can read and edit the
real file rather than a web view.

**Or point it at one directly:**

```bash
curl -X POST http://localhost:8000/api/documents/observe \
  -H "Content-Type: application/json" \
  -d '{"path": "C:/Users/you/Documents/case-notes.docx"}'
```

## SharePoint links

Cerebro does not call the Graph API. In a managed environment your libraries are
synced by OneDrive, so the file is already on disk — Cerebro takes the filename
out of the SharePoint URL and finds it in your sync roots.

Set those roots in **Settings → Documents → SharePoint / OneDrive sync roots**,
one per line:

```
C:\Users\you\Contoso Ltd
C:\Users\you\OneDrive - Contoso Ltd
```

If a link cannot be matched, Cerebro says which file it was looking for rather
than failing silently. Usually it means that library is not synced locally.

## Asking about a document

```bash
# Summarise
curl -X POST http://localhost:8000/api/documents/3/ask

# Ask something specific
curl -X POST http://localhost:8000/api/documents/3/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Which tickets are still open and who owns them?"}'
```

Needs an AI provider. Without one, Cerebro still extracts and searches document
text — it just cannot write prose about it.

To make a document searchable alongside your other knowledge:

```bash
curl -X POST http://localhost:8000/api/documents/3/index
```

## Editing

Edits are described as operations, not as a rewritten file. Three things follow
from that: you can see exactly what will change, the original is backed up, and
an edit that cannot be applied fails cleanly instead of half-applying.

**Always dry-run first.** It opens the real document and validates every
operation against its actual content — a sheet name typo or an out-of-range
paragraph fails here — but writes nothing.

```bash
curl -X POST http://localhost:8000/api/documents/3/edit \
  -H "Content-Type: application/json" \
  -d '{
    "dry_run": true,
    "operations": [
      {"op": "set_cell", "sheet": "Q3 Tickets", "cell": "E2", "value": "Closed"},
      {"op": "append_row", "sheet": "Q3 Tickets",
       "values": ["500BB2", "Tailwind", "High", 6, "Open"]}
    ]
  }'
```

Drop `"dry_run": true` to apply it.

### Word operations

| Operation | Arguments |
|---|---|
| `replace_text` | `find`, `replace` — covers body text and table cells |
| `append_paragraph` | `text`, optional `style` |
| `append_heading` | `text`, `level` (0-9) |
| `insert_paragraph` | `index`, `text` |
| `set_paragraph` | `index`, `text` |
| `delete_paragraph` | `index` |

### Excel operations

| Operation | Arguments |
|---|---|
| `set_cell` | `cell` (`"B7"`), `value`, optional `sheet` |
| `set_range` | `start` (`"A2"`), `values` (list of row lists) |
| `append_row` | `values` |
| `clear_cell` | `cell` |
| `add_sheet` | `title` |
| `rename_sheet` | `sheet`, `title` |

Values that look like numbers are stored as numbers, so Excel can compute with
them. A value starting with `=` is written as a formula.

### Safety

- **Backups.** Every edit copies the file to
  `name.cerebro-backup-<timestamp>.ext` beside it first. Turn this off in
  Settings → Documents if you would rather rely on version history.
- **Open files are refused.** If Word or Excel has the document open, the edit is
  rejected — editing underneath a running Office loses one side of the change.
  Close it and retry.
- **Failures roll back.** If an operation fails partway, the file is restored
  from the backup rather than left half-edited.
- **Nothing happens on its own.** Cerebro reads documents automatically; it only
  writes when something explicitly asks it to.

## Limits

- Documents over 25 MB are skipped (raise it in Settings → Documents).
- Scanned PDFs have no extractable text; Cerebro says so rather than returning
  an empty document. There is no OCR yet.
- Excel reads the first 400 rows per sheet for context. Edits are not limited.
- Word editing preserves paragraph formatting but rewrites the runs inside a
  changed paragraph, so mixed formatting *within* one paragraph is flattened.
