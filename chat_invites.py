"""Local pixels only: stay in alliance chat and join merchant invitations."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import time

import cv2

from top_heroes import Match, WorkflowError, WindowChanged, load_template


class ChatPageChanged(WorkflowError):
    pass


class MiningAlarmTriggered(Exception):
    """Interrupt a visual confirmation when the mining alarm is due."""
    pass


@dataclass(frozen=True)
class Invite:
    button: Match
    key: str
    card: tuple[int, int, int, int]


class ChatImages:
    def __init__(self, base: Path, preview: bool = False, model: Path | None = None):
        self.base = base
        self.detector=None
        if model is not None:
            from yolo_detector import YoloDetector
            info=json.loads(model.with_suffix('.json').read_text(encoding='utf-8'))
            digest=hashlib.sha256(model.read_bytes()).hexdigest()
            if (info.get('stage')!='verified' or info.get('role')!='chat' or
                    info.get('sha256')!=digest or info.get('validated_classes')!=['merchant_join']):
                raise WorkflowError('商队模型尚未验证；未点击邀请')
            self.detector=YoloDetector(model)
        manifest = base / 'templates/chat/assets.json'
        self.info = json.loads(manifest.read_text(encoding='utf-8'))
        if self.info.get('stage') != 'verified' and not preview:
            raise WorkflowError('聊天邀请图像尚未验证；关闭自动加入商队后可单独采矿')
        self.templates = {}
        for name, spec in self.info['assets'].items():
            path = base / 'templates/chat' / spec['file']
            if hashlib.sha256(path.read_bytes()).hexdigest() != spec['sha256']:
                raise WorkflowError(f'聊天图像已改变：{name}')
            self.templates[name] = load_template(path)

    def matches(self, name, frame, roi=None):
        from crystal_gather import find_matches
        spec = self.info['assets'][name]
        template=self.templates[name]
        return find_matches(frame, template, self.info.get('scales',[1]),
                            spec.get('confidence',.94), roi or spec.get('roi'))

    def visible(self, frame):
        return bool(self.matches('chat_header',frame) and self.matches('alliance_active',frame)
                    and self.matches('alliance_chat',frame))

    def invitations(self, frame):
        if not self.visible(frame):
            return []
        height,width=frame.shape[:2]
        layout=self.info['card_layout']
        results=[]
        for heading in self.matches('invite_header',frame):
            types=[m for m in self.matches('merchant',frame) if
                   abs(m.y-heading.y)<=5 and layout['type_dx_min']<=m.x-heading.x<=layout['type_dx_max']]
            if len(types)!=1:
                continue
            merchant=types[0]
            x0=max(0,heading.x-layout['left_pad']);x1=min(width,merchant.x+merchant.width+layout['right_pad'])
            y0=max(0,heading.y-layout['top_pad']);y1=min(height,y0+layout['height'])
            # Both title labels and the red card are required. A random blue
            # join button elsewhere in chat cannot become a merchant action.
            patch=frame[heading.y:heading.y+heading.height,x0:x1]
            if not patch.size or ((patch[:,:,2].astype(int)-patch[:,:,0])>25).mean()<.45:
                continue
            roi=[x0/width,(y0+layout['button_min_y'])/height,x1/width,y1/height]
            if not 0<=roi[1]<roi[3]<=1:
                continue
            if self.matches('joined',frame,roi):
                continue
            buttons=self.matches('join',frame,roi)
            if len(buttons)!=1:
                continue
            button=buttons[0]
            if getattr(self,'detector',None):
                # Use the trained full-client scale, then retain only detections
                # corroborated by this red card and its actual green button.
                joins=[d.match for d in self.detector.detect(frame) if d.name=='merchant_join'
                       and abs(d.match.center[0]-button.center[0])<=5
                       and abs(d.match.center[1]-button.center[1])<=5]
                if len(joins)!=1:continue
                button=joins[0]
            # Identify the static hero/reward region, excluding ticking timers.
            identity=frame[heading.y+heading.height:y0+layout['identity_end_y'],x0:x1]
            if not identity.size:
                continue
            small=cv2.resize(identity,(32,12),interpolation=cv2.INTER_AREA)//32
            key=hashlib.sha256(small.tobytes()).hexdigest()
            results.append(Invite(button,key,(x0,y0,x1,y1)))
        return results


class ChatWatcher:
    def __init__(self, bot):
        self.bot=bot
        main_model=bot.config.get('yolo_model')
        model=Path(main_model).with_name('topheroes-chat.onnx') if main_model else None
        self.images=ChatImages(bot.base,model=model if model and model.is_file() else None)
        self.attempted=set()
        self.previous=None
        self.joined=0
        self.quota_dirty=False
        self.alarm_deadline=None
        self.alarm_queue=None

    def check_alarm(self):
        self.bot.check_stop()
        deadline=getattr(self,'alarm_deadline',None)
        if deadline is not None and time.monotonic()>=deadline:
            raise MiningAlarmTriggered()

    def pause(self,seconds):
        self.check_alarm()
        deadline=getattr(self,'alarm_deadline',None)
        if deadline is not None:seconds=min(seconds,max(0,deadline-time.monotonic()))
        self.bot.sleep(seconds)
        self.check_alarm()

    @staticmethod
    def all_mining(rows,capacity):
        return capacity==4 and len(rows)==4 and all(r.state=='mining' and r.seconds is not None and r.seconds>5 for r in rows)

    def click_asset(self,name):
        deadline=time.monotonic()+self.bot.timeout
        previous=None
        while True:
            self.check_alarm()
            frame=self.bot.session.capture()
            self.check_alarm()
            found=self.images.matches(name,frame)
            match=found[0] if len(found)==1 else None
            if match and previous and abs(match.x-previous.x)<4 and abs(match.y-previous.y)<4:
                self.bot.require_ready()
                self.check_alarm()
                try:
                    offset=self.images.info['assets'][name].get('click_offset',[0,0])
                    point=tuple(c+d for c,d in zip(match.center,offset))
                    self.bot.session.click(point)
                except WindowChanged:
                    previous=None
                    continue
                self.bot.log(f'聊天点击 {name}: score={match.score:.3f}, center={point}')
                self.pause(.15)
                return
            if time.monotonic()>=deadline:
                from crystal_gather import TemplateTimeout
                raise TemplateTimeout(name,frame,f'未确认聊天控件 {name}；未点击')
            previous=match
            self.pause(.15)

    def close_if_open(self):
        frame=self.bot.session.capture()
        if self.images.matches('chat_header',frame):
            self.click_asset('chat_close')
            deadline=time.monotonic()+self.bot.timeout
            while self.images.matches('chat_header',self.bot.session.capture()):
                if time.monotonic()>=deadline:
                    raise ChatPageChanged('聊天没有关闭；未继续派队')
                self.pause(.15)

    def enter(self):
        frame=self.bot.session.capture()
        if not self.images.matches('chat_header',frame):
            self.click_asset('chat_open')
        if not self.images.visible(self.bot.session.capture()):
            self.click_asset('alliance_tab')
        if not self.images.matches('alliance_chat',self.bot.session.capture()):
            self.click_asset('alliance_chat_tab')
        if not self.images.visible(self.bot.session.capture()):
            raise ChatPageChanged('未确认公会聊天频道；未点击邀请')
        self.previous=None
        self.bot.log('四队采矿：常驻公会聊天，监测红色商队邀请')
        self.bot.emit('chat_watch', joined=self.joined)
        if getattr(self,'alarm_deadline',None) is not None:
            self.bot.emit('chat_alarm',queue=self.alarm_queue,deadline=self.alarm_deadline)

    def poll(self):
        self.check_alarm()
        frame=self.bot.session.capture()
        self.check_alarm()
        if not self.images.visible(frame):
            raise ChatPageChanged('聊天频道画面改变；暂停加入')
        for name in ('new_messages_bottom','new_messages','new_messages_top'):
            if name in self.images.templates and self.images.matches(name,frame):
                self.click_asset(name)
                self.previous=None
                return
        candidates=[item for item in self.images.invitations(frame) if item.key not in self.attempted]
        self.check_alarm()
        current=candidates[-1] if candidates else None
        previous=self.previous
        self.previous=current
        if not current or not previous or current.key!=previous.key or any(
                abs(a-b)>4 for a,b in zip(current.button.center,previous.button.center)):
            return
        self.bot.require_ready()
        self.check_alarm()
        try:
            self.bot.session.click(current.button.center)
        except WindowChanged:
            self.previous=None
            return
        self.attempted.add(current.key)
        self.quota_dirty=True
        self.previous=None
        self.bot.log(f'商队邀请：点击加入，center={current.button.center}')
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            self.pause(.2)
            fresh=self.bot.session.capture()
            self.check_alarm()
            if not self.images.visible(fresh):
                raise ChatPageChanged('加入后打开了其他界面；不重复点击加入')
            x0,y0,x1,y1=current.card;h,w=fresh.shape[:2]
            if self.images.matches('joined',fresh,[x0/w,y0/h,x1/w,y1/h]):
                self.joined+=1
                self.bot.log(f'商队邀请：已从图像确认加入，累计{self.joined}次')
                self.bot.emit('chat_watch',joined=self.joined)
                if getattr(self,'alarm_deadline',None) is not None:
                    self.bot.emit('chat_alarm',queue=self.alarm_queue,deadline=self.alarm_deadline)
                return
        self.bot.log('加入结果未确认；该卡片不重复点击，继续监测新邀请')

    def wait(self,rows,observed_at=None):
        if not self.all_mining(rows,self.bot.capacity):return 'not_ready'
        observed_at=time.monotonic() if observed_at is None else observed_at
        first=min(rows,key=lambda row:row.seconds)
        # Keep a single absolute deadline, including time spent opening chat,
        # checking buttons and confirming joins. Leave five seconds for exit.
        deadline=observed_at+first.seconds-5
        if deadline<=time.monotonic():return 'alarm'
        self.alarm_deadline=deadline;self.alarm_queue=first.number
        # With the rescue watchdog enabled, revisit the world periodically to
        # prove the hidden mining timers still move. Chat screenshots alone
        # cannot establish collection progress or extend the rescue deadline.
        from rescue_watchdog import ProgressWatchdog
        watchdog = getattr(self.bot, 'rescue_watchdog', None)
        queue_recheck_at = (time.monotonic()+min(120, watchdog.seconds/2)
                            if isinstance(watchdog, ProgressWatchdog) else deadline)
        self.bot.log(f'马车监测闹钟：{first.number}队最先完成采集，{max(0,deadline-time.monotonic()):.1f}秒后退出；期间持续截图监测')
        self.bot.emit('monitor',rows=rows,delay=max(0,deadline-time.monotonic()),
                      observed_at=observed_at,deadline=deadline,completed=self.bot.completed)
        reason='alarm'
        try:
            self.check_alarm()
            self.enter()
            self.quota_dirty=False
            generation=getattr(self.bot.session,'generation',0)
            while True:
                self.check_alarm()
                if time.monotonic() >= queue_recheck_at:
                    reason='queue_recheck';break
                self.poll()
                if self.quota_dirty:
                    reason='quota';break
                if getattr(self.bot.session,'generation',0)!=generation:
                    reason='window_changed';break
                self.pause(.3)
        except MiningAlarmTriggered:
            self.bot.log(f'马车监测闹钟到时：退出聊天，复查{first.number}队采集与返程状态')
        finally:
            # Exiting chat must remain possible once the alarm has fired.
            self.alarm_deadline=None
        self.close_if_open()
        return reason
