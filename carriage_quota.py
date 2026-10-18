"""Read today's remaining trade attempts from the visible carriage page."""
from pathlib import Path
import hashlib
import json
import time

import cv2
import numpy as np

from queue_monitor import TimerReader, text_glyphs, normalize_glyph
from top_heroes import WorkflowError, WindowChanged, load_template


class CarriageImages:
    def __init__(self, base):
        directory=Path(base)/'templates/carriage'
        self.info=json.loads((directory/'assets.json').read_text(encoding='utf-8'))
        self.templates={}
        for name,spec in self.info['assets'].items():
            path=directory/spec['file']
            if hashlib.sha256(path.read_bytes()).hexdigest()!=spec['sha256']:
                raise WorkflowError(f'马车图像已改变：{name}')
            self.templates[name]=load_template(path)
        self.digits=TimerReader(Path(base)/'templates/timer_digits.npz')

    def matches(self,name,frame):
        from crystal_gather import find_matches
        spec=self.info['assets'][name]
        return find_matches(frame,self.templates[name],spec.get('scales',[1]),
                            spec.get('confidence',.94),spec.get('roi'))

    def read(self,frame):
        labels=self.matches('trade_label',frame)
        if len(labels)!=1:return None
        m=labels[0]
        x=m.x+m.width
        glyphs=text_glyphs(frame[m.y:m.y+m.height,x:x+40])
        if len(glyphs)!=3:return None
        slash=glyphs[1]
        h,w=slash.shape
        if not (3<=w<=5 and 9<=h<=14):return None
        ys,xs=np.nonzero(slash)
        if len(xs)<8 or np.corrcoef(ys,xs)[0,1]>-.6:return None
        values=[]
        for glyph in (glyphs[0],glyphs[2]):
            h,w=glyph.shape
            if not (4<=w<=10 and 8<=h<=15):return None
            scores=1-np.mean((self.digits.samples-normalize_glyph(glyph))**2,axis=(1,2))
            by_digit=np.array([scores[self.digits.labels==d].max() for d in range(10)])
            order=np.argsort(by_digit)
            if by_digit[order[-1]]<.93 or by_digit[order[-1]]-by_digit[order[-2]]<.012:return None
            values.append(int(order[-1]))
        remaining,total=values
        return (remaining,total) if total==4 and 0<=remaining<=total else None


class CarriageQuota:
    def __init__(self,bot):
        self.bot=bot
        self.images=CarriageImages(bot.base)

    def click(self,name):
        previous=None;deadline=time.monotonic()+self.bot.timeout
        generation=getattr(self.bot.session,'generation',0)
        while time.monotonic()<deadline:
            self.bot.check_stop()
            frame=self.bot.session.capture()
            now=getattr(self.bot.session,'generation',0)
            if now!=generation:previous=None;generation=now
            found=self.images.matches(name,frame)
            match=found[0] if len(found)==1 else None
            if match and previous and abs(match.x-previous.x)<4 and abs(match.y-previous.y)<4:
                self.bot.require_ready()
                try:self.bot.session.click(match.center)
                except WindowChanged:previous=None;continue
                self.bot.log(f'马车点击 {name}: score={match.score:.3f}, center={match.center}')
                self.bot.sleep(.2)
                return
            previous=match;self.bot.sleep(.15)
        from crystal_gather import TemplateTimeout
        raise TemplateTimeout(name,frame,f'未确认马车控件 {name}；未点击')

    def check(self):
        # The loot counter on "other merchants" is never a trade counter.
        frame=self.bot.session.capture()
        if not self.images.matches('trade_label',frame):
            if self.images.matches('my_tab',frame):self.click('my_tab')
            else:
                if self.images.matches('return_city',frame):self.click('return_city')
                if not self.images.matches('carriage_menu',self.bot.session.capture()):
                    self.click('carriage_building')
                self.click('carriage_menu')
        previous=None;result=None;deadline=time.monotonic()+self.bot.timeout
        generation=getattr(self.bot.session,'generation',0)
        while time.monotonic()<deadline:
            self.bot.check_stop()
            frame=self.bot.session.capture()
            now=getattr(self.bot.session,'generation',0)
            if now!=generation:previous=None;generation=now
            current=self.images.read(frame)
            if current is not None and current==previous:result=current;break
            previous=current;self.bot.sleep(.15)
        self.click('back')
        deadline=time.monotonic()+self.bot.timeout
        while not self.bot.matches('world'):
            self.bot.check_stop()
            if time.monotonic()>=deadline:
                from crystal_gather import TemplateTimeout
                raise TemplateTimeout('carriage_back',self.bot.session.capture(),'马车页未关闭；未继续操作')
            self.bot.sleep(.15)
        self.bot.log(f'马车剩余贸易次数：{result[0]}/{result[1]}' if result else '马车次数无法稳定识别；本次只采矿')
        self.bot.emit('carriage_quota',remaining=result[0] if result else None)
        return result[0] if result else None
