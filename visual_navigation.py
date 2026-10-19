"""Bounded navigation from screenshots; every action needs two fresh observations."""
from dataclasses import dataclass
from pathlib import Path
import time

from top_heroes import Match, WorkflowError, WindowChanged, load_template


class PageUnavailable(WorkflowError):
    pass


@dataclass(frozen=True)
class Page:
    name: str
    action: str | None = None
    point: tuple[int, int] | None = None


class VisualNavigator:
    def __init__(self, bot):
        self.bot=bot
        self.assets={}
        for name,path,roi in (
            ('return_city','carriage/return_city.png',[.9,.85,1,1]),
            ('return_city_1600','navigation/return_city_1600.png',[.94,.90,1,1]),
            ('chat','chat/chat_header.png',[.33,0,.67,.86]),
            ('back','chat/chat_close.png',[.33,.9,.4,1]),
            ('carriage','carriage/trade_label.png',[.33,0,.67,.15]),
            ('search_panel','navigation/search_header.png',[.25,.2,.75,.65]),
            ('queue_collapsed','navigation/queue_collapsed.png',[.065,.16,.105,.23]),
        ):
            filename=Path(bot.base)/'templates'/path
            if filename.is_file():self.assets[name]=(load_template(filename),roi)

    def match(self,name,frame):
        from crystal_gather import find_matches
        if name not in self.assets:return None
        image,roi=self.assets[name]
        found=find_matches(frame,image,[1],.92,roi)
        if name=='return_city' and not found and 'return_city_1600' in self.assets:
            # The standard 1600px UI uses another rendering of the same label.
            # Keep the old threshold; verify this real-pixel variant separately.
            image,roi=self.assets['return_city_1600']
            found=find_matches(frame,image,[1],.96,roi)
        return found[0] if len(found)==1 else None

    def observe(self,frame):
        # Modal controls take precedence over navigation visible behind them.
        search=self.match('search_panel',frame)
        if search:
            return Page('资源搜索','关闭搜索弹窗',
                        (search.x-round(200*search.width/114),search.center[1]))
        for name,label in (('depart','出发面板'),('gather','采集面板')):
            found=self.bot.matches(name,frame)
            if len(found)==1:
                m=found[0]
                return Page(label,'关闭'+label,(m.x-round(1.2*m.width),m.center[1]))
        back=self.match('back',frame)
        if self.match('chat',frame):return Page('聊天','退出聊天',back.center if back else None)
        if self.match('carriage',frame):return Page('马车','退出马车',back.center if back else None)
        if back:return Page('返回界面','返回',back.center)
        if self.bot.matches('search_icon',frame):
            collapsed=self.match('queue_collapsed',frame)
            if collapsed:return Page('队伍列表收起','展开队伍列表',collapsed.center)
            return Page('世界地图')
        city=self.bot.matches('world',frame)
        if len(city)==1:return Page('城内','进入世界',city[0].center)
        world=self.match('return_city',frame)
        if world:return Page('世界入口暂不可读','回城重置',world.center)
        return Page('未知画面')

    def ensure_world(self, reset=False):
        bot=self.bot
        previous=None;generation=getattr(bot.session,'generation',0)
        deadline=time.monotonic()+max(8,bot.timeout*3)
        actions=0;last_action=None;last_at=0
        reset_pending=reset
        waiting_for_city=False
        reset_completed=False
        while time.monotonic()<deadline:
            bot.check_stop()
            frame=bot.session.capture()
            current_generation=getattr(bot.session,'generation',0)
            if current_generation!=generation:
                generation=current_generation;previous=None
            page=self.observe(frame)
            if page.name=='世界地图' and reset_pending:
                if waiting_for_city:
                    page=Page('等待回城完成')
                else:
                    home=self.match('return_city',frame)
                    page=(Page('世界地图','回城重置',home.center) if home else
                          Page('等待识别回城按钮'))
            key=(page.name,page.action,page.point)
            stable=(previous and previous[:2]==key[:2] and
                    ((previous[2] is None and key[2] is None) or
                     (previous[2] is not None and key[2] is not None and
                      max(abs(a-b) for a,b in zip(previous[2],key[2]))<=4)))
            if stable:
                if page.name=='城内' and (reset_pending or waiting_for_city):
                    reset_pending=False
                    waiting_for_city=False
                    reset_completed=True
                if page.name=='世界地图' and not page.action:
                    bot.log('页面确认：世界地图，继续检查队伍')
                    return reset_completed
                if page.point and page.action:
                    # A slow transition must not toggle city/world repeatedly.
                    if last_action==key and time.monotonic()-last_at<2:
                        bot.sleep(.2);continue
                    x,y=page.point
                    if not (0<=x<frame.shape[1] and 0<=y<frame.shape[0]):
                        raise PageUnavailable('页面恢复目标超出截图；未点击')
                    if actions>=8:break
                    bot.require_ready()
                    try:bot.session.click(page.point)
                    except WindowChanged:previous=None;continue
                    bot.log(f'页面恢复：{page.name} → {page.action}，位置={page.point}')
                    bot.emit('recovery',page=page.name,action=page.action)
                    if page.action=='回城重置':
                        reset_pending=True
                        waiting_for_city=True
                    actions+=1;last_action=key;last_at=time.monotonic()
                    previous=None;bot.sleep(.4);continue
            previous=key
            bot.sleep(.2)
        bot.emit('diagnostic',frame=frame,name='page_recovery',match=None,threshold=0)
        if reset and not reset_completed:
            raise PageUnavailable('回城重置尚未完成：未确认进入城内，不计为已恢复；未重复派队，请查看诊断截图')
        raise PageUnavailable('页面恢复未成功：已等待并尝试已确认的返回路径，请关闭未知弹窗后重试')
