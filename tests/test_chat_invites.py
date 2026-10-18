"""Merchant join requires the card, the actionable button, and stable pixels."""
import unittest
from unittest.mock import Mock,patch
import numpy as np

from chat_invites import ChatImages,ChatWatcher,Invite
from queue_monitor import QueueRow
from top_heroes import Match,WindowChanged,WorkflowError


class ChatTests(unittest.TestCase):
    def images(self,joined=False,merchant=True):
        images=ChatImages.__new__(ChatImages)
        images.info={'card_layout':{'type_dx_min':100,'type_dx_max':220,'left_pad':5,
            'right_pad':5,'top_pad':5,'height':230,'button_min_y':160,'identity_end_y':140}}
        matches={'chat_header':[Match(.99,50,0,40,20)],'alliance_active':[Match(.99,50,20,40,20)],
                 'alliance_chat':[Match(.99,50,30,40,20)],
                 'invite_header':[Match(.99,100,50,35,20)],
                 'merchant':[Match(.99,270,50,35,20)] if merchant else [],
                 'join':[Match(.99,150,235,80,30)],
                 'joined':[Match(.99,150,235,80,30)] if joined else []}
        images.matches=lambda name,*args:matches.get(name,[])
        return images

    def test_only_red_merchant_card_with_actionable_join_is_selected(self):
        frame=np.zeros((400,400,3),np.uint8);frame[:,:,2]=200
        self.assertEqual(len(self.images().invitations(frame)),1)
        self.assertEqual(self.images(joined=True).invitations(frame),[])
        self.assertEqual(self.images(merchant=False).invitations(frame),[])
        self.assertEqual(self.images().invitations(np.zeros_like(frame)),[])

    def test_all_four_must_be_mining_with_confirmed_timers(self):
        rows=[QueueRow(i,'mining',30) for i in range(1,5)]
        self.assertTrue(ChatWatcher.all_mining(rows,4))
        rows[3]=QueueRow(4,'returning',20)
        self.assertFalse(ChatWatcher.all_mining(rows,4))
        rows[3]=QueueRow(4,'mining')
        self.assertFalse(ChatWatcher.all_mining(rows,4))

    def test_real_joined_card_never_becomes_an_actionable_invite(self):
        from pathlib import Path
        from yolo_dataset import read_image
        root=Path(__file__).resolve().parents[1]
        sample=root/'captures/chat-invite-20261014/01-guild-joined-invite.jpg'
        if not sample.is_file():self.skipTest('Local screenshot fixture not available')
        images=ChatImages(root,preview=True)
        frame=read_image(sample)[31:931,1:1601]
        self.assertTrue(images.visible(frame))
        self.assertTrue(images.matches('joined',frame))
        self.assertEqual(images.invitations(frame),[])

    def test_both_user_green_cards_match_inside_full_chat(self):
        from pathlib import Path
        from yolo_dataset import read_image
        root=Path(__file__).resolve().parents[1]
        images=ChatImages(root,model=root/'models/topheroes-chat.onnx')
        background=read_image(root/'captures/chat-invite-20261014/01-guild-joined-invite.jpg')[31:931,1:1601]
        for name in ('merchant-join-user1','merchant-join-user2'):
            card=read_image(root/f'datasets/topheroes/images/{name}.png')
            frame=background.copy();h,w=card.shape[:2];frame[380:380+h,557:557+w]=card
            invites=images.invitations(frame)
            self.assertEqual(len(invites),1)
            self.assertTrue(710<invites[0].button.center[0]<850)

    def watcher(self):
        w=ChatWatcher.__new__(ChatWatcher)
        w.bot=Mock();w.bot.session.capture.return_value=np.zeros((900,1600,3),np.uint8)
        w.images=Mock();w.images.visible.return_value=True
        w.images.templates={}
        w.images.invitations.return_value=[Invite(Match(.99,700,400,100,40),'same',(650,250,950,450))]
        w.images.matches.return_value=[Match(.99,700,400,100,40)]
        w.attempted=set();w.previous=None;w.joined=0
        return w

    def test_two_frames_and_joined_confirmation_then_no_duplicate(self):
        w=self.watcher();w.poll();w.bot.session.click.assert_not_called()
        w.poll();w.bot.session.click.assert_called_once_with((750,420))
        self.assertEqual(w.joined,1)
        w.poll();w.poll();self.assertEqual(w.bot.session.click.call_count,1)

    def test_window_move_does_not_mark_a_join_or_reuse_previous_coordinates(self):
        w=self.watcher();w.bot.session.click.side_effect=WindowChanged('moved')
        w.poll();w.poll()
        self.assertFalse(w.attempted);self.assertIsNone(w.previous)
        self.assertEqual(w.joined,0)

    def test_changed_channel_after_join_stops_without_clicking_again(self):
        w=self.watcher();w.images.visible.side_effect=[True,True,False]
        w.poll()
        with self.assertRaises(WorkflowError):w.poll()
        self.assertEqual(w.bot.session.click.call_count,1)
