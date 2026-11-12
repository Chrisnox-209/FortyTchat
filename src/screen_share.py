"""GUI coordination; capture starts only after an explicit, accepted invitation."""
from .features.screen_share import FEATURE
from .identity import session_id
from .ui.screen_share import ScreenWindow, ScreenPrompt


class ScreenShareController:
    def init_screen_sharing_ui(self):
        self.screen_window=None;self.screen_peer='';self.screen_prompt=None;self.screen_prompt_share=''
        self.screen_frame_finished.connect(self.screen_frame_done)
        self.screen_offer_finished.connect(self.screen_offer_done)
        self.screen_annotation_finished.connect(self.screen_annotation_done)
        self.tray.screen_stop_requested.connect(self.stop_screen_sharing)

    def screen_contact(self,sid):
        return self.window.peers.get(sid) or next((p for p in self.latest_peers if p['sid']==sid),None)

    def show_screen_sharing(self,sid):
        peer=self.screen_contact(sid)
        if not peer or FEATURE not in peer.get('features',()) or not peer.get('encrypted') or sid in self.window.sidebar.blocked:
            self.window.show_notice('Le partage d’écran nécessite deux clients compatibles 2.4.0 ou plus récents.');return None
        if self.screen_window:
            if self.screen_window.phase=='ready' and (not self.screen_window.outgoing or self.screen_peer!=sid):
                self.screen_window.close()
            else:
                self.screen_window.showNormal();self.screen_window.raise_();self.screen_window.activateWindow();return self.screen_window
        if self.screen_prompt:
            self.screen_prompt.raise_();return None
        return self.create_screen_window(sid,peer['nick'],True)

    def create_screen_window(self,sid,nick,outgoing):
        window=ScreenWindow(nick,self.icon_path,outgoing=outgoing)
        self.screen_window=window;self.screen_peer=sid
        window.requested.connect(self.begin_screen_sharing)
        window.stopped.connect(self.stop_screen_sharing)
        window.capture_failed.connect(self.stop_screen_sharing)
        window.encoded.connect(self.send_screen_frame)
        window.annotation_requested.connect(self.send_screen_annotation)
        def closed():
            if self.screen_window is window:
                if window.share:self.worker.stop_screen(window.share)
                self.screen_window=None;self.screen_peer='';self.tray.screen_stop_action.setVisible(False)
            window.deleteLater()
        window.closed.connect(closed);window.show();return window

    def begin_screen_sharing(self):
        window=self.screen_window
        if not window or not window.outgoing:return
        if self.screen_locked:
            window.finish('Déverrouille ta session avant de partager.');return
        sid=self.screen_peer;share=session_id();window.set_state(share,'waiting')
        future=self.worker.offer_screen(sid,share)
        def done(result):
            try:self.screen_offer_finished.emit(share,False if result.cancelled() or result.exception() else bool(result.result()))
            except RuntimeError:pass
        future.add_done_callback(done)

    def screen_offer_done(self,share,ok):
        window=self.screen_window
        if window and window.share==share and window.phase=='waiting' and not ok:
            window.finish('Invitation refusée ou contact indisponible.')

    def send_screen_frame(self,data,width,height):
        window=self.screen_window
        if not window or not window.share or window.phase!='active':return
        share=window.share;future=self.worker.screen_frame(share,data,width,height)
        def done(result):
            try:self.screen_frame_finished.emit(share)
            except RuntimeError:pass
        future.add_done_callback(done)

    def screen_frame_done(self,share):
        window=self.screen_window
        if window and window.share==share and window.source:window.source.released()

    def send_screen_annotation(self,payload):
        window=self.screen_window
        if not window or not window.share or window.phase!='active':return
        share=window.share;future=self.worker.screen_annotation(share,payload)
        def done(result):
            try:self.screen_annotation_finished.emit(share,False if result.cancelled() or result.exception() else bool(result.result()))
            except RuntimeError:pass
        future.add_done_callback(done)

    def screen_annotation_done(self,share,ok):
        window=self.screen_window
        if window and window.share==share:window.annotation_done(ok)

    def stop_screen_sharing(self,reason='Partage terminé.'):
        if self.screen_window:
            window=self.screen_window;share=window.share
            if window.source:window.source.stop()
            window.finish(reason)
            if share:self.worker.stop_screen(share,reason)
        elif self.screen_prompt:self.worker.stop_screen(self.screen_prompt_share,reason)
        self.tray.screen_stop_action.setVisible(False)

    def screen_event(self,kind,data):
        if kind=='screen_notice':
            self.window.show_notice(data['text']);return
        if kind=='screen_frame':
            window=self.screen_window
            if window and not window.outgoing and window.share==data['share']:
                if not window.receive_frame(data['data'],data['width'],data['height']):self.stop_screen_sharing('Image de partage invalide.')
            return
        if kind=='screen_annotation':
            window=self.screen_window
            if window and window.share==data['share'] and self.screen_peer==data['peer']:
                window.receive_annotation(data['payload'])
            return
        if kind!='screen_state':return
        phase,share,sid=data['phase'],data['share'],data['peer']
        peer=self.screen_contact(sid) or {'nick':'Un contact'}
        if phase=='offered':
            if self.screen_window and self.screen_window.phase=='ready':self.screen_window.close()
            if self.screen_window or self.screen_prompt:
                self.worker.answer_screen(share,False);return
            prompt=ScreenPrompt(peer['nick'],lambda accepted:self.worker.answer_screen(share,accepted),self.window)
            self.screen_prompt=prompt;self.screen_prompt_share=share
            def closed(result):
                if self.screen_prompt is prompt:self.screen_prompt=None;self.screen_prompt_share=''
                prompt.deleteLater()
            prompt.finished.connect(closed);prompt.show();prompt.raise_();prompt.activateWindow()
            self.notifications.show(peer['nick']+' propose un partage d’écran','Ouvre l’invitation pour accepter ou refuser.',urgency='normal')
        elif phase in ('waiting','active'):
            outgoing=data['direction']=='out'
            if outgoing and (not self.screen_window or not self.screen_window.outgoing or self.screen_window.share!=share):
                self.worker.stop_screen(share);return
            if not self.screen_window:
                self.create_screen_window(sid,peer['nick'],False)
            self.screen_peer=sid;self.screen_window.set_state(share,phase,data.get('annotations',False))
            self.tray.screen_stop_action.setVisible(phase=='active')
        elif phase=='closed':
            if self.screen_prompt and self.screen_prompt_share==share:self.screen_prompt.finish()
            if self.screen_window and self.screen_window.share==share:self.screen_window.finish(data['reason'])
            self.tray.screen_stop_action.setVisible(False)

    def close_screen_sharing_ui(self):
        if self.screen_prompt:self.screen_prompt.finish()
        if self.screen_window:self.screen_window.close()
