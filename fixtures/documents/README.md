# Synthetic document fixtures

Plain-text stand-ins for scanned documents. Every file is fictional and says so on its
first line. They exercise the extraction and review paths:

| File | Exercises |
|---|---|
| `fd_receipt.txt` | Matches the chat FD; **no nominee line**, so nomination stays unknown |
| `fd_receipt_conflicting.txt` | Same FD, different principal: a conflict the owner must resolve |
| `mf_statement.txt` | Statement date, SIP kept separate from value, "not registered" nominee |
| `policy_schedule.txt` | Cover amount; illegible nominee section goes to review |
| `handwritten_inventory.txt` | Low-quality OCR ("l0cker", "12,4O,000"): always human review |
| `birth_certificate.txt`, `death_certificate.txt` | Not asset documents: classified and sent to human review; never change handover state |
| `death_certificate_mismatch.txt` | Name differs from the owner record ("Patil"): reviewers should hold the case |
| `injection_receipt.txt` | Embedded instructions; must not invoke tools or change anything beyond extracted fields |
