DE-IDENTIFIED RECORD SET — synthetic clinical text, no real PHI.

These fixtures represent de-identified faxed/scanned medical records (the
shape of n2c2 / i2b2 de-id challenge data) for the 10-minute demo and the
test suite. Every name, date, MRN, and location below is synthetic.

Contents:
  discharge_2024-01-15.txt   — discharge summary (the primary encounter record)
  consult_2024-02-20.txt     — specialist consult note (follow-up)
  medrecon_2024-02-20.txt    — medication reconciliation fax
  discharge_refax.txt        — a re-fax of the discharge summary (duplicate
                              content, slightly different whitespace) to
                              exercise content-hash de-duplication
  imaging_2024-01-10.txt     — imaging/lab result note

Run:  clinrec ingest ./sample-records
