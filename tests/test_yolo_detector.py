"""Click geometry and malformed model outputs must not produce stale coordinates."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from top_heroes import WorkflowError, WindowChanged
from yolo_detector import prepare_input, decode_output, YoloDetector
from yolo_detector import Detection
from top_heroes import Match


class CoordinateTests(unittest.TestCase):
    def test_full_frame_padding_is_removed_before_click(self):
        frame = np.zeros((900,1600,3),np.uint8)
        tensor,t = prepare_input(frame,(640,640))
        output=np.array([[[400,380,560,440,0.9,0]]],np.float32)
        d=decode_output(output,{0:"depart"},t,0.65)[0]
        self.assertEqual(d.match.center,(1200,675))
        self.assertEqual((d.match.x,d.match.y,d.match.width,d.match.height),(1000,600,400,150))
        self.assertEqual(tensor.shape,(1,3,640,640))

    def test_roi_offset_and_non_square_resize_are_restored(self):
        _,t=prepare_input(np.zeros((900,1600,3),np.uint8),(640,640),[0.25,0.4,0.75,0.8])
        output=np.array([[[80,216,160,256,0.95,0]]],np.float32)
        d=decode_output(output,{0:"gather"},t,0.65)[0]
        self.assertEqual(d.match.center,(550,435))

    def test_model_boxes_clip_to_crop_and_ignore_nan(self):
        _,t=prepare_input(np.zeros((100,100,3),np.uint8),(640,640))
        output=np.array([[[-30,-30,670,670,0.8,0],[0,0,10,10,np.nan,0]]],np.float32)
        boxes=decode_output(output,{0:"world"},t,0.65)
        self.assertEqual(len(boxes),1)
        self.assertEqual(boxes[0].match.center,(50,50))

    def test_raw_class_head_and_classwise_nms(self):
        _,t=prepare_input(np.zeros((100,100,3),np.uint8),(640,640))
        output=np.zeros((1,7,3),np.float32)
        output[0,:4,:]=np.array([[320,320,320],[320,320,320],[128,128,128],[128,128,128]])
        output[0,4:,:]=np.array([[0.9,0.8,0.1],[0.1,0.1,0.95],[0.1,0.1,0.1]])
        boxes=decode_output(output,{0:"gather",1:"depart",2:"world"},t,0.65)
        self.assertEqual({d.name for d in boxes},{"gather","depart"})
        self.assertEqual(len(boxes),2)

    def test_large_raw_head_filters_invalid_rows_without_changing_classes(self):
        _,t=prepare_input(np.zeros((100,100,3),np.uint8),(640,640))
        output=np.zeros((1,7,18900),np.float32)
        output[0,:4,:]=np.array([[100],[100],[40],[40]])
        # Most proposals are below threshold. Non-finite values anywhere in
        # a proposal must reject that proposal, including an unused class.
        output[0,4:,:]=.01
        output[0,4,3]=.9
        output[0,5,400]=.95;output[0,0,400]=500
        output[0,6,800]=.99;output[0,4,800]=np.nan
        output[0,6,900]=.99;output[0,0,900]=np.inf
        output[0,4,1000]=.8  # Duplicate of proposal3, suppressed by NMS.
        boxes=decode_output(output,{0:'gather',1:'depart',2:'world'},t,.65)
        self.assertEqual([d.name for d in boxes],['depart','gather'])
        self.assertEqual([d.match.center for d in boxes],[(78,15),(15,15)])
        self.assertEqual(decode_output(np.full_like(output,np.nan),
                                     {0:'gather',1:'depart',2:'world'},t,.65),[])

    def test_invalid_output_and_roi_stop(self):
        frame=np.zeros((100,100,3),np.uint8)
        with self.assertRaises(WorkflowError): prepare_input(frame,(640,640),[0,0,1.1,1])
        _,t=prepare_input(frame,(640,640))
        with self.assertRaises(WorkflowError): decode_output(np.zeros((1,8,10)),{0:"world"},t,0.65)

    def test_unverified_and_replaced_model_are_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            model=Path(folder)/"game.onnx"; model.write_bytes(b"test")
            detector=YoloDetector.__new__(YoloDetector); detector.path=model
            import hashlib
            detector.loaded_sha256=hashlib.sha256(model.read_bytes()).hexdigest()
            for info in [{"stage":"bootstrap"},{"stage":"verified","sha256":"wrong"}]:
                model.with_suffix('.json').write_text(json.dumps(info))
                with self.assertRaises(WorkflowError): detector.require_verified()

    def test_moving_window_invalidates_previous_frame(self):
        from crystal_gather import GameSession
        session=GameSession.__new__(GameSession)
        session.hwnd=123; session.size=(1600,900); session.generation=0
        session.capture_box=(10,20,1610,920); session.ensure_size=lambda:False
        with patch('crystal_gather.client_box',return_value=(20,30,1620,930)), patch('crystal_gather.click') as click:
            with self.assertRaises(WindowChanged): session.click((400,300))
            click.assert_not_called()
        self.assertEqual(session.generation,1)

    def test_navigation_uses_trained_crop_and_restored_client_center(self):
        from yolo_detector import NAVIGATION_ROIS
        detector=YoloDetector.__new__(YoloDetector)
        calls=[]
        def detect(frame,roi):
            calls.append(roi)
            return [Detection('world',Match(.9,1515,796,80,80))]
        detector.detect=detect
        matches=detector.matches('world',np.zeros((900,1600,3),np.uint8),[.7,.65,1,1])
        self.assertEqual(calls,[NAVIGATION_ROIS['world']])
        self.assertEqual(matches[0].center,(1555,836))

    def test_old_verified_model_cannot_enable_new_navigation_path(self):
        from yolo_detector import require_verified_model
        import hashlib
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'game.onnx';p.write_bytes(b'test')
            info={'stage':'verified','sha256':hashlib.sha256(b'test').hexdigest()}
            p.with_suffix('.json').write_text(json.dumps(info))
            with self.assertRaises(WorkflowError):require_verified_model(p)
            info['navigation_roi_version']=1
            p.with_suffix('.json').write_text(json.dumps(info))
            require_verified_model(p)

    def test_navigation_augmentation_never_uses_validation_or_test_parents(self):
        from yolo_dataset import augment_navigation,save_boxes
        import cv2
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'images').mkdir();(root/'labels').mkdir()
            manifest={}
            for i,split in enumerate(['train','train','train','train','val','test']):
                name=f'frame{i}.png'
                frame=np.full((900,1000,3),i,np.uint8)
                cv2.imwrite(str(root/'images'/name),frame)
                save_boxes(root/'labels'/f'frame{i}.txt',[['world',900,780,980,860]],1000,900)
                manifest[name]={'reviewed':True,'split':split,'source':name}
            (root/'review.json').write_text(json.dumps(manifest))
            self.assertEqual(augment_navigation(root),8)
            result=json.loads((root/'review.json').read_text())
            children=[m for m in result.values() if m.get('parent')]
            self.assertTrue(all(m['split']=='train' and result[m['parent']]['split']=='train' for m in children))
            self.assertEqual(result['frame4.png']['split'],'val')
            self.assertEqual(result['frame5.png']['split'],'test')

    def test_yolo_returning_badge_is_not_shared_between_rows(self):
        from queue_monitor import QueueReader
        class Detector:
            def detect(self,frame,roi):
                return [Detection('queue_header',Match(.95,20,160,35,16)),
                        *[Detection('timer_region',Match(.95,60,y,70,31)) for y in [194,230,266]],
                        Detection('queue_returning',Match(.95,30,212,18,18)),
                        Detection('queue_mining',Match(.95,30,248,18,18)),
                        Detection('queue_mining',Match(.95,30,284,18,18))]
        reader=QueueReader.__new__(QueueReader);reader.detector=Detector()
        class Timer:
            def read(self,label):return None
        reader.timer=Timer()
        rows=reader.read_yolo(np.zeros((900,1600,3),np.uint8))
        self.assertEqual([r.state for r in rows],['returning','mining','mining'])

    def test_level_text_confirms_six_and_rejects_live_seven(self):
        from crystal_gather import CrystalGatherer
        from top_heroes import load_template
        root=Path(__file__).resolve().parents[1]
        bot=CrystalGatherer.__new__(CrystalGatherer)
        bot.detector=object()
        bot.templates={'level_six':load_template(root/'templates/level_six.png')}
        bot.specs={'level_six':{'confidence':.96}}
        bot.scales=[.9,.95,1,1.05,1.1]
        bot.level_region=lambda frame,plus:frame
        # Even an erroneous YOLO six-class prediction must not authorize search.
        bot.matches=lambda *args: [Match(.99,0,0,10,10)]
        for level,expected in [(6,True),(7,False)]:
            frame=load_template(root/f'tests/fixtures/level_{level}_live.png')
            self.assertEqual(bot.is_six(frame,None),expected)

    def test_bright_background_header_uses_pixels_without_inventing_idle_rows(self):
        from queue_monitor import QueueReader
        from top_heroes import load_template
        root=Path(__file__).resolve().parents[1]
        frame=np.zeros((900,1600,3),np.uint8)
        frame[140:300,:160]=load_template(root/'tests/fixtures/queue_header_bright_background.png')
        items=[Detection('idle',Match(.921,67,204,43,14)),
               Detection('timer_region',Match(.876,57,231,69,25)),
               Detection('timer_region',Match(.864,55,267,71,26)),
               Detection('queue_mining',Match(.966,28,242,17,17)),
               Detection('queue_mining',Match(.967,28,278,17,17))]
        class Detector:
            def detect(self,frame,roi):return items
        reader=QueueReader(root,load_template(root/'templates/idle.png'),Detector())
        rows=reader.read_yolo(frame)
        self.assertEqual([r.state for r in rows],['idle','mining','mining'])
        self.assertEqual([r.seconds for r in rows],[None,1143,1427])
        self.assertIsNone(reader.read_yolo(np.zeros_like(frame)))
        items.pop(0)  # A folded/hidden row is never assumed idle.
        self.assertIsNone(reader.read_yolo(frame))

    def test_idle_calibration_does_not_lower_other_classes_or_full_frame_threshold(self):
        from yolo_detector import QUEUE_ROI
        from unittest.mock import Mock
        detector=YoloDetector.__new__(YoloDetector)
        detector._frame=None;detector._cache={};detector.size=(960,960)
        detector.confidence=.65;detector.input_name='images'
        detector.names={10:'idle',12:'queue_mining',15:'timer_region'}
        output=np.array([[[100,300,200,350,.647,10],
                          [100,400,200,450,.62,12],
                          [100,500,200,550,.62,15]]],np.float32)
        detector.session=Mock();detector.session.run.return_value=[output]
        frame=np.zeros((900,1600,3),np.uint8)
        self.assertEqual([d.name for d in detector.detect(frame,QUEUE_ROI)],['idle'])
        self.assertEqual(detector.detect(frame),[])

    def test_paired_queue_model_must_be_verified_and_cannot_be_used_for_buttons(self):
        from yolo_detector import require_verified_model,linked_queue_model
        import hashlib
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'topheroes.onnx';p.write_bytes(b'buttons')
            info={'stage':'verified','navigation_roi_version':1,
                  'sha256':hashlib.sha256(b'buttons').hexdigest(),'queue_model':'topheroes-queue.onnx'}
            p.with_suffix('.json').write_text(json.dumps(info))
            with self.assertRaises(WorkflowError):require_verified_model(p)
            q=p.with_name('topheroes-queue.onnx');q.write_bytes(b'queues')
            qi={'stage':'verified','navigation_roi_version':1,'role':'queue',
                'sha256':hashlib.sha256(b'queues').hexdigest()}
            q.with_suffix('.json').write_text(json.dumps(qi))
            require_verified_model(p)
            with self.assertRaises(WorkflowError):require_verified_model(q)
            q.write_bytes(b'replaced')
            with self.assertRaises(WorkflowError):require_verified_model(p)

    def test_queue_profile_routes_only_queue_crop_to_separate_detector(self):
        from yolo_detector import QUEUE_ROI
        from unittest.mock import Mock
        detector=YoloDetector.__new__(YoloDetector)
        detector.queue_detector=Mock();detector.queue_detector.detect.return_value=['queue results']
        frame=np.zeros((900,1600,3),np.uint8)
        self.assertEqual(detector.detect(frame,QUEUE_ROI),['queue results'])
        detector.queue_detector.detect.assert_called_once_with(frame,QUEUE_ROI)


if __name__=='__main__': unittest.main()
