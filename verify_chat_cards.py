from pathlib import Path
import json
from yolo_dataset import read_image
from yolo_detector import YoloDetector
from chat_invites import ChatImages

root=Path(__file__).resolve().parent
background=read_image(root/'captures/chat-invite-20261014/01-guild-joined-invite.jpg')[31:931,1:1601]
images=ChatImages(root)
detector=YoloDetector(root/'models/topheroes-merchant-candidate.onnx')
report={}
for name in ('merchant-join-user1','merchant-join-user2'):
 card=read_image(root/f'datasets/topheroes/images/{name}.png')
 frame=background.copy();h,w=card.shape[:2]
 frame[380:380+h,557:557+w]=card
 invites=images.invitations(frame)
 joins=[d.match for d in detector.detect(frame) if d.name=='merchant_join']
 print(name,'templates',invites,'yolo',joins)
 assert len(invites)==len(joins)==1
 assert max(abs(a-b) for a,b in zip(invites[0].button.center,joins[0].center))<=5
 report[name]={'template_center':invites[0].button.center,'yolo_center':joins[0].center,'score':joins[0].score}
(root/'yolo-output/chat-card-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
