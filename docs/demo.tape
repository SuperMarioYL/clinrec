# ClinRec demo tape — the 10-minute happy path (vhs / charmbracelet).
# Renders to assets/demo.gif (see .github/workflows/demo.yml).
# Drives: init → ingest → timeline (browse) → audit --export → eval.

Output assets/demo.gif

Set Shell "bash"
Set FontSize 13
Set Width 160
Set Height 48
Set Theme "Dracula"
Set Padding 20
Set TypingSpeed 4ms

# 0. version
Type "clinrec version"
Enter
Sleep 1s

# 1. init — detect ollama, load medspaCy, write clinrec.toml
Type "clinrec init"
Enter
Sleep 3s

# 2. ingest the de-identified n2c2-style sample records
#    (OCR + content-hash dedup + medspaCy NER + on-prem linker + assemble)
Type "clinrec ingest ./sample-records"
Enter
Sleep 4s

# 3. timeline TUI — browse de-duplicated events, drill into one, quit
Type "clinrec timeline"
Enter
Sleep 3s
Type "1"
Enter
Sleep 2s
Enter
Sleep 1s
Type "a"
Enter
Sleep 2s
Enter
Sleep 1s
Type "q"
Enter
Sleep 1s

# 4. audit export — the regulator-reviewable audit log (no PHI egress)
Type "clinrec audit --export audit.jsonl"
Enter
Sleep 2s

# 5. eval — the m1 killer-falsifier (on-prem vs medspaCy-only baseline)
Type "clinrec eval --gold tests/gold.jsonl"
Enter
Sleep 3s

# 6. show the audit log is real, local, JSON-lines
Type "head -n 2 audit.jsonl"
Enter
Sleep 2s
