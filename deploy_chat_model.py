from pathlib import Path
import shutil,json,hashlib
root=Path(__file__).resolve().parent
for evidence in ('merchant-verification.json','chat-card-verification.json'):
 if not (root/'yolo-output'/evidence).is_file():raise ValueError('Missing verification')
path=root/'models/topheroes-chat.onnx'
shutil.copy2(root/'models/topheroes-merchant-candidate.onnx',path)
info=json.loads((root/'models/topheroes-merchant-candidate.json').read_text(encoding='utf-8'))
info.update(stage='verified',role='chat',sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
 validated_classes=['merchant_join'],verification='yolo-output/merchant-verification.json; yolo-output/chat-card-verification.json',
 note='Join button verified on held-out user crop and synthetic full-client placement; red-card semantics require reviewed pixel templates. Live joined result not yet verified. Other classes are not deployed.')
path.with_suffix('.json').write_text(json.dumps(info,indent=2),encoding='utf-8')
