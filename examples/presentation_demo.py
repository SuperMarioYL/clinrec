"""Run a constructed local TXT-to-timeline example with the rule-based linker."""
import json
from pathlib import Path
import socket
import tempfile
from clinrec.audit import AuditChain
from clinrec.ingest import ingest_folder
from clinrec.llm import Linker
from clinrec.resolve import EntityExtractor
from clinrec.timeline import TimelineAssembler

with tempfile.TemporaryDirectory(prefix='clinrec-demo-') as directory, socket.socket() as unavailable:
    root=Path(directory)
    (root/'01-note.txt').write_text('2024-01-15. Diabetes. Metformin.\n')
    (root/'02-copy.txt').write_text('2024-01-15.  DIABETES.  Metformin.\n')
    (root/'03-followup.txt').write_text('2024-02-20. No pneumonia.\n')
    records,dedup=ingest_folder(root)
    # Reserve a loopback port without listening: the real linker's availability
    # probe cannot reach a model and follows its implemented fallback path.
    unavailable.bind(('127.0.0.1',0))
    linker=Linker(host=f'http://127.0.0.1:{unavailable.getsockname()[1]}')
    extractor=EntityExtractor();audit=AuditChain()
    for record in records:
        audit.record(op='ingest',input_sha256=record.file_sha256,llm_model_id='text',output_sha256=record.raw_text_sha256)
    asm=TimelineAssembler(extractor=extractor,linker=linker,audit=audit)
    result=asm.assemble(records,patient_pseudonym='fictional-example')
    output={'records':len(records),'duplicates_skipped':dedup.duplicates_seen,
            'ner_engine':'medspacy' if extractor.uses_medspacy else 'regex',
            'model_reachable':linker.is_available(),'events':len(result.events),
            'linker_modes':sorted({a.llm_model_id for a in result.audit_chain if a.op=='link'}),
            'audit_entries':len(result.audit_chain),'chain_integrity':asm.audit.verify_chain_integrity()}
    print(json.dumps(output,indent=2))
    assert len(records)==2 and dedup.duplicates_seen==1 and not output['model_reachable']
