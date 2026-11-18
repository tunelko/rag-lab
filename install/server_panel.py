"""Interactive frontend for the same server controller used by scripts."""
import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, RichLog, Static

import setup_texts


class ServerPanel(App):
    BINDINGS = [
        Binding('r', 'poll', show=False),
        Binding('q,ctrl+q', 'leave', show=False, priority=True),
    ]
    CSS = """
    Screen { background: #11151d; color: #d8dee9; }
    #title { height: auto; margin: 1 2 0 2; color: #a8d6ff; text-style: bold; }
    #workspace { margin: 0 2; height: 1fr; }
    #panes { height: auto; }
    #summary, #actions { border: round #526480; height: auto; padding: 0 1; }
    #summary { width: 1fr; }
    #actions { width: 24; }
    #actions Button { width: 100%; margin-bottom: 0; }
    #state { text-style: bold; height: auto; }
    #state.running { color: #92d6a4; }
    #state.attention { color: #efbb85; }
    #details { height: auto; }
    #activity { height: 1fr; min-height: 4; border: round #394960; }
    #nav { height: auto; align-horizontal: right; margin: 0 2 1 2; }
    #nav Button { margin-left: 1; }
    .compact #panes { layout: vertical; }
    .compact #actions, .compact #summary { width: 100%; }
    """

    def __init__(self, root, port, host, language, python):
        super().__init__(ansi_color=False)
        self.root = root
        self.port = port
        self.host = host
        self.last_running_host = None
        self.language = language
        self.python = python
        self.status = {}
        self.operating = False
        self.checking = False
        self.controls_lock = asyncio.Lock()

    def label(self, key, **values):
        return setup_texts.text('panel_' + key, self.language, **values)

    def compose(self) -> ComposeResult:
        yield Static(self.label('title'), id='title')
        with VerticalScroll(id='workspace'):
            with Horizontal(id='panes'):
                with Vertical(id='summary'):
                    yield Static(self.label('unknown'), id='state', markup=False)
                    yield Static('', id='details', markup=False)
                with Vertical(id='actions'):
                    for action in ('start', 'stop', 'restart'):
                        yield Button(self.label(action), id=action, disabled=True)
            yield RichLog(id='activity', max_lines=100, wrap=True, markup=False)
        with Horizontal(id='nav'):
            yield Button(self.label('refresh') + ' (R)', id='refresh')
            yield Button(self.label('exit') + ' (Q)', id='exit')

    def on_mount(self):
        self.title = self.label('title')
        for pane, key in (('summary', 'state'), ('actions', 'actions'), ('activity', 'activity')):
            self.query_one('#' + pane).border_title = self.label(key)
        self.action_poll()
        self.set_interval(4, self.action_poll)

    def on_resize(self, event):
        self.screen.set_class(event.size.width < 60, 'compact')

    async def controller(self, action, *options):
        script = Path(__file__).resolve().parents[1] / 'app' / 'server_control.py'
        command = [self.python, str(script), action, '--root', str(self.root), '--port', str(self.port)]
        host = self.host or (self.last_running_host if action in ('start', 'status') else None)
        if host:
            command.extend(['--host', host])
        process = await asyncio.create_subprocess_exec(
            *command, *options, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        output = '\n'.join(part.decode('utf-8', 'replace').strip() for part in (stdout, stderr) if part.strip())
        if process.returncode:
            raise RuntimeError(output or f'{action} ({process.returncode})')
        return output

    def show_status(self):
        state = self.status.get('state', 'unknown')
        jobs = self.status.get('jobs')
        indicator = self.query_one('#state', Static)
        indicator.update(self.label(state))
        indicator.set_class(state == 'running', 'running')
        indicator.set_class(state in ('unknown', 'occupied', 'starting'), 'attention')
        lines = []
        if 'host' in self.status:
            lines.append(f"http://{self.status['host']}:{self.port}")
        if self.status.get('pid'):
            lines.append(f"PID {self.status['pid']}")
        if state == 'running':
            lines.append(f"{self.label('jobs')}: {jobs if jobs is not None else self.label('unknown')}")
            if jobs:
                lines.append(self.label('wait'))
        error = self.status.get('error') or self.status.get('queue_error')
        if error:
            lines.append(str(error))
        self.query_one('#details', Static).update('\n'.join(lines))
        self.query_one('#start', Button).disabled = self.operating or state != 'stopped'
        for action in ('stop', 'restart'):
            self.query_one('#' + action, Button).disabled = self.operating or state != 'running' or jobs != 0
        self.query_one('#exit', Button).disabled = self.operating

    async def read_status(self):
        try:
            status = json.loads(await self.controller('status', '--json'))
            if status.get('pid'):
                self.last_running_host = status['host']
        except (OSError, RuntimeError, ValueError) as error:
            status = {'error': str(error)}
        if self.status != status:
            self.status = status
            self.show_status()

    def action_poll(self):
        if not (self.checking or self.operating):
            self.checking = True
            self.poll_server()

    @work
    async def poll_server(self):
        try:
            async with self.controls_lock:
                await self.read_status()
        finally:
            self.checking = False

    def log_action(self, text):
        self.query_one('#activity', RichLog).write(f'{datetime.now():%H:%M:%S}  {text}')

    @work
    async def operate(self, action):
        async with self.controls_lock:
            try:
                self.log_action(self.label('working', action=self.label(action)))
                self.log_action(await self.controller(action))
            except (OSError, RuntimeError) as error:
                self.log_action(f"{self.label('error')}: {error}")
            finally:
                await self.read_status()
                self.operating = False
                self.show_status()

    @on(Button.Pressed)
    def pressed(self, event):
        action = event.button.id
        if action == 'refresh':
            self.action_poll()
        elif action == 'exit':
            self.action_leave()
        elif not self.operating and not event.button.disabled and action in ('start', 'stop', 'restart'):
            self.operating = True
            self.show_status()
            self.operate(action)

    def action_leave(self):
        if not self.operating:
            self.exit()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--host')
    parser.add_argument('--lang', choices=setup_texts.LANGUAGES, required=True)
    parser.add_argument('--python', required=True)
    args = parser.parse_args()
    ServerPanel(args.root, args.port, args.host, args.lang, args.python).run()
